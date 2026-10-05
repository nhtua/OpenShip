"""fix runs.client_request_id column type to varchar

Revision ID: d2273e928d11
Revises: 2e60ec7cc0a9
Create Date: 2026-10-04 20:36:19.037763

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2273e928d11'
down_revision: Union[str, Sequence[str], None] = '2e60ec7cc0a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix runs.client_request_id column type to VARCHAR(255)."""
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column(
            "client_request_id",
            type_=sa.String(255),
            nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    pass
