"""add dedicated client_request_id column to runs table

Separates the idempotency key from graph_thread_id (which is reserved
for LangGraph thread state resumption).

Revision ID: fed789
Revises: def456
Create Date: 2026-10-04
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fed789'
down_revision: Union[str, None] = 'def456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add client_request_id column
    with op.batch_alter_table('runs') as batch_op:
        batch_op.add_column(
            sa.Column('client_request_id', sa.String(255), nullable=True),
        )

    # Create unique index for idempotency enforcement
    op.execute(
        "CREATE UNIQUE INDEX ix_runs_client_request_id ON runs(client_request_id) "
        "WHERE client_request_id IS NOT NULL"
    )

    # Backfill: copy graph_thread_id values to client_request_id for existing runs
    op.execute("UPDATE runs SET client_request_id = graph_thread_id WHERE graph_thread_id IS NOT NULL")


def downgrade() -> None:
    op.drop_index('ix_runs_client_request_id')
    with op.batch_alter_table('runs') as batch_op:
        batch_op.drop_column('client_request_id')