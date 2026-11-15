"""Who may see what: hosts see their events; attendees see only matched photos;
everyone else gets 404 so IDs are not revealed."""

from sqlmodel import Session, select

from app.config import UPLOAD_DIR
from app.database import engine
from app.models import FaceRecord, Photo, PhotoMatch


def test_host_sees_every_photo(world):
    for photo_id in (world.matched_photo, world.unmatched_photo):
        assert world.host.get(f'/photos/{photo_id}/file').status_code == 200
    gallery = world.host.get(f'/events/{world.event_id}/gallery').json()
    assert {p['id'] for p in gallery['photos']} == {world.matched_photo, world.unmatched_photo}


def test_attendee_sees_only_matched_photos(world):
    assert world.attendee.get(f'/photos/{world.matched_photo}/file').status_code == 200
    assert world.attendee.get(f'/photos/{world.matched_photo}/file?variant=thumb').status_code == 200
    assert world.attendee.get(f'/photos/{world.unmatched_photo}/file').status_code == 404
    gallery = world.attendee.get(f'/events/{world.event_id}/gallery').json()
    assert [p['id'] for p in gallery['photos']] == [world.matched_photo]


def test_attendee_without_matches_sees_nothing(world):
    assert world.other_attendee.get(f'/photos/{world.matched_photo}/file').status_code == 404
    gallery = world.other_attendee.get(f'/events/{world.event_id}/gallery').json()
    assert gallery['photos'] == []


def test_outsider_gets_404_everywhere(world):
    c, e = world.outsider, world.event_id
    assert c.get(f'/events/{e}').status_code == 404
    assert c.get(f'/events/{e}/gallery').status_code == 404
    assert c.get(f'/photos/{world.matched_photo}/file').status_code == 404
    assert c.post(f'/events/{e}/liveness/challenge').status_code == 404


def test_zip_download_only_contains_visible_photos(world):
    import io
    import zipfile

    ids = f'{world.matched_photo},{world.unmatched_photo}'
    response = world.attendee.get(f'/events/{world.event_id}/gallery/download', params={'photo_ids': ids})
    assert response.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert len(names) == 1


def test_attendees_cannot_use_host_endpoints(world):
    c, e = world.attendee, world.event_id
    assert c.get(f'/events/{e}/analytics').status_code in (403, 404)
    assert c.get(f'/events/{e}/clusters').status_code in (403, 404)
    assert c.get(f'/events/{e}/qr').status_code in (403, 404)
    assert c.post(f'/events/{e}/invite/reset').status_code in (403, 404)
    assert c.delete(f'/events/{e}').status_code in (403, 404)
    assert c.delete(f'/photos/{world.matched_photo}').status_code == 404


def test_another_host_cannot_touch_the_event(world):
    from conftest import make_client, register

    with make_client() as rival:
        register(rival, 'rival@example.com', 'host')
        assert rival.get(f'/events/{world.event_id}').status_code == 404
        assert rival.delete(f'/photos/{world.matched_photo}').status_code == 404
        assert rival.get(f'/photos/{world.unmatched_photo}/file').status_code == 404


def test_invite_token_is_only_shown_to_the_host(world):
    assert 'invite_token' in world.host.get(f'/events/{world.event_id}').json()
    assert 'invite_token' not in world.attendee.get(f'/events/{world.event_id}').json()


# --- Joining ---


def test_wrong_invite_token_is_rejected(world):
    assert world.outsider.get('/invites/not-a-token').status_code == 404
    assert world.outsider.post('/invites/not-a-token/join').status_code == 404


def test_hosts_cannot_join_events(world):
    assert world.host.post(f'/invites/{world.invite_token}/join').status_code == 403


def test_reset_invite_revokes_old_link_but_keeps_members(world):
    new_token = world.host.post(f'/events/{world.event_id}/invite/reset').json()['invite_token']
    assert new_token != world.invite_token
    assert world.outsider.post(f'/invites/{world.invite_token}/join').status_code == 404
    assert world.attendee.get(f'/events/{world.event_id}').status_code == 200
    assert world.outsider.post(f'/invites/{new_token}/join').status_code == 200


# --- Deleting photos ---


def test_host_deletes_photo_and_everything_derived_from_it(world):
    photo_id = world.matched_photo
    with Session(engine) as session:
        key = session.get(Photo, photo_id).s3_key
        session.add(FaceRecord(photo_id=photo_id, event_id=world.event_id, embedding_id='x', quality_score=1, bbox='[]'))
        session.commit()

    response = world.host.delete(f'/photos/{photo_id}')
    assert response.status_code == 200
    assert response.json()['deleted'] is True

    assert not (UPLOAD_DIR / key).exists()
    with Session(engine) as session:
        assert session.get(Photo, photo_id) is None
        assert session.exec(select(PhotoMatch).where(PhotoMatch.photo_id == photo_id)).first() is None
        assert session.exec(select(FaceRecord).where(FaceRecord.photo_id == photo_id)).first() is None
    assert world.attendee.get(f'/photos/{photo_id}/file').status_code == 404


def test_photo_being_processed_cannot_be_deleted(world):
    photo_id = world.add_photo('busy.jpg', status='processing')
    assert world.host.delete(f'/photos/{photo_id}').status_code == 409


def test_deleting_a_photo_reindexes_its_duplicate(world, monkeypatch):
    from app.routers import photos

    queued = []
    monkeypatch.setattr(photos.process_photo, 'delay', lambda photo_id: queued.append(photo_id))
    original = world.add_photo('original.jpg', status='done', phash='abc123')
    duplicate = world.add_photo('copy.jpg', status='duplicate', phash='abc123')

    response = world.host.delete(f'/photos/{original}')
    assert response.json()['reindexed_duplicate'] == duplicate
    assert queued == [duplicate]
    with Session(engine) as session:
        assert session.get(Photo, duplicate).status == 'pending'
