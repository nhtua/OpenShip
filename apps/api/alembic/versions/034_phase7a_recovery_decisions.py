"""Add durable recovery decision and task progress storage.

Revision ID: phase7a034
Revises: phase7a033
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase7a034"
down_revision: str | None = "phase7a033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def upgrade() -> None:
    with op.batch_alter_table("task_records") as batch:
        batch.add_column(
            sa.Column(
                "skipped_steps_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
        batch.add_column(
            sa.Column(
                "call_bindings_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
    _replace(
        "task_decisions",
        "ck_task_decisions_kind",
        "kind IN ('plan_confirmation', 'clarification', 'configuration', 'recovery')",
    )
    _replace(
        "task_decisions",
        "ck_task_decisions_revision",
        "plan_revision >= 0 AND (kind NOT IN ('plan_confirmation', 'recovery') OR plan_revision >= 1)",
    )
    _replace(
        "task_decision_commands",
        "ck_task_decision_commands_action",
        "action IN ('confirm', 'reject', 'answer', 'resume_configuration', 'abort', 'repeat_step', 'continue_next')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    checks = (
        "SELECT 1 FROM task_records WHERE skipped_steps_json != '[]' OR call_bindings_json != '[]' LIMIT 1",
        "SELECT 1 FROM task_decisions WHERE kind = 'recovery' LIMIT 1",
        "SELECT 1 FROM task_decision_commands WHERE action IN ('abort', 'repeat_step', 'continue_next') LIMIT 1",
    )
    if any(connection.execute(sa.text(query)).first() for query in checks):
        raise RuntimeError("cannot downgrade: recovery data would be lost")
    _replace(
        "task_decision_commands",
        "ck_task_decision_commands_action",
        "action IN ('confirm', 'reject', 'answer', 'resume_configuration')",
    )
    _replace(
        "task_decisions",
        "ck_task_decisions_revision",
        "plan_revision >= 0 AND (kind != 'plan_confirmation' OR plan_revision >= 1)",
    )
    _replace(
        "task_decisions",
        "ck_task_decisions_kind",
        "kind IN ('plan_confirmation', 'clarification', 'configuration')",
    )
    with op.batch_alter_table("task_records") as batch:
        batch.drop_column("call_bindings_json")
        batch.drop_column("skipped_steps_json")
