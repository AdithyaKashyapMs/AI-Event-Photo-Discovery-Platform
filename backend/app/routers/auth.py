# POST /auth/register, POST /auth/login, POST /auth/logout and GET /auth/me
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import ratelimit
from app.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    COOKIE_DOMAIN,
    COOKIE_SECURE,
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    SESSION_HINT_COOKIE,
    create_access_token,
    hash_password,
    verify_password,
)
from app.database import get_session
from app.deps import check_request_origin, get_current_user
from app.models import User, UserRole

router = APIRouter()

# Practical check only; deliverability is not verified.
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MIN_PASSWORD_LENGTH = 8
# bcrypt only uses the first 72 bytes of a password.
MAX_PASSWORD_BYTES = 72


def normalize_email(email: str) -> str:
    return email.strip().lower()


class RegisterRequest(BaseModel):
    email: str
    password: str
    role: UserRole

    @field_validator('email')
    @classmethod
    def check_email(cls, value: str) -> str:
        value = normalize_email(value)
        if len(value) > 254 or not EMAIL_PATTERN.match(value):
            raise ValueError('Enter a valid email address')
        return value

    @field_validator('password')
    @classmethod
    def check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f'Password must be at least {MIN_PASSWORD_LENGTH} characters')
        if len(value.encode('utf-8')) > MAX_PASSWORD_BYTES:
            raise ValueError(f'Password must be at most {MAX_PASSWORD_BYTES} bytes')
        return value


class LoginRequest(BaseModel):
    email: str
    password: str


def start_session(response: Response, user: User) -> dict:
    """Sign the user in: the token goes into an HttpOnly cookie, not the body."""
    token = create_access_token({'sub': str(user.id), 'role': user.role.value})
    max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    common = dict(max_age=max_age, path='/', samesite='lax', secure=COOKIE_SECURE, domain=COOKIE_DOMAIN)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(SESSION_HINT_COOKIE, '1', httponly=False, **common)
    return {'id': user.id, 'email': user.email, 'role': user.role.value}


@router.post('/register')
def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    check_request_origin(request)
    ratelimit.hit(ratelimit.REGISTER_PER_IP, ratelimit.client_ip(request))
    if session.exec(select(User).where(User.email == req.email)).first():
        raise HTTPException(status_code=400, detail='Email already registered')
    user = User(email=req.email, hashed_password=hash_password(req.password), role=req.role)
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        # Two registrations for the same email at the same moment.
        session.rollback()
        raise HTTPException(status_code=400, detail='Email already registered') from exc
    session.refresh(user)
    return start_session(response, user)


@router.post('/login')
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    # Login CSRF would sign a victim into the attacker's account.
    check_request_origin(request)
    email = normalize_email(req.email)
    ratelimit.hit(ratelimit.LOGIN_PER_IP, ratelimit.client_ip(request))
    ratelimit.hit(ratelimit.LOGIN_PER_EMAIL, email)
    user = session.exec(select(User).where(User.email == email)).first()
    # Check a password even for unknown emails so response time does not
    # reveal which emails have accounts.
    password_ok = verify_password(req.password, user.hashed_password if user else DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        raise HTTPException(status_code=401, detail='Invalid email or password')
    return start_session(response, user)


@router.post('/logout')
def logout(response: Response):
    for name in (SESSION_COOKIE, SESSION_HINT_COOKIE):
        response.delete_cookie(name, path='/', domain=COOKIE_DOMAIN, secure=COOKIE_SECURE, samesite='lax')
    return {'signed_out': True}


@router.get('/me')
def read_current_user(user: User = Depends(get_current_user)):
    return {'id': user.id, 'email': user.email, 'role': user.role.value}
