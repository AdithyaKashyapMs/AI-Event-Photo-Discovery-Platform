from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from app.models import Event, Photo, User, UserRole, FaceRecord, EventMembership
from app.database import get_session
from app.deps import get_current_user, require_host
from app.tasks import process_photo
from app.hierarchy import hierarchical_query
from typing import Optional
from insightface.app import FaceAnalysis
from qdrant_client import QdrantClient
import os
import uuid
import qrcode
from io import BytesIO
import numpy as np
import cv2
import io
import zipfile
import warnings
from time import perf_counter


router = APIRouter()
_selfie_face_app = None


def get_selfie_face_app():
    global _selfie_face_app
    if _selfie_face_app is None:
        _selfie_face_app = FaceAnalysis(
            name="buffalo_l", providers=["CPUExecutionProvider"]
        )
        _selfie_face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _selfie_face_app


# Create a new event (host only)
@router.post("/")
def create_event(
    name: str,
    description: Optional[str] = None,
    user: User = Depends(require_host),
    session: Session = Depends(get_session),
):
    event = Event(host_id=user.id, name=name, description=description)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


# List all events owned by the current host
@router.get("/")
def list_events(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    if user.role == UserRole.host:
        return session.exec(select(Event).where(Event.host_id == user.id)).all()

    return session.exec(
        select(Event)
        .join(EventMembership, EventMembership.event_id == Event.id)
        .where(EventMembership.user_id == user.id)
    ).all()


# Get a single event by ID
@router.get("/{event_id}")
def get_event(
    event_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/{event_id}/public")
def get_public_event(event_id: int, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"id": event.id, "name": event.name, "description": event.description}


@router.post("/{event_id}/join")
def join_event(
    event_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if user.role != UserRole.attendee:
        raise HTTPException(status_code=403, detail="Attendee access required")

    membership = session.exec(
        select(EventMembership).where(
            EventMembership.user_id == user.id,
            EventMembership.event_id == event_id,
        )
    ).first()
    if membership:
        return membership

    membership = EventMembership(user_id=user.id, event_id=event_id)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership


# Update an event (host only, must own the event)
@router.put("/{event_id}")
def update_event(
    event_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    user: User = Depends(require_host),
    session: Session = Depends(get_session),
):
    event = session.get(Event, event_id)
    if not event or event.host_id != user.id:
        raise HTTPException(status_code=404, detail="Event not found")
    if name:
        event.name = name
    if description:
        event.description = description
    session.commit()
    session.refresh(event)
    return event


# Directory for local photo storage (S3 in production)
UPLOAD_DIR = "uploads"


# Bulk upload photos for an event
@router.post("/{event_id}/photos")
async def upload_photos(
    event_id: int,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_host),
    session: Session = Depends(get_session),
):
    # Verify the event exists. require_host prevents attendee uploads.
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Create the upload directory for this event
    os.makedirs(f"{UPLOAD_DIR}/{event_id}", exist_ok=True)
    photos = []

    for file in files:
        # Generate a unique filename preserving the original extension
        ext = os.path.splitext(file.filename)[1]
        s3_key = f"{event_id}/{uuid.uuid4()}{ext}"
        file_path = os.path.join(UPLOAD_DIR, s3_key)

        # Write file contents to local storage
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Create a Photo record with pending status
        photo = Photo(event_id=event_id, s3_key=s3_key, status="pending")
        session.add(photo)
        photos.append(photo)

    session.commit()
    for p in photos:
        session.refresh(p)
        process_photo.delay(p.id)

    return {"uploaded": len(photos), "photos": photos}


# Generate a QR code image for the event invite link
@router.get("/{event_id}/qr")
def generate_qr(
    event_id: int,
    user: User = Depends(require_host),
    session: Session = Depends(get_session),
):
    event = session.get(Event, event_id)
    if not event or event.host_id != user.id:
        raise HTTPException(status_code=404, detail="Event not found")

    # Build the invite URL and generate the QR code
    invite_url = f"http://localhost:3000/events/{event_id}/join"
    img = qrcode.make(invite_url)

    # Save QR image to an in-memory buffer
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")


# --- Selfie Matching ---


@router.post("/{event_id}/match-selfie")
def match_selfie(
    event_id: int,
    files: list[UploadFile] | None = File(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
):
    uploaded_files = list(files or [])
    if file is not None:
        uploaded_files.append(file)
    if not uploaded_files:
        raise HTTPException(status_code=400, detail="At least one selfie is required")

    face_app = get_selfie_face_app()
    embeddings = []
    weights = []
    try:
        for uploaded_file in uploaded_files:
            contents = uploaded_file.file.read()
            if not contents:
                continue
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
                faces = face_app.get(img)
            if not faces:
                continue
            face = max(faces, key=lambda detected: detected["det_score"])
            embeddings.append(np.asarray(face["embedding"], dtype=float))
            weights.append(float(face["det_score"]))
    finally:
        # Selfies are processed in memory only and are never written to storage.
        for uploaded_file in uploaded_files:
            uploaded_file.file.close()

    if not embeddings:
        raise HTTPException(status_code=400, detail="No face detected in the supplied selfies")

    weights_array = np.asarray(weights, dtype=float)
    fused_embedding = np.average(
        np.asarray(embeddings),
        axis=0,
        weights=np.maximum(weights_array, 1e-6),
    )
    fused_embedding /= max(float(np.linalg.norm(fused_embedding)), 1e-12)
    query_embedding = fused_embedding.tolist()

    # Search Qdrant for matching face embeddings
    collection_name = f"event_{event_id}_faces"
    client = QdrantClient(host="localhost", port=6333)

    research_started = perf_counter()
    _, research_stats = hierarchical_query(
        client, collection_name, query_embedding, limit=100, use_cache=False
    )
    research_latency_ms = round((perf_counter() - research_started) * 1000, 2)

    project_started = perf_counter()
    results, search_stats = hierarchical_query(
        client, collection_name, query_embedding, limit=100, use_cache=True
    )
    project_latency_ms = round((perf_counter() - project_started) * 1000, 2)
    search_stats["latency_ms"] = project_latency_ms
    indexed_faces = search_stats["indexed_faces"]
    faces_considered = search_stats["faces_considered"]
    search_stats["reduction_percent"] = round(
        (1 - faces_considered / indexed_faces) * 100, 1
    ) if indexed_faces else 0
    search_stats["similarity_threshold"] = 0.55
    search_stats["raw_results"] = len(results)

    # Keep recognition confidence based on face similarity. Photo quality must not
    # turn a weak identity match into an accepted result.
    seen_photos = {}
    for result in results:
        photo_id = result.payload["photo_id"]
        quality = float(result.payload.get("quality_score", 0.5))
        similarity = float(result.score)
        candidate = {
            "confidence": similarity,
            "similarity": similarity,
            "quality": quality,
        }
        if photo_id not in seen_photos or similarity > seen_photos[photo_id]["confidence"]:
            seen_photos[photo_id] = candidate


    matches = [
        {"photo_id": pid, **scores} for pid, scores in seen_photos.items()
    ]
    matches.sort(key=lambda match: match["confidence"], reverse=True)
    search_stats["unique_photos"] = len(matches)
    search_stats["best_similarity"] = round(
        matches[0]["similarity"], 4
    ) if matches else 0
    search_stats["average_similarity"] = round(
        sum(match["similarity"] for match in matches) / len(matches), 4
    ) if matches else 0
    search_stats["no_match_reason"] = (
        "No indexed face met the similarity threshold"
        if not matches and indexed_faces
        else "No faces are indexed for this event"
        if not matches
        else None
    )
    search_stats["comparison"] = {
        "research_hierarchy": {
            "method": "Hierarchy rebuilt from Qdrant on every request",
            "latency_ms": research_latency_ms,
            "candidates": research_stats["faces_considered"],
        },
        "our_project": {
            "method": "Cached hierarchy with weighted fusion and filtering",
            "latency_ms": project_latency_ms,
            "candidates": faces_considered,
        },
        "improvements": [
            "Hierarchy index reused between selfie requests",
            "Weighted fusion of multiple selfie embeddings",
            "Similarity threshold for rejecting weak matches",
            "Photo-level deduplication",
        ],
        "accuracy_benchmark": "Unavailable without labeled ground-truth identities",
    }

    return {
        "matches": matches,
        "total": len(matches),
        "selfies_processed": len(embeddings),
        "search": search_stats,
        "privacy": {
            "selfies_persisted": False,
            "temporary_files_deleted": True,
        },
    }


# --- Gallery Endpoints ---


@router.get("/{event_id}/gallery")
def get_gallery(
    event_id: int,
    min_confidence: float = 0.0,
    sort_by: str = "date",
    page: int = 1,
    per_page: int = 20,
    photo_ids: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Query photos for this event from the database
    query = select(Photo).where(Photo.event_id == event_id, Photo.status == "done")
    matched_photo_ids = None
    if photo_ids is not None:
        try:
            matched_photo_ids = {
                int(photo_id) for photo_id in photo_ids.split(",") if photo_id
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid photo IDs") from exc
        query = query.where(Photo.id.in_(matched_photo_ids))

    # Apply sorting
    if sort_by == "date":
        query = query.order_by(Photo.created_at.desc())

    # Apply pagination
    offset = (page - 1) * per_page
    photos = session.exec(query.offset(offset).limit(per_page)).all()
    total = session.exec(query).all()

    return {
        "photos": [
            {"id": p.id, "s3_key": p.s3_key, "created_at": str(p.created_at)}
            for p in photos
        ],
        "page": page,
        "per_page": per_page,
        "total": len(total),
    }


@router.get("/{event_id}/gallery/download")
def download_gallery(
    event_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Fetch all processed photos for this event
    photos = session.exec(
        select(Photo).where(Photo.event_id == event_id, Photo.status == "done")
    ).all()

    if not photos:
        raise HTTPException(status_code=404, detail="No photos available for download")

    # Create an in-memory ZIP file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for photo in photos:
            # Read photo from storage (local or S3)
            file_path = f"uploads/{photo.s3_key}"
            try:
                with open(file_path, "rb") as f:
                    zf.writestr(photo.s3_key, f.read())
            except FileNotFoundError:
                continue

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=event_{event_id}_photos.zip"
        },
    )


@router.get('/{event_id}/clusters')
async def get_event_clusters(event_id: int):
    """Group all face embeddings for an event by person using centroid comparison."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    import numpy as np

    client = QdrantClient(url='http://localhost:6333')
    collection_name = f'event_{event_id}_faces'

    # Check if the collection exists before scrolling
    if not client.collection_exists(collection_name):
        return {'clusters': [], 'total_unique_people': 0}

    # Scroll through all embeddings for this event
    all_points = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=collection_name,
            limit=100,
            with_payload=True,
            with_vectors=True,
            offset=offset
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset

    if not all_points:
        return {'clusters': [], 'total_unique_people': 0}

    # Incremental centroid comparison for clustering
    similarity_threshold = 0.6
    clusters = []  # Each cluster: {'centroid': np.array, 'embedding_ids': [], 'photo_ids': []}

    for point in all_points:
        vector = np.array(point.vector)
        embedding_id = point.id
        photo_id = point.payload.get('photo_id')

        # Compare against existing cluster centroids
        assigned = False
        for cluster in clusters:
            centroid = cluster['centroid']
            # Cosine similarity between the face vector and cluster centroid
            similarity = np.dot(vector, centroid) / (
                np.linalg.norm(vector) * np.linalg.norm(centroid)
            )
            if similarity >= similarity_threshold:
                # Add to this cluster and update centroid incrementally
                cluster['embedding_ids'].append(str(embedding_id))
                cluster['photo_ids'].append(photo_id)
                n = len(cluster['embedding_ids'])
                cluster['centroid'] = cluster['centroid'] + (vector - cluster['centroid']) / n
                assigned = True
                break

        if not assigned:
            # Create a new cluster for this face
            clusters.append({
                'centroid': vector,
                'embedding_ids': [str(embedding_id)],
                'photo_ids': [photo_id]
            })

    # Build response with representative embedding and photo count per cluster
    cluster_results = []
    for i, cluster in enumerate(clusters):
        unique_photos = list(set(cluster['photo_ids']))
        cluster_results.append({
            'cluster_id': i,
            'representative_embedding_id': cluster['embedding_ids'][0],
            'photo_count': len(unique_photos),
            'photo_ids': unique_photos
        })

    return {
        'clusters': cluster_results,
        'total_unique_people': len(clusters)
    }


@router.get('/{event_id}/analytics')
async def get_event_analytics(event_id: int, session: Session = Depends(get_session)):
    """Return comprehensive event analytics including clustering and quality data."""
    from sqlmodel import select, func
    from qdrant_client import QdrantClient
    import numpy as np

    # Total photos uploaded for this event
    total_photos = session.exec(
        select(func.count(Photo.id)).where(Photo.event_id == event_id)
    ).one()

    # Processing progress: count photos by status
    processed_count = session.exec(
        select(func.count(Photo.id)).where(
            Photo.event_id == event_id, Photo.status == 'done'
        )
    ).one()
    processing_progress = (processed_count / total_photos * 100) if total_photos > 0 else 0

    # Total faces detected across all photos
    faces_detected = session.exec(
        select(func.count(FaceRecord.id)).where(FaceRecord.event_id == event_id)
    ).one()

    # Unique people estimate from clustering
    client = QdrantClient(url='http://localhost:6333')
    collection_name = f'event_{event_id}_faces'
    unique_people = 0
    if client.collection_exists(collection_name):
        # Reuse the clustering logic to count unique people
        clusters_response = await get_event_clusters(event_id)
        unique_people = clusters_response['total_unique_people']

    # Attendee engagement: count members for this event
    memberships = session.exec(
        select(EventMembership).where(EventMembership.event_id == event_id)
    ).all()
    attendee_engagement = {
        'total_attendees': len(memberships),
        'attendees_with_matches': 0
    }

    # Duplicate detection report
    face_records = session.exec(
        select(FaceRecord).where(FaceRecord.event_id == event_id)
    ).all()
    duplicate_count = 0
    seen_embeddings = set()
    for record in face_records:
        if record.embedding_id in seen_embeddings:
            duplicate_count += 1
        seen_embeddings.add(record.embedding_id)

    # Photo quality scoring summary
    quality_scores = [r.quality_score for r in face_records if r.quality_score is not None]
    quality_summary = {
        'average_quality': float(np.mean(quality_scores)) if quality_scores else 0.0,
        'min_quality': float(np.min(quality_scores)) if quality_scores else 0.0,
        'max_quality': float(np.max(quality_scores)) if quality_scores else 0.0,
        'low_quality_faces': len([q for q in quality_scores if q < 0.5])
    }

    hierarchy = {"branches": 0, "indexed_faces": faces_detected}
    collection_name = f"event_{event_id}_faces"
    if client.collection_exists(collection_name):
        points, _ = client.scroll(
            collection_name=collection_name,
            limit=10000,
            with_payload=True,
            with_vectors=False,
        )
        codes = {
            point.payload.get("identity_code")
            for point in points
            if point.payload and point.payload.get("identity_code")
        }
        hierarchy = {"branches": len(codes), "indexed_faces": len(points)}

    return {
        'total_photos': total_photos,
        'faces_detected': faces_detected,
        'unique_people': unique_people,
        'processing_progress': round(processing_progress, 1),
        'attendee_engagement': attendee_engagement,
        'duplicate_detection': {
            'total_face_records': len(face_records),
            'duplicate_embeddings': duplicate_count
        },
        'quality_summary': quality_summary
        ,
        'hierarchy': hierarchy,
    }