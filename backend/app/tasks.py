import json
import logging
import os
import uuid
import warnings

import cv2
import imagehash
from celery import Celery
from insightface.app import FaceAnalysis
from PIL import Image
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlmodel import Session, func, select

from app.config import REDIS_URL, UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import engine
from app.hierarchy import rebuild_event_hierarchy
from app.models import FaceRecord, Photo

logger = logging.getLogger(__name__)

# Celery app with Redis as the message broker
celery_app = Celery('tasks', broker=REDIS_URL)
celery_app.conf.update(
    # Prefork currently fails on macOS with this Python/billiard combination
    # before the task handler starts. Use solo locally; production can override it.
    worker_pool=os.getenv("CELERY_WORKER_POOL", "solo"),
    worker_concurrency=1,
    task_track_started=True,
)

EMBEDDING_SIZE = 512
# Laplacian variance at which a face crop counts as fully sharp (quality 1.0).
SHARPNESS_FOR_FULL_QUALITY = 500.0

# Loaded on first use so the worker starts quickly.
_face_app = None


def get_face_app():
    global _face_app
    if _face_app is None:
        _face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app


def _rebuild_hierarchy_when_idle(session: Session, event_id: int) -> None:
    """Rebuild the event hierarchy once no photos in the event are still queued.

    Rebuilding reads every indexed face, so doing it after each photo makes a bulk
    upload quadratic. The last photo of a batch triggers a single rebuild.
    """
    remaining = session.exec(
        select(func.count(Photo.id)).where(
            Photo.event_id == event_id,
            Photo.status.in_(['pending', 'processing']),
        )
    ).one()
    if remaining:
        return
    client = get_qdrant_client()
    if client.collection_exists(face_collection_name(event_id)):
        rebuild_event_hierarchy(client, event_id)


def _finish(session: Session, photo: Photo, status: str) -> None:
    photo.status = status
    session.add(photo)
    session.commit()
    _rebuild_hierarchy_when_idle(session, photo.event_id)


def _face_quality(img, bbox) -> float:
    """Sharpness of the face crop (Laplacian variance), scaled to 0.0–1.0."""
    height, width = img.shape[:2]
    x1, y1 = max(0, int(bbox[0])), max(0, int(bbox[1]))
    x2, y2 = min(width, int(bbox[2])), min(height, int(bbox[3]))
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / SHARPNESS_FOR_FULL_QUALITY)


@celery_app.task
def process_photo(photo_id: int):
    with Session(engine) as session:
        photo = session.get(Photo, photo_id)
        if not photo:
            return f"Error: Photo ID {photo_id} not found."

        photo.status = 'processing'
        session.add(photo)
        session.commit()
        session.refresh(photo)

        file_path = UPLOAD_DIR / photo.s3_key
        if not file_path.is_file():
            _finish(session, photo, 'failed')
            return f"Error: Physical file {file_path} not found on disk."

        try:
            # cv2.imread applies the EXIF orientation, so faces are upright.
            img = cv2.imread(str(file_path))
            if img is None:
                raise ValueError("Could not decode image with OpenCV.")

            # Skip exact or near-exact re-uploads before running face detection.
            # The perceptual hash survives re-encoding and resizing.
            with Image.open(file_path) as pil_img:
                img_hash = str(imagehash.phash(pil_img))
            photo.phash = img_hash
            original = session.exec(
                select(Photo).where(
                    Photo.event_id == photo.event_id,
                    Photo.phash == img_hash,
                    Photo.status == 'done',
                    Photo.id != photo.id,
                )
            ).first()
            if original:
                _finish(session, photo, 'duplicate')
                return f"Skipped photo {photo_id}: duplicate of photo {original.id}."

            # Detect faces and compute 512-dimensional embeddings (InsightFace buffalo_l).
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    'ignore',
                    message='`estimate` is deprecated since version 0.26.*',
                    category=FutureWarning,
                    module=r'insightface\.utils\.face_align',
                )
                faces = get_face_app().get(img)

            client = get_qdrant_client()
            collection_name = face_collection_name(photo.event_id)
            if not client.collection_exists(collection_name):
                client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=EMBEDDING_SIZE, distance=Distance.COSINE),
                )

            points = []
            for index, face in enumerate(faces):
                bbox = face['bbox'].astype(int)
                quality = _face_quality(img, bbox)
                embedding_id = str(uuid.uuid4())
                points.append(
                    PointStruct(
                        id=embedding_id,
                        vector=face['embedding'].tolist(),
                        payload={
                            "photo_id": photo_id,
                            "event_id": photo.event_id,
                            "face_index": index,
                            "quality_score": quality,
                        },
                    )
                )
                session.add(
                    FaceRecord(
                        photo_id=photo_id,
                        event_id=photo.event_id,
                        embedding_id=embedding_id,
                        quality_score=quality,
                        bbox=json.dumps(bbox.tolist()),
                    )
                )

            # One request per photo instead of one per face.
            if points:
                client.upsert(collection_name=collection_name, points=points, wait=True)

            _finish(session, photo, 'done')
            return f"Processed photo {photo_id} successfully. Found {len(faces)} faces."

        except Exception as exc:
            logger.exception("Failed processing photo %s", photo_id)
            session.rollback()
            _finish(session, photo, 'failed')
            return f"Failed processing photo {photo_id}: {exc}"
