"""fix runs.graph_thread_id column type to varchar

Revision ID: 82af9b93462b
Revises: d2273e928d11
Create Date: 2026-10-04 20:39:48.678601

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82af9b93462b'
down_revision: Union[str, Sequence[str], None] = 'd2273e928d11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix runs.graph_thread_id column type to VARCHAR(255)."""
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column(
            "graph_thread_id",
            type_=sa.String(255),
            nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    pass
