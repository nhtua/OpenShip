"""Allow revision zero for pre-plan task decisions.

Revision ID: phase7a033
Revises: phase7a032
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase7a033"
down_revision: str | None = "phase7a032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def upgrade() -> None:
    _replace("task_records", "ck_task_records_revision", "current_revision >= 0")
    _replace(
        "task_decisions",
        "ck_task_decisions_revision",
        "plan_revision >= 0 AND (kind != 'plan_confirmation' OR plan_revision >= 1)",
    )
    _replace(
        "task_decision_commands",
        "ck_task_decision_commands_revision",
        "plan_revision >= 0",
    )


def downgrade() -> None:
    _replace(
        "task_decision_commands",
        "ck_task_decision_commands_revision",
        "plan_revision >= 1",
    )
    _replace("task_decisions", "ck_task_decisions_revision", "plan_revision >= 1")
    _replace("task_records", "ck_task_records_revision", "current_revision >= 1")
