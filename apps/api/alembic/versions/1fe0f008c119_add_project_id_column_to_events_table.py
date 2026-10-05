"""add project_id column to events table

Revision ID: 1fe0f008c119
Revises: 4744a658452c
Create Date: 2026-10-04 20:46:30.221420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '1fe0f008c119'
down_revision: Union[str, Sequence[str], None] = '4744a658452c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add project_id column to events table (nullable)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("events")}

    if "project_id" not in columns:
        with op.batch_alter_table("events") as batch_op:
            batch_op.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key("fk_events_project_id", "projects", ["project_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    """Downgrade schema."""
    pass
