"""Persist fenced task plans, committed outcomes and durable decisions.

Adds the three nonterminal wait statuses to ``runs`` and the task projection
tables. Historical runs remain untouched: no task row is fabricated for a
run that predates task graph v5.

Revision ID: phase7a031
Revises: phase6b030
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase7a031"
down_revision: str | None = "phase6b030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = (
    "created",
    "queued",
    "running",
    "waiting_connector",
    "waiting_plan",
    "waiting_input",
    "waiting_config",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "stale",
)
WAITING_STATUSES = ("waiting_plan", "waiting_input", "waiting_config")
TASK_TABLES = (
    "task_decision_commands",
    "task_decisions",
    "task_step_attempts",
    "task_plans",
    "task_records",
)


def _status_check(statuses: tuple[str, ...]) -> str:
    return f"status IN ({', '.join(repr(status) for status in statuses)})"


def upgrade() -> None:
    op.create_table(
        "task_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("initiator_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), server_default="conversation", nullable=False),
        sa.Column("mode", sa.String(length=16), server_default="copilot", nullable=False),
        sa.Column("graph_version", sa.Integer(), server_default="5", nullable=False),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=True),
        sa.Column("fence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("context_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("counters_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("accepted_evidence_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("verified_outputs_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("verified_steps_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("last_disposition", sa.String(length=24), server_default="none", nullable=False),
        sa.Column("active_ms", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("active_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiator_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_task_records_run_id"),
        sa.CheckConstraint("graph_version = 5", name="ck_task_records_graph_version"),
        sa.CheckConstraint("current_revision >= 1", name="ck_task_records_revision"),
        sa.CheckConstraint("fence >= 0", name="ck_task_records_fence"),
        sa.CheckConstraint("active_ms >= 0", name="ck_task_records_active_ms"),
        sa.CheckConstraint("scope IN ('conversation', 'project')", name="ck_task_records_scope"),
        sa.CheckConstraint("mode IN ('auto', 'copilot')", name="ck_task_records_mode"),
        sa.CheckConstraint(
            _status_check((
                "unknown",
                "planned",
                "running",
                "waiting",
                "verification_pending",
                "completed",
                "blocked",
                "failed",
                "cancelled",
            )),
            name="ck_task_records_status",
        ),
        sa.CheckConstraint(
            "last_disposition IN ('none', 'known_failure', 'material_revision', "
            "'uncertain', 'denied', 'policy_revoked', 'budget_exhausted')",
            name="ck_task_records_last_disposition",
        ),
    )
    op.create_index("ix_task_records_project_id", "task_records", ["project_id"])
    op.create_index("ix_task_records_conversation_id", "task_records", ["conversation_id"])
    op.create_index("ix_task_records_status", "task_records", ["status"])

    op.create_table(
        "task_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="validated", nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "revision", name="uq_task_plans_run_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_task_plans_revision"),
        sa.CheckConstraint("status IN ('proposed', 'validated')", name="ck_task_plans_status"),
    )
    op.create_index("ix_task_plans_task_id", "task_plans", ["task_id"])

    op.create_table(
        "task_step_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operation_id"], ["tool_operations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "step_id", "ordinal", name="uq_task_step_attempts_run_step_ordinal"
        ),
        sa.UniqueConstraint("run_id", "operation_id", name="uq_task_step_attempts_run_operation"),
        sa.CheckConstraint("plan_revision >= 1", name="ck_task_step_attempts_revision"),
        sa.CheckConstraint("ordinal >= 1 AND ordinal <= 3", name="ck_task_step_attempts_ordinal"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted', 'uncertain')",
            name="ck_task_step_attempts_status",
        ),
    )
    op.create_index("ix_task_step_attempts_task_id", "task_step_attempts", ["task_id"])
    op.create_index("ix_task_step_attempts_run_id", "task_step_attempts", ["run_id"])
    op.create_index(
        "ix_task_step_attempts_operation_id", "task_step_attempts", ["operation_id"]
    )

    op.create_table(
        "task_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("expected_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("request_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "request_id", name="uq_task_decisions_run_request"),
        sa.CheckConstraint("plan_revision >= 1", name="ck_task_decisions_revision"),
        sa.CheckConstraint(
            "kind IN ('plan_confirmation', 'clarification', 'configuration')",
            name="ck_task_decisions_kind",
        ),
        sa.CheckConstraint(
            "state IN ('open', 'confirmed', 'rejected', 'answered', 'expired', 'cancelled')",
            name="ck_task_decisions_state",
        ),
    )
    op.create_index("ix_task_decisions_task_id", "task_decisions", ["task_id"])
    op.create_index("ix_task_decisions_run_id", "task_decisions", ["run_id"])
    op.create_index(
        "uq_task_decisions_open_run",
        "task_decisions",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
        sqlite_where=sa.text("state = 'open'"),
    )

    op.create_table(
        "task_decision_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("expected_digest", sa.String(length=64), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decision_id"], ["task_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "client_request_id",
            name="uq_task_decision_commands_run_client_request",
        ),
        sa.CheckConstraint(
            "action IN ('confirm', 'reject', 'answer', 'resume_configuration')",
            name="ck_task_decision_commands_action",
        ),
        sa.CheckConstraint(
            "plan_revision >= 1", name="ck_task_decision_commands_revision"
        ),
    )
    op.create_index("ix_task_decision_commands_task_id", "task_decision_commands", ["task_id"])
    op.create_index(
        "ix_task_decision_commands_decision_id", "task_decision_commands", ["decision_id"]
    )

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint("ck_runs_status", _status_check(RUN_STATUSES))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "task_records" in tables:
        task_rows = bind.execute(
            sa.text("SELECT COUNT(*) FROM task_records")
        ).scalar_one()
    else:
        task_rows = 0
    v5_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM runs WHERE graph_version >= 5")
    ).scalar_one()
    waiting_rows = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM runs WHERE status IN "
            f"({', '.join(repr(status) for status in WAITING_STATUSES)})"
        )
    ).scalar_one()
    if task_rows or v5_rows or waiting_rows:
        raise RuntimeError(
            "refusing to downgrade while v5 task rows, v5 runs or waiting runs"
            " exist; remove them deliberately before dropping task state"
        )

    for table in TASK_TABLES:
        if table in tables:
            op.drop_table(table)

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            _status_check(tuple(
                status for status in RUN_STATUSES if status not in WAITING_STATUSES
            )),
        )
