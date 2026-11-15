"""Test setup: SQLite, in-memory Qdrant and fake Redis, so no services are needed.

The environment is configured before any `app` module is imported, because
the database engine and settings are read at import time.
"""

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='eventsnap-tests-'))
os.environ.update(
    {
        'APP_ENV': 'test',
        'DATABASE_URL': f'sqlite:///{_TMP / "test.db"}',
        'UPLOAD_DIR': str(_TMP / 'uploads'),
        'QDRANT_URL': ':memory:',
        'JWT_SECRET_KEY': 'test-secret-key-that-is-long-enough-1234567890',
        'CORS_ORIGINS': 'http://localhost:3000',
        'LIVENESS_REQUIRED': 'true',
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402

from app import liveness, ratelimit  # noqa: E402
from app.config import UPLOAD_DIR  # noqa: E402
from app.database import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Event, EventMembership, Photo, PhotoMatch, User, UserRole  # noqa: E402

FRONTEND_ORIGIN = 'http://localhost:3000'
PASSWORD = 'correct-horse-battery'


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Empty database, uploads folder and Redis for every test."""
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fake = fakeredis.FakeRedis()
    ratelimit.set_redis_client(fake)
    monkeypatch.setattr(liveness, '_redis_client', fake)
    yield fake


@pytest.fixture
def redis_fake(fresh_state):
    return fresh_state


def make_client() -> TestClient:
    # The Origin header is what a browser on the frontend would send.
    return TestClient(app, headers={'Origin': FRONTEND_ORIGIN})


@pytest.fixture
def client():
    with make_client() as test_client:
        yield test_client


def register(client: TestClient, email: str, role: str = 'attendee'):
    response = client.post('/auth/register', json={'email': email, 'password': PASSWORD, 'role': role})
    assert response.status_code == 200, response.text
    return response


class World:
    """A host with one event and two photos, a matched attendee, an unmatched
    attendee and an outsider, each with their own signed-in client."""

    def __init__(self):
        self.host = make_client()
        self.attendee = make_client()
        self.other_attendee = make_client()
        self.outsider = make_client()
        register(self.host, 'host@example.com', 'host')
        register(self.attendee, 'guest@example.com')
        register(self.other_attendee, 'other@example.com')
        register(self.outsider, 'outsider@example.com')

        event = self.host.post('/events/', params={'name': 'Wedding'}).json()
        self.event_id = event['id']
        self.invite_token = event['invite_token']
        for guest in (self.attendee, self.other_attendee):
            assert guest.post(f'/invites/{self.invite_token}/join').status_code == 200

        with Session(engine) as session:
            guest = session.exec(select(User).where(User.email == 'guest@example.com')).one()
            self.attendee_id = guest.id
            self.matched_photo = self._add_photo(session, 'matched.jpg')
            self.unmatched_photo = self._add_photo(session, 'unmatched.jpg')
            session.add(
                PhotoMatch(
                    user_id=guest.id,
                    event_id=self.event_id,
                    photo_id=self.matched_photo,
                    similarity=0.8,
                )
            )
            session.commit()

    def _add_photo(self, session: Session, name: str, status: str = 'done', phash: str | None = None) -> int:
        key = f'{self.event_id}/{name}'
        path = UPLOAD_DIR / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_tiny_jpeg())
        photo = Photo(event_id=self.event_id, s3_key=key, status=status, phash=phash)
        session.add(photo)
        session.commit()
        session.refresh(photo)
        return photo.id

    def add_photo(self, name: str, status: str = 'done', phash: str | None = None) -> int:
        with Session(engine) as session:
            return self._add_photo(session, name, status, phash)

    def close(self):
        for test_client in (self.host, self.attendee, self.other_attendee, self.outsider):
            test_client.close()


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), (200, 120, 40)).save(buffer, 'JPEG')
    return buffer.getvalue()


@pytest.fixture
def world():
    w = World()
    yield w
    w.close()


__all__ = ['Event', 'EventMembership', 'UserRole']
