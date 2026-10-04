"""phase 2 durable workspace schema

Add project ownership, durable runs, queue/fencing, event sequencing, and outbox.
Backfill Phase 1 data into the new project model.

Revision ID: def456
Revises: abc123
Create Date: 2026-10-04
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'def456'
down_revision: Union[str, None] = 'abc123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use String(36) for UUIDs to be SQLite-compatible

    # 1. Create projects table
    op.create_table(
        'projects',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('owner_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_user_id', name='uq_projects_owner_user'),
    )
    op.create_index('ix_projects_owner_user_id', 'projects', ['owner_user_id'])

    # 2. Add project_id to conversations (nullable for now, backfilled later)
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.add_column(
            sa.Column('project_id', sa.String(36), nullable=True),
        )
        batch_op.create_foreign_key(
            'fk_conversations_project_id', 'projects', ['project_id'], ['id'], ondelete='CASCADE'
        )

    # 3. Create runs table
    op.create_table(
        'runs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('graph_thread_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='created'),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('provider_started_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('error_code', sa.String(50), nullable=True),
        sa.Column('usage', sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('created', 'queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out', 'stale')", name='ck_runs_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_runs_conversation_id', 'runs', ['conversation_id'])
    op.create_index('ix_runs_project_id', 'runs', ['project_id'])
    op.create_index('ix_runs_status', 'runs', ['status'])
    op.create_index('ix_runs_ended_at', 'runs', ['ended_at'])

    # 4. Create run_jobs table (queue + fencing)
    op.create_table(
        'run_jobs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('owner_id', sa.String(50), nullable=True),
        sa.Column('fence', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_run_jobs_run_id', 'run_jobs', ['run_id'])
    op.create_index('ix_run_jobs_available_at', 'run_jobs', ['available_at'])
    op.create_index('ix_run_jobs_owner_id', 'run_jobs', ['owner_id'])

    # 5. Add next_event_sequence to conversations
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.add_column(
            sa.Column('next_event_sequence', sa.Integer(), nullable=False, server_default='0'),
        )

    # 6. Create events table
    op.create_table(
        'events',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(100), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'sequence', name='uq_events_conversation_sequence'),
    )
    op.create_index('ix_events_conversation_id_sequence', 'events', ['conversation_id', 'sequence'])

    # 7. Create outbox table (SSE notification pattern)
    op.create_table(
        'outbox',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('events.id', ondelete='CASCADE'), nullable=True),
        sa.Column('type', sa.String(100), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_outbox_conversation_id', 'outbox', ['conversation_id'])
    op.create_index('ix_outbox_processed_at', 'outbox', ['processed_at'])

    # 8. Add run_id to messages (nullable for historical messages)
    with op.batch_alter_table('messages') as batch_op:
        batch_op.add_column(
            sa.Column('run_id', sa.String(36), nullable=True),
        )
        batch_op.create_foreign_key(
            'fk_messages_run_id', 'runs', ['run_id'], ['id'], ondelete='SET NULL'
        )

    # 8a. Unique constraint: messages unique by (run_id, role) for chat runs
    op.execute(
        "CREATE UNIQUE INDEX ix_messages_run_id_role ON messages(run_id, role) WHERE run_id IS NOT NULL"
    )

    # 8b. One active run per conversation (partial unique index)
    op.execute(
        "CREATE UNIQUE INDEX ix_runs_conversation_active "
        "ON runs(conversation_id) "
        "WHERE status IN ('created', 'queued', 'running')"
    )

    # 9. Backfill: Create a default project for each user
    bind = op.get_bind()
    result = bind.execute(sa.text("SELECT id, username FROM users"))
    users = result.fetchall()
    for row in users:
        user_id, username = row.id, row.username
        bind.execute(
            sa.text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner_id, :name)"),
            {"id": str(uuid.uuid4()), "owner_id": user_id, "name": f"{username}'s project"},
        )

    # 10. Backfill: Set conversations.project_id based on owner's project
    result = bind.execute(sa.text("SELECT id, owner_user_id FROM projects"))
    projects = result.fetchall()
    project_map = {}
    for row in projects:
        project_map[row.owner_user_id] = row.id
    for owner_id, proj_id in project_map.items():
        bind.execute(
            sa.text("UPDATE conversations SET project_id = :proj_id WHERE user_id = :owner_id"),
            {"proj_id": proj_id, "owner_id": owner_id},
        )

    # 10a. Index on conversations.project_id
    op.create_index('ix_conversations_project_id', 'conversations', ['project_id'])

    # 11. Make conversations.project_id non-nullable (after backfill)
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.alter_column('project_id', nullable=False)


def downgrade() -> None:
    # Reverse order: drop FKs first, then tables
    op.drop_index('ix_runs_conversation_active')
    op.drop_index('ix_messages_run_id_role')
    with op.batch_alter_table('messages') as batch_op:
        batch_op.drop_constraint('fk_messages_run_id', type_='foreignkey')
        batch_op.drop_column('run_id')
    op.drop_index('ix_outbox_processed_at')
    op.drop_index('ix_outbox_conversation_id')
    op.drop_table('outbox')
    op.drop_index('ix_events_conversation_id_sequence')
    op.drop_table('events')
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_column('next_event_sequence')
    op.drop_index('ix_run_jobs_owner_id')
    op.drop_index('ix_run_jobs_available_at')
    op.drop_index('ix_run_jobs_run_id')
    op.drop_table('run_jobs')
    op.drop_index('ix_runs_ended_at')
    op.drop_index('ix_runs_status')
    op.drop_index('ix_runs_project_id')
    op.drop_index('ix_runs_conversation_id')
    op.drop_table('runs')
    op.drop_index('ix_conversations_project_id')
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_constraint('fk_conversations_project_id', type_='foreignkey')
        batch_op.drop_column('project_id')
    op.drop_index('ix_projects_owner_user_id')
    op.drop_table('projects')