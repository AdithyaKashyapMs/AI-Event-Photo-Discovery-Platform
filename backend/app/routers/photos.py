from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue
from sqlmodel import Session, delete, select

from app.access import get_accessible_photo, is_event_host
from app.config import THUMBNAIL_DIR, UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import get_session
from app.deps import get_current_user
from app.hierarchy import forget_cached_hierarchy
from app.models import Event, FaceLabel, FaceRecord, Photo, PhotoMatch, User
from app.tasks import process_photo

router = APIRouter()

THUMBNAIL_SIZE = (640, 640)


def photo_path(photo: Photo) -> Path:
    """Absolute path of a stored photo, refusing keys that escape UPLOAD_DIR."""
    path = (UPLOAD_DIR / photo.s3_key).resolve()
    if not path.is_relative_to(UPLOAD_DIR) or not path.is_file():
        raise HTTPException(status_code=404, detail='Photo file not found')
    return path


def thumbnail_path(photo: Photo, source: Path) -> Path:
    """Create (once) and return a small JPEG preview for gallery grids."""
    thumbnail = THUMBNAIL_DIR / f'{photo.id}.jpg'
    if thumbnail.is_file() and thumbnail.stat().st_mtime >= source.stat().st_mtime:
        return thumbnail
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            # Apply the camera's EXIF rotation so previews are upright.
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail(THUMBNAIL_SIZE)
            temporary = thumbnail.with_suffix('.tmp')
            image.save(temporary, 'JPEG', quality=82, optimize=True)
            temporary.replace(thumbnail)
    except OSError as exc:
        raise HTTPException(status_code=404, detail='Photo file not found') from exc
    return thumbnail


@router.get('/{photo_id}/status')
def get_photo_status(photo: Photo = Depends(get_accessible_photo)):
    # Current processing status: pending, processing, done, duplicate, or failed
    return {
        'photo_id': photo.id,
        'status': photo.status,
    }


@router.get('/{photo_id}/file')
def get_photo_file(
    variant: Literal['full', 'thumb'] = Query('full'),
    photo: Photo = Depends(get_accessible_photo),
):
    """Serve a photo to its event's host or to an attendee whose selfie matched it."""
    source = photo_path(photo)
    path = thumbnail_path(photo, source) if variant == 'thumb' else source
    return FileResponse(
        path,
        headers={
            # Photos are private: browsers may cache them, shared caches may not.
            'Cache-Control': 'private, max-age=3600',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.delete('/{photo_id}')
def delete_photo(
    photo_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete one photo (event host only): the file, its thumbnail, the face
    embeddings found in it, and every attendee's match to it."""
    photo = session.get(Photo, photo_id)
    event = session.get(Event, photo.event_id) if photo else None
    if not photo or not event or not is_event_host(event, current_user):
        raise HTTPException(status_code=404, detail='Photo not found')
    if photo.status == 'processing':
        raise HTTPException(
            status_code=409,
            detail='This photo is being processed right now. Try again in a few seconds.',
        )

    # Face embeddings for this photo.
    client = get_qdrant_client()
    collection_name = face_collection_name(photo.event_id)
    if client.collection_exists(collection_name):
        client.delete(
            collection_name=collection_name,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key='photo_id', match=MatchValue(value=photo.id))])
            ),
            wait=True,
        )
        forget_cached_hierarchy(collection_name)

    # If later uploads were skipped as duplicates of this photo, index one of
    # them instead so those faces stay searchable.
    promoted = None
    if photo.status == 'done' and photo.phash:
        promoted = session.exec(
            select(Photo)
            .where(
                Photo.event_id == photo.event_id,
                Photo.phash == photo.phash,
                Photo.status == 'duplicate',
                Photo.id != photo.id,
            )
            .order_by(Photo.id)
        ).first()
        if promoted:
            promoted.status = 'pending'
            session.add(promoted)

    session.execute(delete(PhotoMatch).where(PhotoMatch.photo_id == photo.id))
    face_ids = select(FaceRecord.id).where(FaceRecord.photo_id == photo.id)
    session.execute(delete(FaceLabel).where(FaceLabel.face_id.in_(face_ids)))
    session.execute(delete(FaceRecord).where(FaceRecord.photo_id == photo.id))
    s3_key = photo.s3_key
    session.delete(photo)
    session.commit()

    (UPLOAD_DIR / s3_key).unlink(missing_ok=True)
    (THUMBNAIL_DIR / f'{photo_id}.jpg').unlink(missing_ok=True)
    if promoted:
        process_photo.delay(promoted.id)
    return {'deleted': True, 'photo_id': photo_id, 'reindexed_duplicate': promoted.id if promoted else None}
