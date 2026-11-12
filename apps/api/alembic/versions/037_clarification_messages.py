"""Allow clarification messages without weakening primary message uniqueness."""

import sqlalchemy as sa

from alembic import op

revision = "inbox037"
down_revision = "inbox036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "message_kind", sa.String(24), nullable=False, server_default="primary"
        ),
    )
    op.drop_index("ix_messages_run_id_role", table_name="messages")
    op.create_index(
        "ix_messages_run_id_role",
        "messages",
        ["run_id", "role"],
        unique=True,
        sqlite_where=sa.text("run_id IS NOT NULL AND message_kind = 'primary'"),
        postgresql_where=sa.text("run_id IS NOT NULL AND message_kind = 'primary'"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM messages WHERE message_kind != 'primary' LIMIT 1")
    ).first():
        raise RuntimeError(
            "cannot downgrade: clarification messages would lose their classification"
        )
    op.drop_index("ix_messages_run_id_role", table_name="messages")
    op.create_index(
        "ix_messages_run_id_role",
        "messages",
        ["run_id", "role"],
        unique=True,
        sqlite_where=sa.text("run_id IS NOT NULL"),
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.drop_column("messages", "message_kind")
