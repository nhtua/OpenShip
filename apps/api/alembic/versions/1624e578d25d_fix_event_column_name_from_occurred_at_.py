"""fix event column name from occurred_at to created_at

Revision ID: 1624e578d25d
Revises: ghi789
Create Date: 2026-10-04 20:18:27.191218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '1624e578d25d'
down_revision: Union[str, Sequence[str], None] = 'ghi789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename events.occurred_at to events.created_at (if it exists)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("events")]

    if "occurred_at" in columns and "created_at" not in columns:
        with op.batch_alter_table("events") as batch_op:
            batch_op.alter_column("occurred_at", new_column_name="created_at")


def downgrade() -> None:
    """Rename events.created_at back to events.occurred_at (if needed)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("events")]

    if "created_at" in columns and "occurred_at" not in columns:
        with op.batch_alter_table("events") as batch_op:
            batch_op.alter_column("created_at", new_column_name="occurred_at")
