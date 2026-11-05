from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session
from app.auth import decode_token
from app.database import get_session
from app.models import User, UserRole
from jose import JWTError

# Extracts Bearer token from the Authorization header
security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: Session = Depends(get_session),
) -> User:
    """Verify the JWT and return the corresponding database user."""
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload["sub"])
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