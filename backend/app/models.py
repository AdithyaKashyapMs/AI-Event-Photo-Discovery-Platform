from datetime import datetime, timezone
from typing import Optional
import enum
import secrets

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_invite_token() -> str:
    """Unguessable invite code (128 bits) used in join links and QR codes."""
    return secrets.token_urlsafe(16)


class UserRole(str, enum.Enum):
    host = 'host'
    attendee = 'attendee'


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    role: UserRole
    created_at: datetime = Field(default_factory=_utc_now)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key='user.id', index=True)
    name: str
    description: Optional[str] = None
    # Secret part of the invite link. Event IDs are sequential, so they must
    # never be enough to join; the host can reset this to revoke old links.
    invite_token: str = Field(default_factory=new_invite_token, unique=True, index=True)
    created_at: datetime = Field(default_factory=_utc_now)


class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key='event.id', index=True)
    # Storage key relative to UPLOAD_DIR (named for a future move to S3).
    s3_key: str
    # pending, processing, done, duplicate, or failed
    status: str = 'pending'
    phash: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utc_now)


class FaceRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key='photo.id', index=True)
    event_id: int = Field(foreign_key='event.id', index=True)
    embedding_id: str
    quality_score: float
    bbox: str


class EventMembership(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('user_id', 'event_id'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    event_id: int = Field(foreign_key='event.id', index=True)
    joined_at: datetime = Field(default_factory=_utc_now)


class PhotoMatch(SQLModel, table=True):
    """A photo an attendee is allowed to see because their live selfie matched it.

    Attendees can only view and download photos listed here; hosts can see
    every photo in their own events. Only photo IDs and similarity scores are
    stored, never the selfie or its face embedding.
    """

    __table_args__ = (UniqueConstraint('user_id', 'photo_id'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    event_id: int = Field(foreign_key='event.id', index=True)
    photo_id: int = Field(foreign_key='photo.id', index=True)
    similarity: float
    matched_at: datetime = Field(default_factory=_utc_now)


class FaceLabel(SQLModel, table=True):
    """Ground truth for measuring accuracy: who a detected face really is.

    Written by the host in the labelling tool and read only by
    scripts/evaluate_matching.py. It never affects what attendees can see.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key='event.id', index=True)
    face_id: int = Field(foreign_key='facerecord.id', unique=True, index=True)
    # A name for a person, or NOT_A_FACE for a false detection.
    person: str = Field(index=True)
    labelled_at: datetime = Field(default_factory=_utc_now)


NOT_A_FACE = '__not_a_face__'
