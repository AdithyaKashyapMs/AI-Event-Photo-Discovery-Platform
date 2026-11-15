"""baseline schema

The schema as it stood when Alembic was introduced. Databases created before
that by SQLModel.create_all are brought up to this schema and stamped with
this revision by app.database.run_migrations, not re-created.

Revision ID: 0001
Revises: 
Create Date: 2026-11-15 15:50:02.273416
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role', sa.Enum('host', 'attendee', name='userrole'), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_email'), ['email'], unique=True)

    op.create_table('event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('invite_token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_host_id'), ['host_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_invite_token'), ['invite_token'], unique=True)

    op.create_table('eventmembership',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('joined_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'event_id')
    )
    with op.batch_alter_table('eventmembership', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eventmembership_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_eventmembership_user_id'), ['user_id'], unique=False)

    op.create_table('photo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('s3_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('phash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_photo_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photo_phash'), ['phash'], unique=False)

    op.create_table('facerecord',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('photo_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('embedding_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('quality_score', sa.Float(), nullable=False),
    sa.Column('bbox', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.ForeignKeyConstraint(['photo_id'], ['photo.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('facerecord', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_facerecord_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_facerecord_photo_id'), ['photo_id'], unique=False)

    op.create_table('photomatch',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('photo_id', sa.Integer(), nullable=False),
    sa.Column('similarity', sa.Float(), nullable=False),
    sa.Column('matched_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.ForeignKeyConstraint(['photo_id'], ['photo.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'photo_id')
    )
    with op.batch_alter_table('photomatch', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_photomatch_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photomatch_photo_id'), ['photo_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photomatch_user_id'), ['user_id'], unique=False)

    op.create_table('facelabel',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('face_id', sa.Integer(), nullable=False),
    sa.Column('person', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('labelled_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.ForeignKeyConstraint(['face_id'], ['facerecord.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('facelabel', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_facelabel_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_facelabel_face_id'), ['face_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_facelabel_person'), ['person'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('facelabel', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_facelabel_person'))
        batch_op.drop_index(batch_op.f('ix_facelabel_face_id'))
        batch_op.drop_index(batch_op.f('ix_facelabel_event_id'))

    op.drop_table('facelabel')
    with op.batch_alter_table('photomatch', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_photomatch_user_id'))
        batch_op.drop_index(batch_op.f('ix_photomatch_photo_id'))
        batch_op.drop_index(batch_op.f('ix_photomatch_event_id'))

    op.drop_table('photomatch')
    with op.batch_alter_table('facerecord', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_facerecord_photo_id'))
        batch_op.drop_index(batch_op.f('ix_facerecord_event_id'))

    op.drop_table('facerecord')
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_photo_phash'))
        batch_op.drop_index(batch_op.f('ix_photo_event_id'))

    op.drop_table('photo')
    with op.batch_alter_table('eventmembership', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eventmembership_user_id'))
        batch_op.drop_index(batch_op.f('ix_eventmembership_event_id'))

    op.drop_table('eventmembership')
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_invite_token'))
        batch_op.drop_index(batch_op.f('ix_event_host_id'))

    op.drop_table('event')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_email'))

    op.drop_table('user')
    # PostgreSQL keeps the enum type after its table is dropped.
    sa.Enum(name='userrole').drop(op.get_bind(), checkfirst=True)
