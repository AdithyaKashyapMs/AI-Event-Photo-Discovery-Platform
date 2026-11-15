from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS, UPLOAD_DIR
from app.database import create_db_and_tables
from app.routers import auth, events, invites, labels, photos, users


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_db_and_tables()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title='Event Photo Discovery API', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Photos are never served as static files; see GET /photos/{photo_id}/file.
app.include_router(auth.router, prefix='/auth', tags=['auth'])
app.include_router(events.router, prefix='/events', tags=['events'])
app.include_router(labels.router, prefix='/events', tags=['labelling'])
app.include_router(invites.router, prefix='/invites', tags=['invites'])
app.include_router(photos.router, prefix='/photos', tags=['photos'])
app.include_router(users.router, prefix='/users', tags=['users'])
