
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from app.models import Photo, User
from app.database import get_session
from app.deps import get_current_user

router = APIRouter()

@router.get('/{photo_id}/status')
def get_photo_status(
    photo_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    # Query the photo metadata from PostgreSQL
    photo = session.get(Photo, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail='Photo not found')
    
    # Return the current processing status (pending, processing, done, or failed)
    return {
        'photo_id': photo.id,
        'status': photo.status
    }
