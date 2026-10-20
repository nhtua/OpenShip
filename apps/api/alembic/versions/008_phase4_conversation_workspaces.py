"""Add stable per-conversation workspace associations.

Revision ID: phase4c008
Revises: phase4b007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase4c008"
down_revision: str | None = "phase4b007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("relative_root", sa.String(length=255), nullable=False),
        sa.Column("storage_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="available", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("schema_version = 1", name="ck_conversation_workspaces_schema_version"),
        sa.CheckConstraint(
            "status IN ('available', 'unavailable', 'deleted')",
            name="ck_conversation_workspaces_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_workspaces_conversation"),
    )
    op.create_index(
        "ix_conversation_workspaces_project_id",
        "conversation_workspaces",
        ["project_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM conversation_workspaces")).scalar_one()
    if count:
        raise ValueError(
            "refusing to downgrade: conversation workspaces contain stable storage associations "
            f"({count} records). Keep this revision or explicitly remove the associations first."
        )
    op.drop_index("ix_conversation_workspaces_project_id", table_name="conversation_workspaces")
    op.drop_table("conversation_workspaces")
