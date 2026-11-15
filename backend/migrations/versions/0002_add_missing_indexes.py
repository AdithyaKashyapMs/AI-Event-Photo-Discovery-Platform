"""add indexes and constraints missing from pre-Alembic databases

Databases created by SQLModel.create_all before these were added to
app/models.py never got them (create_all does not alter existing tables).
Each one is created only if it is missing, so this is a no-op on databases
built from 0001.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ('ix_event_host_id', 'event', 'host_id'),
    ('ix_eventmembership_event_id', 'eventmembership', 'event_id'),
    ('ix_eventmembership_user_id', 'eventmembership', 'user_id'),
    ('ix_facerecord_event_id', 'facerecord', 'event_id'),
    ('ix_facerecord_photo_id', 'facerecord', 'photo_id'),
    ('ix_photo_event_id', 'photo', 'event_id'),
]
MEMBERSHIP_UNIQUE = 'eventmembership_user_id_event_id_key'


def _has_unique(inspector, table: str, columns: list[str]) -> bool:
    for constraint in inspector.get_unique_constraints(table):
        if sorted(constraint['column_names']) == sorted(columns):
            return True
    return any(
        index.get('unique') and sorted(index['column_names']) == sorted(columns)
        for index in inspector.get_indexes(table)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for name, table, column in INDEXES:
        if name not in {index['name'] for index in inspector.get_indexes(table)}:
            op.create_index(name, table, [column])

    if not _has_unique(inspector, 'eventmembership', ['user_id', 'event_id']):
        # Keep the first membership if a user joined the same event twice.
        op.execute(
            'DELETE FROM eventmembership WHERE id NOT IN '
            '(SELECT MIN(id) FROM eventmembership GROUP BY user_id, event_id)'
        )
        with op.batch_alter_table('eventmembership') as batch:
            batch.create_unique_constraint(MEMBERSHIP_UNIQUE, ['user_id', 'event_id'])


def downgrade() -> None:
    # 0001 already defines these for new databases; nothing to undo.
    pass
