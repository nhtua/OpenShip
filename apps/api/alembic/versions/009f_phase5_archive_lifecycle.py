"""Add reversible project and conversation archive timestamps.

Revision ID: phase5a009f
Revises: phase5a009e
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a009f"
down_revision: str | None = "phase5a009e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    archived_projects = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM projects WHERE archived_at IS NOT NULL")).scalar()
        or 0
    )
    archived_conversations = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM conversations WHERE archived_at IS NOT NULL")
        ).scalar()
        or 0
    )
    if archived_projects or archived_conversations:
        raise RuntimeError(
            "refusing to downgrade: archive state would be lost "
            f"({archived_projects} archived projects, {archived_conversations} archived conversations)"
        )

    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("archived_at")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("archived_at")
