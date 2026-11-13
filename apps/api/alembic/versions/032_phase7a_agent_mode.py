"""Persist each account's agent execution mode and its optimistic revision.

Existing accounts and new accounts default to Copilot at revision 1. The mode
is constrained to the two maintained selections; the revision is always
positive so a compare-and-swap write cannot fall back to an unversioned value.

Revision ID: phase7a032
Revises: phase7a031
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase7a032"
down_revision: str | None = "phase7a031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "agent_mode",
                sa.String(length=16),
                server_default="copilot",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "agent_mode_revision",
                sa.Integer(),
                server_default="1",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_users_agent_mode", "agent_mode IN ('auto', 'copilot')"
        )
        batch.create_check_constraint(
            "ck_users_agent_mode_revision", "agent_mode_revision >= 1"
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_agent_mode_revision", type_="check")
        batch.drop_constraint("ck_users_agent_mode", type_="check")
        batch.drop_column("agent_mode_revision")
        batch.drop_column("agent_mode")
