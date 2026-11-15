from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlmodel import Session

from app.auth import SESSION_COOKIE, decode_token
from app.config import CORS_ORIGINS
from app.database import get_session
from app.models import User, UserRole

# Browsers authenticate with the HttpOnly session cookie. A Bearer token in the
# Authorization header is still accepted for scripts and the /docs page.
security = HTTPBearer(auto_error=False)

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


def check_request_origin(request: Request) -> None:
    """Block cross-site request forgery for cookie-authenticated requests.

    Cookies are sent automatically, so a state-changing request (POST, PUT,
    DELETE) must come from one of the frontend origins in CORS_ORIGINS.
    SameSite=Lax already stops most cross-site requests; this also covers
    sibling subdomains, which count as the same site. Requests without Origin
    or Referer do not come from a browser page and carry no CSRF risk.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get('origin')
    if not origin:
        referer = request.headers.get('referer')
        origin = _origin_of(referer) if referer else None
    if origin is not None and origin not in CORS_ORIGINS:
        raise HTTPException(status_code=403, detail='Request blocked: unexpected origin')


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: Session = Depends(get_session),
) -> User:
    """Verify the session token and return the corresponding database user."""
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            check_request_origin(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail='Not signed in',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    try:
        payload = decode_token(token)
        user_id = int(payload['sub'])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail='Invalid token')

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail='User not found')
    return user


def require_host(user: User = Depends(get_current_user)):
    """Only allow users with the host role."""
    if user.role != UserRole.host:
        raise HTTPException(status_code=403, detail='Host access required')
    return user
