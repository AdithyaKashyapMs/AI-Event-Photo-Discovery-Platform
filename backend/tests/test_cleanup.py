"""scripts/cleanup_orphans removes only data nothing points to."""

import sys
import uuid

from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlmodel import Session

from app.config import UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import engine
from app.models import FaceRecord
from scripts import cleanup_orphans


def _run(monkeypatch, *flags):
    monkeypatch.setattr(sys, 'argv', ['cleanup_orphans', *flags])
    cleanup_orphans.main()


def test_cleanup_removes_orphans_and_keeps_live_data(world, monkeypatch):
    client = get_qdrant_client()
    live = face_collection_name(world.event_id)
    gone = face_collection_name(999)
    for name in (live, gone):
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(name, vectors_config=VectorParams(size=4, distance=Distance.COSINE))

    kept_embedding, stale_embedding = str(uuid.uuid4()), str(uuid.uuid4())
    with Session(engine) as session:
        session.add(FaceRecord(photo_id=world.matched_photo, event_id=world.event_id, embedding_id=kept_embedding,
                               quality_score=1, bbox='[0,0,4,4]'))
        # A face record whose embedding is missing from Qdrant.
        session.add(FaceRecord(photo_id=world.matched_photo, event_id=world.event_id,
                               embedding_id=str(uuid.uuid4()), quality_score=1, bbox='[0,0,4,4]'))
        session.commit()
    client.upsert(live, [
        PointStruct(id=kept_embedding, vector=[1, 0, 0, 0], payload={'photo_id': world.matched_photo}),
        PointStruct(id=stale_embedding, vector=[0, 1, 0, 0], payload={'photo_id': 424242}),
    ])
    stray = UPLOAD_DIR / str(world.event_id) / 'stray.jpg'
    stray.write_bytes(b'x')

    _run(monkeypatch)  # dry run changes nothing
    assert client.collection_exists(gone) and stray.exists()

    _run(monkeypatch, '--apply')
    assert not client.collection_exists(gone)
    assert [str(p.id) for p in client.scroll(live)[0]] == [kept_embedding]
    with Session(engine) as session:
        from sqlmodel import select
        assert [f.embedding_id for f in session.exec(select(FaceRecord)).all()] == [kept_embedding]
    assert not stray.exists()
    assert list((UPLOAD_DIR / '.orphaned').rglob('stray.jpg'))  # moved, recoverable
    # Live photos are untouched.
    assert world.host.get(f'/photos/{world.matched_photo}/file').status_code == 200
    client.delete_collection(live)
