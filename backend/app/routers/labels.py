"""Labelling tool (host only): record who each detected face really is.

The labels are ground truth for scripts/evaluate_matching.py, which measures
how many of a person's photos the matcher finds (recall) and how many of the
photos it returns are really them (precision). Labels never change what
attendees can see.

Workflow: the app proposes groups of faces that look alike (the same greedy
clustering as /events/{id}/clusters). The host names a group and unticks
faces that are someone else. Because the proposals come from the model, the
faces it finds hardest (profiles, blur, small background faces) may be left
out of a group; "suggestions" lists the remaining unlabelled faces closest to
a labelled person, down to a low similarity, so those hard faces get labelled
too. Without that step the ground truth would only contain easy faces and
recall would look better than it is.
"""

from __future__ import annotations

import json
from collections import defaultdict

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, field_validator
from sqlmodel import Session, delete, select

from app.access import require_event_host
from app.config import UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import get_session
from app.hierarchy import scroll_all_points
from app.models import NOT_A_FACE, Event, FaceLabel, FaceRecord, Photo
from app.routers.events import _cluster_faces

router = APIRouter()

CROP_SIZE = 160
CROP_MARGIN = 0.35
SUGGESTION_FLOOR = 0.15
MAX_SUGGESTIONS = 60
MAX_PERSON_NAME = 60


def _face_vectors(event_id: int) -> dict[str, np.ndarray]:
    """Normalised embedding per Qdrant point ID (FaceRecord.embedding_id)."""
    client = get_qdrant_client()
    name = face_collection_name(event_id)
    if not client.collection_exists(name):
        return {}
    vectors = {}
    for point in scroll_all_points(client, name):
        vector = np.asarray(point.vector, dtype=float)
        vectors[str(point.id)] = vector / max(float(np.linalg.norm(vector)), 1e-12)
    return vectors


def _faces(session: Session, event_id: int) -> list[FaceRecord]:
    return session.exec(select(FaceRecord).where(FaceRecord.event_id == event_id).order_by(FaceRecord.id)).all()


def _labels(session: Session, event_id: int) -> dict[int, str]:
    rows = session.exec(select(FaceLabel).where(FaceLabel.event_id == event_id)).all()
    return {row.face_id: row.person for row in rows}


def _face_payload(face: FaceRecord, label: str | None, similarity: float | None = None) -> dict:
    box = json.loads(face.bbox)
    return {
        "face_id": face.id,
        "photo_id": face.photo_id,
        "quality": round(face.quality_score, 3),
        "width": int(box[2] - box[0]) if len(box) == 4 else None,
        "person": label,
        "similarity": None if similarity is None else round(similarity, 3),
        "crop_url": f"/events/{face.event_id}/faces/{face.id}/crop",
    }


@router.get("/{event_id}/labelling")
def labelling_overview(
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    """Look-alike groups, unlabelled faces first, plus a summary of labels."""
    faces = _faces(session, event.id)
    labels = _labels(session, event.id)
    by_embedding = {face.embedding_id: face for face in faces}
    vectors = _face_vectors(event.id)

    class _Point:  # the shape _cluster_faces expects
        def __init__(self, point_id, vector, photo_id):
            self.id, self.vector, self.payload = point_id, vector, {"photo_id": photo_id}

    points = [_Point(eid, vectors[eid], face.photo_id) for eid, face in by_embedding.items() if eid in vectors]
    groups = []
    for cluster in _cluster_faces(points):
        members = [by_embedding[eid] for eid in cluster["embedding_ids"]]
        unlabelled = [face for face in members if face.id not in labels]
        groups.append(
            {
                "faces": [_face_payload(face, labels.get(face.id)) for face in members],
                "unlabelled": len(unlabelled),
            }
        )
    # Biggest unfinished groups first: they label the most faces per click.
    groups.sort(key=lambda group: (group["unlabelled"] == 0, -group["unlabelled"]))

    people: dict[str, dict] = defaultdict(lambda: {"faces": 0, "photos": set()})
    face_by_id = {face.id: face for face in faces}
    for face_id, person in labels.items():
        if person != NOT_A_FACE and face_id in face_by_id:
            people[person]["faces"] += 1
            people[person]["photos"].add(face_by_id[face_id].photo_id)
    return {
        "total_faces": len(faces),
        "labelled_faces": len(labels),
        "people": sorted(
            ({"person": name, "faces": v["faces"], "photos": len(v["photos"])} for name, v in people.items()),
            key=lambda row: -row["faces"],
        ),
        "groups": groups,
    }


@router.get("/{event_id}/labelling/suggestions")
def labelling_suggestions(
    person: str = Query(..., min_length=1, max_length=MAX_PERSON_NAME),
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    """Unlabelled faces most similar to a labelled person, hardest included."""
    faces = _faces(session, event.id)
    labels = _labels(session, event.id)
    vectors = _face_vectors(event.id)
    labelled = [vectors[f.embedding_id] for f in faces if labels.get(f.id) == person and f.embedding_id in vectors]
    if not labelled:
        raise HTTPException(status_code=404, detail="No faces are labelled with this name yet")
    # Compare against every labelled view (best match), so a profile face can
    # be suggested by the person's other profile faces.
    references = np.asarray(labelled)
    scored = []
    for face in faces:
        if face.id in labels or face.embedding_id not in vectors:
            continue
        similarity = float(np.max(references @ vectors[face.embedding_id]))
        if similarity >= SUGGESTION_FLOOR:
            scored.append((similarity, face))
    scored.sort(key=lambda item: -item[0])
    # The faces already labelled as this person, least typical first, so a
    # wrong label stands out at the start of the list and can be removed.
    centroid = references.mean(axis=0)
    labelled_faces = sorted(
        (f for f in faces if labels.get(f.id) == person and f.embedding_id in vectors),
        key=lambda f: float(vectors[f.embedding_id] @ centroid),
    )
    return {
        "person": person,
        "labelled": [
            _face_payload(face, person, float(vectors[face.embedding_id] @ centroid) / max(float(np.linalg.norm(centroid)), 1e-12))
            for face in labelled_faces
        ],
        "faces": [_face_payload(face, None, similarity) for similarity, face in scored[:MAX_SUGGESTIONS]],
    }


class Assignment(BaseModel):
    face_id: int
    # A name, NOT_A_FACE, or null to remove the label.
    person: str | None

    @field_validator("person")
    @classmethod
    def clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value or len(value) > MAX_PERSON_NAME:
            raise ValueError(f"Names must be 1 to {MAX_PERSON_NAME} characters")
        return value


class LabelRequest(BaseModel):
    assignments: list[Assignment]


@router.put("/{event_id}/labels")
def save_labels(
    request: LabelRequest,
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    face_ids = {a.face_id for a in request.assignments}
    valid = set(
        session.exec(select(FaceRecord.id).where(FaceRecord.event_id == event.id, FaceRecord.id.in_(face_ids))).all()
    )
    if face_ids - valid:
        raise HTTPException(status_code=404, detail="Some faces are not in this event")
    session.execute(delete(FaceLabel).where(FaceLabel.face_id.in_(face_ids)))
    for assignment in request.assignments:
        if assignment.person is not None:
            session.add(FaceLabel(event_id=event.id, face_id=assignment.face_id, person=assignment.person))
    session.commit()
    return {"saved": len(request.assignments)}


@router.get("/{event_id}/labels/export")
def export_labels(
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    """Labels as JSON (person -> faces and photos), e.g. to attach to a report."""
    faces = {face.id: face for face in _faces(session, event.id)}
    people: dict[str, dict] = defaultdict(lambda: {"face_ids": [], "photo_ids": set()})
    for face_id, person in _labels(session, event.id).items():
        if person != NOT_A_FACE and face_id in faces:
            people[person]["face_ids"].append(face_id)
            people[person]["photo_ids"].add(faces[face_id].photo_id)
    return {
        "event_id": event.id,
        "people": {
            name: {"face_ids": sorted(v["face_ids"]), "photo_ids": sorted(v["photo_ids"])}
            for name, v in sorted(people.items())
        },
    }


@router.get("/{event_id}/faces/{face_id}/crop")
def face_crop(
    face_id: int,
    event: Event = Depends(require_event_host),
    session: Session = Depends(get_session),
):
    """A small square JPEG of one detected face, with some context around it."""
    face = session.get(FaceRecord, face_id)
    photo = session.get(Photo, face.photo_id) if face and face.event_id == event.id else None
    if not photo:
        raise HTTPException(status_code=404, detail="Face not found")
    path = (UPLOAD_DIR / photo.s3_key).resolve()
    # Same reader as the worker, so the stored box lines up with the pixels.
    image = cv2.imread(str(path)) if path.is_relative_to(UPLOAD_DIR) and path.is_file() else None
    if image is None:
        raise HTTPException(status_code=404, detail="Photo file not found")
    x1, y1, x2, y2 = json.loads(face.bbox)
    side = max(x2 - x1, y2 - y1) * (1 + 2 * CROP_MARGIN)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    height, width = image.shape[:2]
    left, top = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    right, bottom = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        raise HTTPException(status_code=404, detail="Face not found")
    crop = cv2.resize(crop, (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode face")
    return Response(
        encoded.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
