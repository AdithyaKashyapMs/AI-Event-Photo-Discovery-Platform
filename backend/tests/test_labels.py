import json
import uuid

import numpy as np
import pytest
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlmodel import Session, select

from app.config import face_collection_name, get_qdrant_client
from app.database import engine
from app.models import NOT_A_FACE, FaceLabel, FaceRecord


@pytest.fixture
def faces(world):
    """Four faces: three of person A (one hard, low similarity) and one of B."""
    rng = np.random.default_rng(0)
    a, b = rng.standard_normal(512), rng.standard_normal(512)
    noise = lambda: rng.standard_normal(512)  # noqa: E731
    vectors = {
        'a1': a + 0.2 * noise(),
        'a2': a + 0.2 * noise(),
        'a_hard': a + 1.6 * noise(),  # below the clustering threshold, still A
        'b1': b,
    }
    client = get_qdrant_client()
    name = face_collection_name(world.event_id)
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=512, distance=Distance.COSINE))
    ids = {}
    with Session(engine) as session:
        for key, vector in vectors.items():
            embedding_id = str(uuid.uuid4())
            photo_id = world.matched_photo if key in ('a1', 'b1') else world.unmatched_photo
            record = FaceRecord(
                photo_id=photo_id, event_id=world.event_id, embedding_id=embedding_id,
                quality_score=0.8, bbox=json.dumps([1, 1, 7, 7]),
            )
            session.add(record)
            session.commit()
            ids[key] = record.id
            client.upsert(name, [PointStruct(id=embedding_id, vector=vector.tolist(), payload={'photo_id': photo_id})])
    yield ids
    client.delete_collection(name)


def test_labelling_is_host_only(world, faces):
    for guest in (world.attendee, world.outsider):
        assert guest.get(f'/events/{world.event_id}/labelling').status_code in (403, 404)
        assert guest.put(f'/events/{world.event_id}/labels', json={'assignments': []}).status_code in (403, 404)
        assert guest.get(f'/events/{world.event_id}/faces/{faces["a1"]}/crop').status_code in (403, 404)


def test_overview_groups_faces(world, faces):
    data = world.host.get(f'/events/{world.event_id}/labelling').json()
    assert data['total_faces'] == 4 and data['labelled_faces'] == 0
    group_sizes = sorted(len(g['faces']) for g in data['groups'])
    assert group_sizes[-1] == 2  # a1 + a2 grouped; the hard face is on its own


def test_label_then_suggestions_surface_the_hard_face(world, faces):
    url = f'/events/{world.event_id}/labels'
    saved = world.host.put(url, json={'assignments': [
        {'face_id': faces['a1'], 'person': '  Rahul  '},
        {'face_id': faces['a2'], 'person': 'Rahul'},
    ]})
    assert saved.status_code == 200

    suggestions = world.host.get(f'/events/{world.event_id}/labelling/suggestions', params={'person': 'Rahul'}).json()
    suggested = [f['face_id'] for f in suggestions['faces']]
    assert suggested[0] == faces['a_hard']  # most similar unlabelled face
    assert faces['a1'] not in suggested

    world.host.put(url, json={'assignments': [{'face_id': faces['a_hard'], 'person': 'Rahul'}]})
    export = world.host.get(f'/events/{world.event_id}/labels/export').json()
    assert export['people']['Rahul']['face_ids'] == sorted([faces['a1'], faces['a2'], faces['a_hard']])
    assert export['people']['Rahul']['photo_ids'] == sorted([world.matched_photo, world.unmatched_photo])


def test_relabel_and_unlabel(world, faces):
    url = f'/events/{world.event_id}/labels'
    world.host.put(url, json={'assignments': [{'face_id': faces['b1'], 'person': 'Wrong'}]})
    world.host.put(url, json={'assignments': [{'face_id': faces['b1'], 'person': NOT_A_FACE}]})
    export = world.host.get(f'/events/{world.event_id}/labels/export').json()
    assert export['people'] == {}  # not-a-face labels are excluded
    world.host.put(url, json={'assignments': [{'face_id': faces['b1'], 'person': None}]})
    with Session(engine) as session:
        assert session.exec(select(FaceLabel)).all() == []


def test_faces_from_other_events_are_rejected(world, faces):
    other = world.host.post('/events/', params={'name': 'Other'}).json()['id']
    response = world.host.put(f'/events/{other}/labels', json={'assignments': [{'face_id': faces['a1'], 'person': 'X'}]})
    assert response.status_code == 404


def test_face_crop_is_a_jpeg(world, faces):
    response = world.host.get(f'/events/{world.event_id}/faces/{faces["a1"]}/crop')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.content[:2] == b'\xff\xd8'


def test_deleting_a_photo_removes_its_labels(world, faces):
    world.host.put(f'/events/{world.event_id}/labels', json={'assignments': [{'face_id': faces['a1'], 'person': 'Rahul'}]})
    assert world.host.delete(f'/photos/{world.matched_photo}').status_code == 200
    with Session(engine) as session:
        assert session.exec(select(FaceLabel)).all() == []


def test_deleting_the_event_removes_labels(world, faces):
    world.host.put(f'/events/{world.event_id}/labels', json={'assignments': [{'face_id': faces['a1'], 'person': 'Rahul'}]})
    assert world.host.delete(f'/events/{world.event_id}').status_code == 200
    with Session(engine) as session:
        assert session.exec(select(FaceLabel)).all() == []
