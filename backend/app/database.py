import logging
import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

from app.models import new_invite_token

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql+psycopg://postgres:postgres@localhost:5432/eventphotos',
)

# SQLite (used by the tests) must allow the connection to be used from
# FastAPI's worker threads.
_connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)

BACKEND_DIR = Path(__file__).resolve().parent.parent
BASELINE_REVISION = '0001'


def _alembic_config():
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND_DIR / 'migrations'))
    # Keep the app's logging setup; alembic.ini's is for the command line.
    config.attributes['configure_logging'] = False
    return config


def _upgrade_pre_alembic_database(connection) -> None:
    """Bring a database made by SQLModel.create_all (before Alembic) up to the
    baseline schema, so it can be stamped instead of re-created."""
    # Tables added after the database was created (for example facelabel).
    SQLModel.metadata.create_all(connection)
    if connection.dialect.name != 'postgresql':
        return
    connection.execute(text('ALTER TABLE photo ADD COLUMN IF NOT EXISTS phash VARCHAR'))
    connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photo_phash ON photo (phash)'))
    connection.execute(text('ALTER TABLE event ADD COLUMN IF NOT EXISTS invite_token VARCHAR'))
    missing = connection.execute(text('SELECT id FROM event WHERE invite_token IS NULL')).scalars().all()
    for event_id in missing:
        connection.execute(
            text('UPDATE event SET invite_token = :token WHERE id = :id'),
            {'token': new_invite_token(), 'id': event_id},
        )
    connection.execute(text('ALTER TABLE event ALTER COLUMN invite_token SET NOT NULL'))
    connection.execute(
        text('CREATE UNIQUE INDEX IF NOT EXISTS ix_event_invite_token ON event (invite_token)')
    )


def run_migrations() -> None:
    """Apply database migrations (backend/migrations) up to the latest revision.

    Runs at API startup unless AUTO_MIGRATE=false; with several API processes,
    disable it and run `alembic upgrade head` once per deployment instead.
    """
    from alembic import command

    config = _alembic_config()
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config.attributes['connection'] = connection
        if 'alembic_version' not in tables and 'user' in tables:
            logger.info('Database predates migrations: upgrading it and stamping revision %s', BASELINE_REVISION)
            _upgrade_pre_alembic_database(connection)
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, 'head')


def create_db_and_tables():
    if os.getenv('AUTO_MIGRATE', 'true').strip().lower() in {'0', 'false', 'no'}:
        return
    run_migrations()


def get_session():
    with Session(engine) as session:
        yield session
