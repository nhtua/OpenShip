"""Persist fixed-analysis phase state for explicit resumes.

Revision ID: phase6b029
Revises: phase6b028
"""

import sqlalchemy as sa

from alembic import op

revision = "phase6b029"
down_revision = "phase6b028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("analysis_state_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "analysis_state_json")
