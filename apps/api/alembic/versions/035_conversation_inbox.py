"""Add the durable conversation inbox.

Revision ID: inbox035
Revises: phase7a034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "inbox035"
down_revision: str | None = "phase7a034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_inbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("client_request_id", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="SET NULL")
        ),
        sa.Column(
            "decision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_decisions.id", ondelete="SET NULL"),
        ),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_inbox_conversation_request"
        ),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_inbox_conversation_sequence"
        ),
        sa.CheckConstraint(
            "disposition IN ('clarification', 'queued')", name="ck_inbox_disposition"
        ),
        sa.CheckConstraint(
            "status IN ('attached', 'queued', 'promoted', 'failed')",
            name="ck_inbox_status",
        ),
    )
    op.create_index(
        "ix_inbox_conversation_status_sequence",
        "conversation_inbox",
        ["conversation_id", "status", "sequence"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM conversation_inbox LIMIT 1")).first():
        raise RuntimeError("cannot downgrade: conversation inbox contains messages")
    op.drop_index(
        "ix_inbox_conversation_status_sequence", table_name="conversation_inbox"
    )
    op.drop_table("conversation_inbox")
