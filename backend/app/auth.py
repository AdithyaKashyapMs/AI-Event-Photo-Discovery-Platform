import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import jwt

# APP_ENV=production turns the development fallbacks below into startup errors.
APP_ENV = os.getenv('APP_ENV', 'development').strip().lower()
IS_PRODUCTION = APP_ENV == 'production'

# Secret key for signing tokens. Set JWT_SECRET_KEY in the environment for any
# shared or deployed setup; the fallback exists only for local development.
_DEV_SECRET_KEY = 'changeme'
MIN_SECRET_LENGTH = 32
SECRET_KEY = os.getenv('JWT_SECRET_KEY', _DEV_SECRET_KEY)
if IS_PRODUCTION and (SECRET_KEY == _DEV_SECRET_KEY or len(SECRET_KEY) < MIN_SECRET_LENGTH):
    raise RuntimeError(
        f'APP_ENV=production needs JWT_SECRET_KEY set to a random value of at least '
        f'{MIN_SECRET_LENGTH} characters. Generate one with: '
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
if SECRET_KEY == _DEV_SECRET_KEY:
    warnings.warn(
        'JWT_SECRET_KEY is not set; using the insecure development secret.',
        RuntimeWarning,
        stacklevel=2,
    )
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# The session token lives in an HttpOnly cookie, so page scripts (and any XSS)
# cannot read it. Secure is on by default in production (HTTPS only).
SESSION_COOKIE = 'eventsnap_session'
# Readable, secret-free companion cookie so the frontend knows a session exists.
SESSION_HINT_COOKIE = 'eventsnap_signed_in'
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true' if IS_PRODUCTION else 'false').strip().lower() in {
    '1',
    'true',
    'yes',
}
# Optional, e.g. ".eventsnap.me" when the API and frontend use sibling subdomains.
COOKIE_DOMAIN = os.getenv('COOKIE_DOMAIN') or None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode('utf-8'),
            hashed.encode('utf-8'),
        )
    except (ValueError, TypeError):
        return False


# A real hash to check against when the email is unknown, so a login attempt
# takes the same time whether or not the account exists.
DUMMY_PASSWORD_HASH = hash_password('not-a-real-password')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({'exp': expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
