"""Redact revoked credential bytes while retaining connector history.

Revision ID: phase5a012
Revises: phase5a011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a012"
down_revision: str | None = "phase5a011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connector_credentials",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    purged_credentials = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM connector_credentials WHERE purged_at IS NOT NULL"
            )
        ).scalar_one()
    )
    if purged_credentials:
        raise RuntimeError(
            "refusing to downgrade: purged credential bytes cannot be restored"
        )

    op.drop_column("connector_credentials", "purged_at")
