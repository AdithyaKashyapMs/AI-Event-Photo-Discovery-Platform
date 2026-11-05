from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime, timezone
import enum

class UserRole(str, enum.Enum):
    host = 'host'
    attendee = 'attendee'

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    role: UserRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key='user.id')
    name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key='event.id')
    s3_key: str
    status: str = 'pending'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FaceRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key='photo.id')
    event_id: int = Field(foreign_key='event.id')
    embedding_id: str
    quality_score: float
    bbox: str

class EventMembership(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id')
    event_id: int = Field(foreign_key='event.id')
    joined_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))