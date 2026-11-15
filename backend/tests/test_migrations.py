"""Migrations build exactly the schema in app/models.py, and pre-Alembic
databases are upgraded in place instead of re-created."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app import database


def _engine(tmp_path, name):
    return create_engine(f'sqlite:///{tmp_path / name}')


def _migrate(connection, target='head'):
    config = database._alembic_config()
    config.attributes['connection'] = connection
    command.upgrade(config, target)
    return config


def _differences(connection):
    context = MigrationContext.configure(connection, opts={'render_as_batch': True})
    return compare_metadata(context, SQLModel.metadata)


def test_migrations_match_models(tmp_path):
    with _engine(tmp_path, 'fresh.db').begin() as connection:
        _migrate(connection)
        assert _differences(connection) == []


def test_downgrade_to_nothing_and_back(tmp_path):
    with _engine(tmp_path, 'roundtrip.db').begin() as connection:
        config = _migrate(connection)
        command.downgrade(config, 'base')
        assert set(inspect(connection).get_table_names()) == {'alembic_version'}
        command.upgrade(config, 'head')
        assert _differences(connection) == []


def test_pre_alembic_database_is_upgraded_and_keeps_its_data(tmp_path, monkeypatch):
    engine = _engine(tmp_path, 'legacy.db')
    with engine.begin() as connection:
        # A database from before Alembic: tables but no alembic_version, and
        # without the indexes and constraint that were added to the models later.
        SQLModel.metadata.create_all(connection)
        connection.execute(text('DROP INDEX ix_photo_event_id'))
        connection.execute(
            text("INSERT INTO user (email, hashed_password, role, created_at) VALUES ('a@b.c', 'x', 'host', '2026-01-01')")
        )
    monkeypatch.setattr(database, 'engine', engine)
    database.run_migrations()

    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0002'
        assert connection.execute(text('SELECT email FROM user')).scalar() == 'a@b.c'
        assert 'ix_photo_event_id' in {i['name'] for i in inspect(connection).get_indexes('photo')}
    # Running again is a no-op.
    database.run_migrations()
