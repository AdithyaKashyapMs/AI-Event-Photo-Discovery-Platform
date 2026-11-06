from fastapi import APIRouter, Depends
from app.models import User
from app.deps import get_current_user

router = APIRouter()

@router.delete('/me/biometric-data')
def delete_biometric_data(current_user: User = Depends(get_current_user)):
    return {
        'detail': 'No persistent attendee biometric data exists to delete. '
                  'Uploaded selfies are processed in memory and discarded.',
        'deleted_count': 0,
        'temporary_data_deleted': True,
    }