from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList
from app.models import User, FaceRecord, EventMembership
from app.deps import get_current_user
from app.database import engine

router = APIRouter()

@router.delete('/me/biometric-data')
def delete_biometric_data(current_user: User = Depends(get_current_user)):
    # Find all events the user is a member of
    with Session(engine) as session:
        memberships = session.exec(
            select(EventMembership).where(EventMembership.user_id == current_user.id)
        ).all()

        event_ids = [m.event_id for m in memberships]
        if not event_ids:
            return {'detail': 'All biometric data deleted successfully', 'deleted_count': 0}

        # Find all FaceRecords associated with the user's events.
        face_records = session.exec(
            select(FaceRecord).where(FaceRecord.event_id.in_(event_ids))
        ).all()

        # Delete embeddings from Qdrant in batches, while allowing the database
        # cleanup to complete if an individual collection is unavailable.
        client = QdrantClient(host='localhost', port=6333)
        embeddings_by_collection = {}
        for record in face_records:
            collection_name = f'event_{record.event_id}_faces'
            embeddings_by_collection.setdefault(collection_name, []).append(
                record.embedding_id
            )

        qdrant_failures = []
        for collection_name, embedding_ids in embeddings_by_collection.items():
            try:
                client.delete(
                    collection_name=collection_name,
                    points_selector=PointIdsList(points=embedding_ids),
                    wait=True,
                )
            except Exception as exc:
                qdrant_failures.append(
                    {'collection': collection_name, 'error': str(exc)}
                )

        # Delete FaceRecords from the database
        for record in face_records:
            session.delete(record)
        session.commit()

    return {
        'detail': 'All biometric data deleted successfully',
        'deleted_count': len(face_records),
        'qdrant_failures': qdrant_failures,
    }