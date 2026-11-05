# POST /auth/register and POST /auth/login endpoints
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from app.models import User, UserRole
from app.auth import hash_password, verify_password, create_access_token
from app.database import get_session

router = APIRouter()

class RegisterRequest(BaseModel):
    email: str
    password: str
    role: UserRole

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post('/register')
def register(req: RegisterRequest, session: Session = Depends(get_session)):
    # Check if email already exists
    existing = session.exec(select(User).where(User.email == req.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail='Email already registered')
    # Create new user with hashed password
    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        role=req.role
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    # Return access token
    token = create_access_token({'sub': str(user.id), 'role': user.role})
    return {'access_token': token, 'token_type': 'bearer'}

@router.post('/login')
def login(req: LoginRequest, session: Session = Depends(get_session)):
    # Find user by email
    user = session.exec(select(User).where(User.email == req.email)).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    # Return access token
    token = create_access_token({'sub': str(user.id), 'role': user.role})
    return {'access_token': token, 'token_type': 'bearer'}