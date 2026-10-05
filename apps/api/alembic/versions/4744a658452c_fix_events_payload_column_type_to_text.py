"""fix events.payload column type to text

Revision ID: 4744a658452c
Revises: 82af9b93462b
Create Date: 2026-10-04 20:40:11.946485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4744a658452c'
down_revision: Union[str, Sequence[str], None] = '82af9b93462b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix events.payload column type to Text."""
    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column(
            "payload",
            type_=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    pass
