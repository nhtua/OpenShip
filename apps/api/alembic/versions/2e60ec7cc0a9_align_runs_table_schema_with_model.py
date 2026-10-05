"""align runs table schema with model

Revision ID: 2e60ec7cc0a9
Revises: 1624e578d25d
Create Date: 2026-10-04 20:29:44.947576

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '2e60ec7cc0a9'
down_revision: Union[str, Sequence[str], None] = '1624e578d25d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Align runs table schema with model."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("runs")}

    # Rename completed_at to ended_at if needed
    if "completed_at" in columns and "ended_at" not in columns:
        with op.batch_alter_table("runs") as batch_op:
            batch_op.alter_column("completed_at", new_column_name="ended_at")

    # Add usage column if missing (drop separate token columns first)
    token_cols = {"input_tokens", "output_tokens", "total_tokens", "provider_model", "estimated"}
    if token_cols & columns:
        with op.batch_alter_table("runs") as batch_op:
            for col in sorted(token_cols & columns):
                try:
                    batch_op.drop_column(col)
                except Exception:
                    pass
            if "usage" not in columns:
                batch_op.add_column(sa.Column("usage", sa.Text(), nullable=True))
    elif "usage" not in columns:
        with op.batch_alter_table("runs") as batch_op:
            batch_op.add_column(sa.Column("usage", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    pass
