"""add actor_id column to events table

Tracks the user/actor who triggered each durable semantic event.

Revision ID: abc123
Revises: fed789
Create Date: 2026-10-04
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'ghi789'
down_revision: Union[str, None] = 'fed789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if column already exists (idempotent for test re-runs)
    from alembic import op
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    columns = [c['name'] for c in inspector.get_columns('events')]

    if 'actor_id' not in columns:
        with op.batch_alter_table('events') as batch_op:
            batch_op.add_column(
                sa.Column('actor_id', UUID(as_uuid=True), nullable=True)
            )

    # Add foreign key separately for SQLite compatibility
    try:
        op.execute("CREATE INDEX ix_events_actor_id ON events(actor_id)")
    except Exception:
        pass  # Index may already exist

    try:
        op.execute("ALTER TABLE events ADD CONSTRAINT fk_events_actor_id_users FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL")
    except Exception:
        pass  # Constraint may already exist


def downgrade() -> None:
    try:
        op.execute("ALTER TABLE events DROP CONSTRAINT fk_events_actor_id_users")
    except Exception:
        pass
    try:
        op.drop_index('ix_events_actor_id', table_name='events')
    except Exception:
        pass
    with op.batch_alter_table('events') as batch_op:
        batch_op.drop_column('actor_id')