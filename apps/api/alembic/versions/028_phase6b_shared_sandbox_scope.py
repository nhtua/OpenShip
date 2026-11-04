"""Share a reusable sandbox pointer across runs in the current conversation scope.

Revision ID: phase6b028
Revises: phase6b027
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6b028"
down_revision: str | None = "phase6b027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Old per-run pointers can coexist in one conversation. Keep the most
    # recently updated row as the single authoritative scope pointer; dropping
    # other DB pointers never calls or claims to clean up provider sandboxes.
    bind.execute(
        sa.text(
            "DELETE FROM workflow_sandboxes WHERE id IN ("
            "SELECT id FROM ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY project_id, conversation_id "
            "ORDER BY updated_at DESC, created_at DESC, id DESC) AS position "
            "FROM workflow_sandboxes) ranked WHERE position > 1)"
        )
    )
    with op.batch_alter_table("workflow_sandboxes") as batch:
        batch.drop_constraint("uq_workflow_sandboxes_root_run", type_="unique")
        batch.add_column(sa.Column("requested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("intent_id", sa.Uuid()))
        batch.add_column(
            sa.Column("command_uncertain_until", sa.DateTime(timezone=True))
        )
        batch.add_column(sa.Column("command_operation_id", sa.Uuid()))
    rows = bind.execute(
        sa.text(
            "SELECT id, updated_at FROM workflow_sandboxes WHERE status = 'requested'"
        )
    ).all()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE workflow_sandboxes SET requested_at = :requested_at, "
                "intent_id = :intent_id WHERE id = :id"
            ),
            {
                "requested_at": row.updated_at,
                "intent_id": str(uuid.uuid4()),
                "id": row.id,
            },
        )
    with op.batch_alter_table("workflow_sandboxes") as batch:
        batch.drop_constraint("ck_workflow_sandbox_state", type_="check")
        batch.create_unique_constraint(
            "uq_workflow_sandboxes_scope", ["project_id", "conversation_id"]
        )
        batch.create_check_constraint(
            "ck_workflow_sandbox_state",
            "(status = 'requested' AND sandbox_id IS NULL AND expires_at IS NULL "
            "AND requested_at IS NOT NULL AND intent_id IS NOT NULL "
            "AND command_uncertain_until IS NULL AND command_operation_id IS NULL) OR "
            "(status = 'active' AND sandbox_id IS NOT NULL AND expires_at IS NOT NULL "
            "AND requested_at IS NULL AND intent_id IS NULL "
            "AND ((command_uncertain_until IS NULL AND command_operation_id IS NULL) "
            "OR (command_uncertain_until IS NOT NULL AND command_operation_id IS NOT NULL)))",
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM workflow_sandboxes")
    ).scalar_one()
    if rows:
        raise RuntimeError(
            "refusing downgrade while provider sandbox intents/pointers exist; "
            "OpenSandbox still owns expiration and cleanup"
        )
    with op.batch_alter_table("workflow_sandboxes") as batch:
        batch.drop_constraint("ck_workflow_sandbox_state", type_="check")
        batch.drop_constraint("uq_workflow_sandboxes_scope", type_="unique")
        batch.drop_column("command_operation_id")
        batch.drop_column("command_uncertain_until")
        batch.drop_column("intent_id")
        batch.drop_column("requested_at")
        batch.create_unique_constraint(
            "uq_workflow_sandboxes_root_run", ["root_run_id"]
        )
        batch.create_check_constraint(
            "ck_workflow_sandbox_state",
            "(status = 'requested' AND sandbox_id IS NULL AND expires_at IS NULL) "
            "OR (status = 'active' AND sandbox_id IS NOT NULL AND expires_at IS NOT NULL)",
        )
