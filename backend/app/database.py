import os

from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql+psycopg://postgres:postgres@localhost:5432/eventphotos',
)

engine = create_engine(DATABASE_URL)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
