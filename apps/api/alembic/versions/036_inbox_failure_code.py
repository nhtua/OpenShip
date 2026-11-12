"""Record a safe reason when queued work cannot be promoted."""

import sqlalchemy as sa

from alembic import op

revision = "inbox036"
down_revision = "inbox035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_inbox", sa.Column("failure_code", sa.String(100), nullable=True)
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM conversation_inbox WHERE failure_code IS NOT NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError("cannot downgrade: inbox failure reasons would be lost")
    op.drop_column("conversation_inbox", "failure_code")
