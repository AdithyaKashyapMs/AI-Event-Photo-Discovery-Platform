import io
import os
import tempfile
import threading
import uuid
import warnings
import zipfile
from time import perf_counter
from typing import Optional

import cv2
import numpy as np
import qrcode
import redis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from insightface.app import FaceAnalysis
from PIL import Image
from sqlmodel import Session, delete, func, select

from app.access import (
    is_event_host,
    require_event_access,
    require_event_host,
    visible_photo_ids,
)
from app.config import (
    FRONTEND_URL,
    THUMBNAIL_DIR,
    UPLOAD_DIR,
    face_collection_name,
    get_qdrant_client,
)
from app import ratelimit
from app.database import get_session
from app.deps import get_current_user, require_host
from app.hierarchy import (
    CANDIDATE_SIMILARITY_FLOOR,
    MATCH_SIMILARITY_THRESHOLD,
    hierarchical_query,
    forget_cached_hierarchy,
    scroll_all_points,
)
from app.liveness import (
    LivenessError,
    check_frame,
    consume_challenge,
    create_challenge,
    liveness_required,
    verify_frames,
)
from app.models import (
    new_invite_token,
    Event,
    EventMembership,
    FaceLabel,
    FaceRecord,
    Photo,
    PhotoMatch,
    User,
    UserRole,
)
from app.tasks import process_photo

router = APIRouter()

MAX_EVENT_NAME_LENGTH = 200
MAX_EVENT_DESCRIPTION_LENGTH = 2000

# Uploads: stored under the format Pillow detects, not the client's file name.
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
UPLOAD_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}

MAX_SELFIE_FILES = 6
MAX_SELFIE_BYTES = 8 * 1024 * 1024

# Faces whose similarity to a cluster centroid reaches this join that cluster.
CLUSTER_SIMILARITY_THRESHOLD = 0.6

_selfie_face_app = None

# Face recognition on selfies is CPU-heavy. FastAPI runs these requests in a
# thread pool, so without a cap a burst of selfies would all run at once and
# slow every request down. Extra requests wait briefly, then get a 503.
SELFIE_MAX_CONCURRENT = max(1, int(os.getenv("SELFIE_MAX_CONCURRENT", "2")))
SELFIE_QUEUE_WAIT_SECONDS = float(os.getenv("SELFIE_QUEUE_WAIT_SECONDS", "20"))
_selfie_slots = threading.BoundedSemaphore(SELFIE_MAX_CONCURRENT)


def get_selfie_face_app():
    global _selfie_face_app
    if _selfie_face_app is None:
        # "observe" mode scores liveness on every face but still computes the
        # embedding; app.liveness decides whether the selfie is accepted.
        _selfie_face_app = FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"],
            addons=["liveness"],
            liveness_mode="observe",
        )
        _selfie_face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _selfie_face_app


def fuse_selfie_embeddings(
    embeddings: list[np.ndarray],
    detection_scores: list[float],
) -> tuple[np.ndarray, float]:
    """Build a robust normalized template from one or more selfie embeddings."""
    normalized = np.asarray(embeddings, dtype=float)
    normalized /= np.maximum(
        np.linalg.norm(normalized, axis=1, keepdims=True),
        1e-12,
    )
    base_weights = np.maximum(np.asarray(detection_scores, dtype=float), 1e-6)

    if len(normalized) == 1:
        return normalized[0], 1.0

    similarities = normalized @ normalized.T
    np.fill_diagonal(similarities, 0.0)
    agreement = similarities.sum(axis=1) / (len(normalized) - 1)
    # Keep every usable view, but reduce the influence of an inconsistent view.
    consistency_weights = np.clip((agreement + 1.0) / 2.0, 0.1, 1.0)
    weights = base_weights * consistency_weights
    fused = np.average(normalized, axis=0, weights=weights)
    fused /= max(float(np.linalg.norm(fused)), 1e-12)
    return fused, float(np.average(agreement, weights=weights))


def parse_photo_ids(photo_ids: Optional[str]) -> Optional[set[int]]:
    """Parse a comma-separated photo ID list from a query parameter."""
    if photo_ids is None:
        return None
    try:
        return {int(photo_id) for photo_id in photo_ids.split(",") if photo_id.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid photo IDs") from exc


def clean_event_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Event name is required")
    if len(name) > MAX_EVENT_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Event name is too long")
    return name


def clean_event_description(description: Optional[str]) -> Optional[str]:
    if description is None:
        return None
    description = description.strip()
    if len(description) > MAX_EVENT_DESCRIPTION_LENGTH:
        raise HTTPException(status_code=400, detail="Event description is too long")
    return description or None


# --- Events ---


# Create a new event (host only)
@router.post("/")
def create_event(
    name: str,
    description: Optional[str] = None,
    user: User = Depends(require_host),
    session: Session = Depends(get_session),
):
    event = Event(
        host_id=user.id,
        name=clean_event_name(name),
        description=clean_event_description(description),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event_payload(event, user)


def event_payload(event: Event, user: User) -> dict:
    """Event fields for the API. Only the host receives the invite token."""
    host = is_event_host(event, user)
    data = event.model_dump(exclude={"invite_token"})
    data["is_host"] = host
    if host:
        data["invite_token"] = event.invite_token
    return data


# Hosts see the events they own; attendees see the events they joined.
@router.get("/")
def list_events(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    if user.role == UserRole.host:
        query = select(Event).where(Event.host_id == user.id)
    else:
        query = (
            select(Event)
            .join(EventMembership, EventMembership.event_id == Event.id)
            .where(EventMembership.user_id == user.id)
        )
    events = session.exec(query.order_by(Event.created_at.desc())).all()
    return [event_payload(event, user) for event in events]


@router.get("/{event_id}")
def get_event(
    user: User = Depends(get_current_user),
    event: Event = Depends(require_event_access),
):
    return event_payload(event, user)


# Replace the invite token; links and QR codes already shared stop working.
# Attendees who already joined keep their access.
@router.post("/{event_id}/invite/reset")
def reset_invite(
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    event.invite_token = new_invite_token()
    session.add(event)
    session.commit()
    session.refresh(event)
    return {"invite_token": event.invite_token}


# Update an event (host only, must own the event)
@router.put("/{event_id}")
def update_event(
    name: Optional[str] = None,
    description: Optional[str] = None,
    user: User = Depends(require_host),
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    if name is not None:
        event.name = clean_event_name(name)
    if description is not None:
        # An empty description clears it.
        event.description = clean_event_description(description)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event_payload(event, user)


# Delete an event and everything derived from it (host only): photos and
# thumbnails, the face embeddings of everyone in those photos, attendees'
# saved matches and memberships.
@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    photos = session.exec(select(Photo).where(Photo.event_id == event_id)).all()

    client = get_qdrant_client()
    collection_name = face_collection_name(event_id)
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)
    forget_cached_hierarchy(collection_name)

    for model in (PhotoMatch, FaceLabel, FaceRecord, EventMembership):
        session.execute(delete(model).where(model.event_id == event_id))
    session.execute(delete(Photo).where(Photo.event_id == event_id))
    session.delete(event)
    session.commit()

    # Files last: if this fails, the database no longer references them.
    for photo in photos:
        (UPLOAD_DIR / photo.s3_key).unlink(missing_ok=True)
        (THUMBNAIL_DIR / f"{photo.id}.jpg").unlink(missing_ok=True)
    return {"deleted": True, "photos_deleted": len(photos)}


# --- Photo upload and processing ---


def _store_upload(event_id: int, upload: UploadFile) -> str:
    """Save one uploaded image and return its storage key. Raises ValueError."""
    event_dir = UPLOAD_DIR / str(event_id)
    event_dir.mkdir(parents=True, exist_ok=True)
    temporary = event_dir / f".upload-{uuid.uuid4()}"
    try:
        size = 0
        with temporary.open("wb") as destination:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError(
                        f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                    )
                destination.write(chunk)
        if size == 0:
            raise ValueError("File is empty")

        # Check the content, not the file name: it must be a readable image.
        try:
            with Image.open(temporary) as image:
                image_format = image.format
                image.verify()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Not a supported image file") from exc
        extension = UPLOAD_FORMATS.get(image_format or "")
        if extension is None:
            raise ValueError(
                "Unsupported image format. Use JPEG, PNG, WebP, BMP or TIFF."
            )

        s3_key = f"{event_id}/{uuid.uuid4()}{extension}"
        temporary.replace(UPLOAD_DIR / s3_key)
        return s3_key
    finally:
        temporary.unlink(missing_ok=True)


# Bulk upload photos for an event (host only)
@router.post("/{event_id}/photos")
def upload_photos(
    event_id: int,
    files: list[UploadFile] = File(...),
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    photos = []
    rejected = []
    for upload in files:
        try:
            s3_key = _store_upload(event_id, upload)
        except ValueError as exc:
            rejected.append({"filename": upload.filename, "reason": str(exc)})
            continue
        finally:
            upload.file.close()
        photo = Photo(event_id=event_id, s3_key=s3_key, status="pending")
        session.add(photo)
        photos.append(photo)

    if not photos:
        detail = rejected[0]["reason"] if len(rejected) == 1 else "No valid images were uploaded"
        raise HTTPException(status_code=400, detail=detail)

    session.commit()
    for photo in photos:
        session.refresh(photo)
        process_photo.delay(photo.id)

    return {
        "uploaded": len(photos),
        "photos": [{"id": photo.id, "status": photo.status} for photo in photos],
        "rejected": rejected,
    }


# Lightweight processing counts, safe to poll every few seconds.
@router.get("/{event_id}/processing-status")
def get_processing_status(
    event_id: int,
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(Photo.status, func.count(Photo.id))
        .where(Photo.event_id == event_id)
        .group_by(Photo.status)
    ).all()
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    finished = sum(counts.get(status, 0) for status in ("done", "duplicate", "failed"))
    return {
        "total": total,
        "processed": finished,
        "done": counts.get("done", 0),
        "duplicate": counts.get("duplicate", 0),
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0) + counts.get("processing", 0),
        "progress": round(finished / total * 100, 1) if total else 0,
    }


# QR code image for the event invite link (host only)
@router.get("/{event_id}/qr")
def generate_qr(event: Event = Depends(require_event_host)):
    img = qrcode.make(f"{FRONTEND_URL}/join/{event.invite_token}")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


# --- Selfie matching ---


@router.post("/{event_id}/liveness/challenge")
def start_liveness_challenge(
    event_id: int,
    current_user: User = Depends(get_current_user),
    event: Event = Depends(require_event_access),
):
    """Issue a random, single-use head-turn challenge for the selfie camera."""
    ratelimit.hit(ratelimit.LIVENESS_CHALLENGE_PER_USER, str(current_user.id))
    try:
        return create_challenge(current_user.id, event_id)
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503, detail="Liveness service is unavailable. Please try again."
        ) from exc


def _read_selfie_faces(face_app, uploaded_file: UploadFile) -> list:
    contents = uploaded_file.file.read(MAX_SELFIE_BYTES + 1)
    if len(contents) > MAX_SELFIE_BYTES:
        raise HTTPException(status_code=413, detail="Selfie image is too large")
    if not contents:
        return []
    np_arr = np.frombuffer(contents, np.uint8)
    try:
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise HTTPException(status_code=400, detail="Invalid selfie image") from exc
    if img is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            message='`estimate` is deprecated since version 0.26.*',
            category=FutureWarning,
            module=r'insightface\.utils\.face_align',
        )
        return face_app.get(img)


def _save_matches(
    session: Session, user_id: int, event_id: int, matches: list[dict]
) -> None:
    """Replace the user's saved matches for this event with the new search."""
    session.execute(
        delete(PhotoMatch).where(
            PhotoMatch.user_id == user_id, PhotoMatch.event_id == event_id
        )
    )
    existing_photo_ids = set(
        session.exec(
            select(Photo.id).where(
                Photo.event_id == event_id,
                Photo.id.in_([match["photo_id"] for match in matches]),
            )
        ).all()
    ) if matches else set()
    for match in matches:
        if match["photo_id"] in existing_photo_ids:
            session.add(
                PhotoMatch(
                    user_id=user_id,
                    event_id=event_id,
                    photo_id=match["photo_id"],
                    similarity=match["similarity"],
                )
            )
    session.commit()


@router.post("/{event_id}/match-selfie")
def match_selfie(
    event_id: int,
    files: list[UploadFile] | None = File(None),
    file: UploadFile | None = File(None),
    challenge_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    event: Event = Depends(require_event_access),
    session: Session = Depends(get_session),
):
    uploaded_files = list(files or [])
    if file is not None:
        uploaded_files.append(file)
    try:
        ratelimit.hit(ratelimit.SELFIE_MATCH_PER_USER, str(current_user.id))
        if not uploaded_files:
            raise HTTPException(status_code=400, detail="At least one selfie is required")
        if len(uploaded_files) > MAX_SELFIE_FILES:
            raise HTTPException(status_code=400, detail="Too many selfie images")
        if challenge_id is None and liveness_required():
            raise HTTPException(
                status_code=400,
                detail="A live camera check is required to search for photos.",
            )

        # Consume the challenge before any heavy work, so a failed attempt
        # cannot be retried with the same challenge.
        steps = None
        if challenge_id is not None:
            try:
                steps = consume_challenge(challenge_id, current_user.id, event_id)
            except LivenessError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except redis.RedisError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Liveness service is unavailable. Please try again.",
                ) from exc
            if len(uploaded_files) != len(steps):
                raise HTTPException(
                    status_code=422,
                    detail="The liveness check is incomplete. Please try again.",
                )

        if not _selfie_slots.acquire(timeout=SELFIE_QUEUE_WAIT_SECONDS):
            raise HTTPException(
                status_code=503,
                detail="Lots of people are searching right now. Please try again in a moment.",
                headers={"Retry-After": "10"},
            )
        try:
            face_app = get_selfie_face_app()
            faces_per_file = [
                _read_selfie_faces(face_app, uploaded_file) for uploaded_file in uploaded_files
            ]
        finally:
            _selfie_slots.release()
    finally:
        # Selfie frames are processed in memory only and are never written to
        # storage; their embeddings are discarded after this request.
        for uploaded_file in uploaded_files:
            uploaded_file.file.close()

    embeddings = []
    weights = []
    if steps is not None:
        try:
            frame_checks = [
                check_frame(faces, step, number)
                for number, (faces, step) in enumerate(zip(faces_per_file, steps), start=1)
            ]
            liveness_result = verify_frames(frame_checks)
        except LivenessError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Every verified frame is used: the straight-on and turned views are
        # fused into one template, which matches more poses in event photos.
        for frame in frame_checks:
            embeddings.append(frame.embedding)
            weights.append(frame.det_score)
    else:
        liveness_result = {
            "passed": False,
            "method": "not performed (LIVENESS_REQUIRED is disabled)",
        }
        for faces in faces_per_file:
            faces = [face for face in faces if getattr(face, "embedding", None) is not None]
            if not faces:
                continue
            face = max(faces, key=lambda detected: detected["det_score"])
            embeddings.append(np.asarray(face["embedding"], dtype=float))
            weights.append(float(face["det_score"]))

    if not embeddings:
        raise HTTPException(status_code=400, detail="No face detected in the supplied selfies")

    fused_embedding, fusion_agreement = fuse_selfie_embeddings(embeddings, weights)
    query_embedding = fused_embedding.tolist()

    # Search the event's indexed faces. Benchmarks comparing search strategies
    # live in backend/scripts/benchmark_search.py, not in user requests.
    collection_name = face_collection_name(event_id)
    client = get_qdrant_client()
    started = perf_counter()
    if client.collection_exists(collection_name):
        results, search_stats = hierarchical_query(
            client, collection_name, query_embedding, limit=500, use_cache=True
        )
        indexed_faces = search_stats["indexed_faces"]
    else:
        results, indexed_faces = [], 0
    latency_ms = round((perf_counter() - started) * 1000, 1)

    # Hosts can see every photo anyway, so they may explore candidates down to
    # the floor. Attendees only get photos at or above the match threshold, so
    # a weak match cannot expose someone else's photos.
    host = is_event_host(event, current_user)
    access_floor = CANDIDATE_SIMILARITY_FLOOR if host else MATCH_SIMILARITY_THRESHOLD

    # Best face similarity per photo. Photo quality is not used, so a sharp
    # photo cannot turn a weak identity match into an accepted result.
    best_by_photo: dict[int, float] = {}
    for result in results:
        photo_id = result.payload["photo_id"]
        best_by_photo[photo_id] = max(best_by_photo.get(photo_id, 0.0), float(result.score))
    candidates = sorted(
        ({"photo_id": photo_id, "similarity": score} for photo_id, score in best_by_photo.items()),
        key=lambda match: match["similarity"],
        reverse=True,
    )
    accepted_count = sum(
        1 for match in candidates if match["similarity"] >= MATCH_SIMILARITY_THRESHOLD
    )
    visible_matches = [match for match in candidates if match["similarity"] >= access_floor]
    _save_matches(session, current_user.id, event_id, visible_matches)

    if accepted_count:
        no_match_reason = None
    elif indexed_faces:
        no_match_reason = "None of the faces in this event's photos matched yours closely enough."
    else:
        no_match_reason = "No photos have been processed for this event yet. Try again later."

    # "matches" holds the photos this user may now view (saved server-side);
    # "total" counts those at or above the default match threshold.
    return {
        "matches": visible_matches,
        "total": accepted_count,
        "frames_used": len(embeddings),
        "liveness": liveness_result,
        "search": {
            "indexed_faces": indexed_faces,
            "latency_ms": latency_ms,
            "similarity_threshold": MATCH_SIMILARITY_THRESHOLD,
            "candidate_floor": access_floor,
            "fusion_agreement": round(fusion_agreement, 4),
            "no_match_reason": no_match_reason,
        },
        "privacy": {
            "selfie_frames_stored": False,
            "selfie_embedding_stored": False,
            "stored": "the IDs and similarity scores of the photos you matched",
        },
    }


# --- Gallery ---


def _photo_payload(photo: Photo, similarity: Optional[float]) -> dict:
    return {
        "id": photo.id,
        "url": f"/photos/{photo.id}/file",
        "thumbnail_url": f"/photos/{photo.id}/file?variant=thumb",
        "similarity": similarity,
        "created_at": photo.created_at.isoformat(),
    }


@router.get("/{event_id}/gallery")
def get_gallery(
    event_id: int,
    matched_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    event: Event = Depends(require_event_access),
    session: Session = Depends(get_session),
):
    """Attendees see only their matched photos; hosts see the whole event.

    A host can pass matched_only=true to see the results of their own selfie
    search instead of every photo.
    """
    host = is_event_host(event, current_user)
    offset = (page - 1) * per_page

    if host and not matched_only:
        base = select(Photo).where(Photo.event_id == event_id, Photo.status == "done")
        total = session.exec(
            select(func.count()).select_from(base.subquery())
        ).one()
        photos = session.exec(
            base.order_by(Photo.created_at.desc(), Photo.id.desc())
            .offset(offset)
            .limit(per_page)
        ).all()
        scores = {
            photo_id: similarity
            for photo_id, similarity in session.exec(
                select(PhotoMatch.photo_id, PhotoMatch.similarity).where(
                    PhotoMatch.user_id == current_user.id,
                    PhotoMatch.event_id == event_id,
                )
            ).all()
        }
        items = [_photo_payload(photo, scores.get(photo.id)) for photo in photos]
        scope = "all"
    else:
        base = (
            select(Photo, PhotoMatch.similarity)
            .join(PhotoMatch, PhotoMatch.photo_id == Photo.id)
            .where(
                PhotoMatch.user_id == current_user.id,
                PhotoMatch.event_id == event_id,
                Photo.status == "done",
            )
        )
        total = session.exec(
            select(func.count()).select_from(base.subquery())
        ).one()
        rows = session.exec(
            base.order_by(PhotoMatch.similarity.desc(), Photo.id)
            .offset(offset)
            .limit(per_page)
        ).all()
        items = [_photo_payload(photo, similarity) for photo, similarity in rows]
        scope = "matched"

    return {
        "photos": items,
        "scope": scope,
        "is_host": host,
        "page": page,
        "per_page": per_page,
        "total": total,
        "similarity_threshold": MATCH_SIMILARITY_THRESHOLD,
    }


@router.get("/{event_id}/gallery/download")
def download_gallery(
    event_id: int,
    photo_ids: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    event: Event = Depends(require_event_access),
    session: Session = Depends(get_session),
):
    """ZIP of the requested photos, limited to the photos this user may see."""
    query = select(Photo).where(Photo.event_id == event_id, Photo.status == "done")
    requested_ids = parse_photo_ids(photo_ids)
    if requested_ids is not None:
        if not requested_ids:
            raise HTTPException(status_code=400, detail="No photos selected for download")
        query = query.where(Photo.id.in_(requested_ids))
    allowed_ids = visible_photo_ids(session, event, current_user)
    if allowed_ids is not None:
        query = query.where(Photo.id.in_(allowed_ids))
    photos = session.exec(query.order_by(Photo.id)).all()
    if not photos:
        raise HTTPException(status_code=404, detail="No photos available for download")

    # Build the archive in a temporary file (spilling to disk when large)
    # rather than holding hundreds of photos in memory. JPEGs are already
    # compressed, so they are stored without recompression.
    archive = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for photo in photos:
            source = (UPLOAD_DIR / photo.s3_key).resolve()
            if source.is_relative_to(UPLOAD_DIR) and source.is_file():
                zf.write(source, arcname=f"photo_{photo.id}{source.suffix}")
    archive.seek(0)

    def stream():
        try:
            while chunk := archive.read(1024 * 1024):
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="event_{event_id}_photos.zip"'
        },
    )


# --- Host analytics ---


def _cluster_faces(points: list) -> list[dict]:
    """Group faces by person with incremental centroid comparison."""
    clusters: list[dict] = []
    for point in points:
        vector = np.asarray(point.vector, dtype=float)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        photo_id = point.payload.get("photo_id") if point.payload else None
        for cluster in clusters:
            centroid = cluster["centroid"]
            similarity = float(vector @ centroid) / max(float(np.linalg.norm(centroid)), 1e-12)
            if similarity >= CLUSTER_SIMILARITY_THRESHOLD:
                cluster["embedding_ids"].append(str(point.id))
                cluster["photo_ids"].add(photo_id)
                count = len(cluster["embedding_ids"])
                cluster["centroid"] = centroid + (vector - centroid) / count
                break
        else:
            clusters.append({
                "centroid": vector,
                "embedding_ids": [str(point.id)],
                "photo_ids": {photo_id},
            })
    return clusters


def _event_clusters(event_id: int) -> list[dict]:
    client = get_qdrant_client()
    collection_name = face_collection_name(event_id)
    if not client.collection_exists(collection_name):
        return []
    return _cluster_faces(scroll_all_points(client, collection_name))


@router.get('/{event_id}/clusters')
def get_event_clusters(
    event_id: int,
    event: Event = Depends(require_event_host),
):
    """Group all face embeddings for an event by person (host only)."""
    clusters = _event_clusters(event_id)
    return {
        'clusters': [
            {
                'cluster_id': index,
                'representative_embedding_id': cluster['embedding_ids'][0],
                'photo_count': len(cluster['photo_ids']),
                'photo_ids': sorted(pid for pid in cluster['photo_ids'] if pid is not None),
            }
            for index, cluster in enumerate(clusters)
        ],
        'total_unique_people': len(clusters),
    }


@router.get('/{event_id}/analytics')
def get_event_analytics(
    event_id: int,
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    """Event analytics for the host: processing, faces, people and quality."""
    status = get_processing_status(event_id, event, session)

    faces_detected, average_quality, min_quality, max_quality = session.exec(
        select(
            func.count(FaceRecord.id),
            func.avg(FaceRecord.quality_score),
            func.min(FaceRecord.quality_score),
            func.max(FaceRecord.quality_score),
        ).where(FaceRecord.event_id == event_id)
    ).one()
    low_quality_faces = session.exec(
        select(func.count(FaceRecord.id)).where(
            FaceRecord.event_id == event_id, FaceRecord.quality_score < 0.5
        )
    ).one()

    total_attendees = session.exec(
        select(func.count(EventMembership.id)).where(EventMembership.event_id == event_id)
    ).one()
    attendees_with_matches = session.exec(
        select(func.count(func.distinct(PhotoMatch.user_id))).where(
            PhotoMatch.event_id == event_id,
            PhotoMatch.user_id != event.host_id,
        )
    ).one()

    client = get_qdrant_client()
    collection_name = face_collection_name(event_id)
    hierarchy = {"branches": 0, "indexed_faces": 0}
    unique_people = 0
    if client.collection_exists(collection_name):
        points = scroll_all_points(client, collection_name)
        codes = {
            point.payload.get("identity_code")
            for point in points
            if point.payload and point.payload.get("identity_code")
        }
        hierarchy = {"branches": len(codes), "indexed_faces": len(points)}
        unique_people = len(_cluster_faces(points))

    return {
        'total_photos': status['total'],
        'faces_detected': faces_detected,
        'unique_people': unique_people,
        'processing_progress': status['progress'],
        'processing': status,
        'attendee_engagement': {
            'total_attendees': total_attendees,
            'attendees_with_matches': attendees_with_matches,
        },
        'duplicate_detection': {
            'method': 'perceptual hash (pHash) per event',
            'duplicate_photos': status['duplicate'],
            'total_face_records': faces_detected,
        },
        'failed_photos': status['failed'],
        'quality_summary': {
            'average_quality': float(average_quality or 0.0),
            'min_quality': float(min_quality or 0.0),
            'max_quality': float(max_quality or 0.0),
            'low_quality_faces': low_quality_faces,
        },
        'hierarchy': hierarchy,
    }
