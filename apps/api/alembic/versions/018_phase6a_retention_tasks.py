"""Add explicitly requested durable retention maintenance tasks.

Revision ID: phase6a018
Revises: phase6a017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a018"
down_revision: str | None = "phase6a017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retention_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("event_retention_days", sa.Integer(), nullable=False),
        sa.Column("checkpoint_retention_days", sa.Integer(), nullable=False),
        sa.Column("messaging_retention_days", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="requested", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumer_acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumer_stream_sequence", sa.BigInteger(), nullable=True),
        sa.Column("consumer_sequence", sa.BigInteger(), nullable=True),
        sa.Column("ack_pending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "length(trim(requested_by)) BETWEEN 1 AND 255",
            name="ck_retention_requested_by",
        ),
        sa.CheckConstraint(
            "event_retention_days BETWEEN 1 AND 36500", name="ck_retention_event_days"
        ),
        sa.CheckConstraint(
            "checkpoint_retention_days BETWEEN 1 AND 36500",
            name="ck_retention_checkpoint_days",
        ),
        sa.CheckConstraint(
            "messaging_retention_days BETWEEN 1 AND 36500",
            name="ck_retention_messaging_days",
        ),
        sa.CheckConstraint(
            "batch_size BETWEEN 1 AND 10000", name="ck_retention_batch_size"
        ),
        sa.CheckConstraint(
            "state IN ('requested', 'running', 'completed', 'failed')",
            name="ck_retention_task_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_retention_task_attempts"),
        sa.CheckConstraint(
            "state = 'failed' OR error_code IS NULL", name="ck_retention_task_error"
        ),
        sa.CheckConstraint(
            "(consumer_stream_sequence IS NULL AND consumer_sequence IS NULL "
            "AND ack_pending_at IS NULL AND consumer_acked_at IS NULL) OR "
            "(consumer_stream_sequence > 0 AND consumer_sequence > 0 "
            "AND ack_pending_at IS NOT NULL)",
            name="ck_retention_task_ack_intent",
        ),
        sa.CheckConstraint(
            "consumer_acked_at IS NULL OR state IN ('completed', 'failed')",
            name="ck_retention_task_ack_state",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retention_task_ack_reconcile",
        "retention_tasks",
        ["consumer_acked_at", "consumer_sequence", "consumer_stream_sequence"],
        postgresql_where=sa.text("consumer_acked_at IS NULL"),
        sqlite_where=sa.text("consumer_acked_at IS NULL"),
    )

    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_destination", type_="check")
        batch.drop_constraint("ck_outbox_failure_scope", type_="check")
        batch.add_column(sa.Column("maintenance_task_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_outbox_maintenance_task_id_retention_tasks",
            "retention_tasks",
            ["maintenance_task_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_outbox_destination",
            "destination IN ('event', 'command', 'control', 'legacy', 'failure', 'maintenance')",
        )
        batch.create_check_constraint(
            "ck_outbox_failure_scope",
            "(destination = 'failure' AND failure_id IS NOT NULL "
            "AND conversation_id IS NULL AND run_id IS NULL AND event_id IS NULL "
            "AND dispatch_generation IS NULL AND maintenance_task_id IS NULL) OR "
            "(destination = 'maintenance' AND maintenance_task_id IS NOT NULL "
            "AND failure_id IS NULL AND conversation_id IS NULL AND run_id IS NULL "
            "AND event_id IS NULL AND dispatch_generation IS NULL) OR "
            "(destination NOT IN ('failure', 'maintenance') AND failure_id IS NULL "
            "AND maintenance_task_id IS NULL AND conversation_id IS NOT NULL)",
        )

    op.create_index(
        "uq_outbox_maintenance_task_id",
        "outbox",
        ["maintenance_task_id"],
        unique=True,
        postgresql_where=sa.text("destination = 'maintenance'"),
        sqlite_where=sa.text("destination = 'maintenance'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    task_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM retention_tasks")
    ).scalar_one()
    if task_count:
        raise RuntimeError(
            "refusing Phase 6A downgrade while retention task history exists"
        )

    op.drop_index("uq_outbox_maintenance_task_id", table_name="outbox")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_failure_scope", type_="check")
        batch.drop_constraint("ck_outbox_destination", type_="check")
        batch.drop_constraint(
            "fk_outbox_maintenance_task_id_retention_tasks", type_="foreignkey"
        )
        batch.drop_column("maintenance_task_id")
        batch.create_check_constraint(
            "ck_outbox_destination",
            "destination IN ('event', 'command', 'control', 'legacy', 'failure')",
        )
        batch.create_check_constraint(
            "ck_outbox_failure_scope",
            "(destination = 'failure' AND failure_id IS NOT NULL "
            "AND conversation_id IS NULL AND run_id IS NULL AND event_id IS NULL "
            "AND dispatch_generation IS NULL) OR "
            "(destination <> 'failure' AND failure_id IS NULL "
            "AND conversation_id IS NOT NULL)",
        )
    op.drop_index("ix_retention_task_ack_reconcile", table_name="retention_tasks")
    op.drop_table("retention_tasks")
