from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.routers import auth, events, photos, users
from app.database import create_db_and_tables

app = FastAPI(title='Event Photo Discovery API')


@app.on_event('startup')
def initialize_database():
    create_db_and_tables()


app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
os.makedirs('uploads', exist_ok=True)
app.mount('/uploads', StaticFiles(directory='uploads'), name='uploads')
app.include_router(auth.router, prefix='/auth')
app.include_router(events.router, prefix='/events')
app.include_router(photos.router, prefix='/photos')
app.include_router(users.router, prefix="/users")