"""Who may see what.

- The host of an event can see and manage every photo in it.
- An attendee who joined an event can see only the photos their live selfie
  search matched (rows in PhotoMatch).
- Everyone else gets 404, so event and photo IDs are not revealed.
"""

from typing import Optional

from fastapi import Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.deps import get_current_user, require_host
from app.models import Event, EventMembership, Photo, PhotoMatch, User


def is_event_host(event: Event, user: User) -> bool:
    return event.host_id == user.id


def is_event_member(session: Session, event_id: int, user_id: int) -> bool:
    return (
        session.exec(
            select(EventMembership.id).where(
                EventMembership.event_id == event_id,
                EventMembership.user_id == user_id,
            )
        ).first()
        is not None
    )


def matched_photo_ids(session: Session, event_id: int, user_id: int) -> set[int]:
    return set(
        session.exec(
            select(PhotoMatch.photo_id).where(
                PhotoMatch.event_id == event_id,
                PhotoMatch.user_id == user_id,
            )
        ).all()
    )


def visible_photo_ids(session: Session, event: Event, user: User) -> Optional[set[int]]:
    """None means every photo in the event; otherwise the allowed photo IDs."""
    if is_event_host(event, user):
        return None
    return matched_photo_ids(session, event.id, user.id)


def require_event_access(
    event_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Event:
    """The event, if the user hosts it or has joined it."""
    event = session.get(Event, event_id)
    if event and (
        is_event_host(event, current_user)
        or is_event_member(session, event_id, current_user.id)
    ):
        return event
    raise HTTPException(status_code=404, detail="Event not found")


def require_event_host(
    event_id: int,
    current_user: User = Depends(require_host),
    session: Session = Depends(get_session),
) -> Event:
    """The event, if the user is its host."""
    event = session.get(Event, event_id)
    if not event or not is_event_host(event, current_user):
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def get_accessible_photo(
    photo_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Photo:
    """The photo, if the user hosts its event or their selfie search matched it."""
    photo = session.get(Photo, photo_id)
    if photo:
        event = session.get(Event, photo.event_id)
        if event and is_event_host(event, current_user):
            return photo
        allowed = session.exec(
            select(PhotoMatch.id).where(
                PhotoMatch.photo_id == photo_id,
                PhotoMatch.user_id == current_user.id,
            )
        ).first()
        if allowed is not None and is_event_member(session, photo.event_id, current_user.id):
            return photo
    raise HTTPException(status_code=404, detail="Photo not found")
