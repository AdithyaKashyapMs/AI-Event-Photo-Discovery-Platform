"""Joining an event through its secret invite link (/join/{token} in the frontend).

Event IDs are sequential, so knowing an ID is never enough to join; only the
random invite token from the host's link or QR code is.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app import ratelimit
from app.database import get_session
from app.deps import get_current_user
from app.models import Event, EventMembership, User, UserRole

router = APIRouter()


def get_event_by_token(token: str, session: Session) -> Event:
    event = session.exec(select(Event).where(Event.invite_token == token)).first()
    if not event:
        raise HTTPException(
            status_code=404,
            detail="This invite link is invalid or has been replaced by the host.",
        )
    return event


# Name and description for the invite page, before the attendee has logged in.
@router.get('/{token}')
def get_invite(token: str, request: Request, session: Session = Depends(get_session)):
    ratelimit.hit(ratelimit.INVITE_PER_IP, ratelimit.client_ip(request))
    event = get_event_by_token(token, session)
    return {'name': event.name, 'description': event.description}


@router.post('/{token}/join')
def join_with_invite(
    token: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    ratelimit.hit(ratelimit.INVITE_PER_IP, ratelimit.client_ip(request))
    event = get_event_by_token(token, session)
    if user.role != UserRole.attendee:
        raise HTTPException(
            status_code=403,
            detail='Host accounts cannot join events. Sign in with an attendee account.',
        )

    membership = session.exec(
        select(EventMembership).where(
            EventMembership.user_id == user.id,
            EventMembership.event_id == event.id,
        )
    ).first()
    if membership is None:
        membership = EventMembership(user_id=user.id, event_id=event.id)
        session.add(membership)
        session.commit()
    # The event ID is safe to reveal now: every event endpoint checks membership.
    return {'event_id': event.id, 'name': event.name}
