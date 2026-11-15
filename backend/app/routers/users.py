from fastapi import APIRouter, Depends
from sqlmodel import Session, delete

from app.database import get_session
from app.deps import get_current_user
from app.models import PhotoMatch, User

router = APIRouter()


@router.delete('/me/biometric-data')
def delete_biometric_data(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete what is stored about this user's selfie searches.

    Selfie frames and the face embedding computed from them are never stored.
    The only per-user result is the list of matched photos (IDs and similarity
    scores), which is deleted here; a new live selfie search is then needed to
    see those photos again.

    Face embeddings of the people who appear in an event's photos belong to
    that event, not to a user account, so they are not removed here. They are
    deleted when the host deletes the photos' event (DELETE /events/{id}).
    """
    result = session.execute(delete(PhotoMatch).where(PhotoMatch.user_id == current_user.id))
    session.commit()
    return {
        'detail': 'Your saved photo matches have been deleted. Your selfie frames and '
                  'selfie face embedding were never stored. Face embeddings from event '
                  'photos are kept by each event until its host deletes the event.',
        'deleted_count': result.rowcount,
        'selfie_frames_stored': False,
        'selfie_embedding_stored': False,
    }
