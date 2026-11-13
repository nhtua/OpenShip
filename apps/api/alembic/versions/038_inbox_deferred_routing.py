"""Allow inbox messages to await their routing decision."""

import sqlalchemy as sa

from alembic import op

revision = "inbox038"
down_revision = "inbox037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_inbox") as batch:
        batch.alter_column("disposition", existing_type=sa.String(24), nullable=True)
        batch.drop_constraint("ck_inbox_status", type_="check")
        batch.create_check_constraint(
            "ck_inbox_status",
            "status IN ('awaiting', 'attached', 'queued', 'promoted', 'failed')",
        )


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM conversation_inbox WHERE disposition IS NULL OR status = 'awaiting' LIMIT 1")
    ).first():
        raise RuntimeError("cannot downgrade: inbox contains unassigned messages")
    with op.batch_alter_table("conversation_inbox") as batch:
        batch.alter_column("disposition", existing_type=sa.String(24), nullable=False)
        batch.drop_constraint("ck_inbox_status", type_="check")
        batch.create_check_constraint(
            "ck_inbox_status", "status IN ('attached', 'queued', 'promoted', 'failed')"
        )
