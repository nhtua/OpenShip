"""Persist resumable Phase 6A migration cursors.

Revision ID: phase6a020
Revises: phase6a019
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a020"
down_revision: str | None = "phase6a019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "messaging_cutovers",
        sa.Column("cutover_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("transport_epoch", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("last_operator", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="applying", nullable=False
        ),
        sa.Column(
            "phase", sa.String(length=16), server_default="events", nullable=False
        ),
        sa.Column("rollback_epoch", sa.Uuid(), nullable=True),
        sa.Column("last_event_id", sa.Uuid(), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_last_event_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_last_run_id", sa.Uuid(), nullable=True),
        sa.Column("events_scanned", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "events_backfilled", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("runs_scanned", sa.Integer(), server_default="0", nullable=False),
        sa.Column("commands_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("commands_reused", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "rollback_events_scanned", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "rollback_events_requeued", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "rollback_runs_scanned", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "rollback_generations_bumped",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("execution_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admission_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admission_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('applying', 'complete', 'rolling_back', 'rolled_back')",
            name="ck_messaging_cutover_status",
        ),
        sa.CheckConstraint(
            "phase IN ('events', 'runs', 'complete', 'rollback_events', "
            "'rollback_runs', 'rolled_back')",
            name="ck_messaging_cutover_phase",
        ),
        sa.CheckConstraint(
            "length(trim(requested_by)) BETWEEN 1 AND 255 AND "
            "length(trim(last_operator)) BETWEEN 1 AND 255",
            name="ck_messaging_cutover_operator",
        ),
        sa.CheckConstraint(
            "events_scanned >= 0 AND events_backfilled >= 0 AND runs_scanned >= 0 "
            "AND commands_created >= 0 AND commands_reused >= 0 "
            "AND rollback_events_scanned >= 0 AND rollback_events_requeued >= 0 "
            "AND rollback_runs_scanned >= 0 "
            "AND rollback_generations_bumped >= 0",
            name="ck_messaging_cutover_counts",
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND phase = 'complete' AND completed_at IS NOT NULL "
            "AND rolled_back_at IS NULL) OR "
            "(status = 'applying' AND completed_at IS NULL AND rolled_back_at IS NULL) OR "
            "(status = 'rolling_back' AND phase IN ('rollback_events', 'rollback_runs') "
            "AND completed_at IS NOT NULL AND rollback_epoch IS NOT NULL "
            "AND rolled_back_at IS NULL) OR "
            "(status = 'rolled_back' AND phase = 'rolled_back' "
            "AND completed_at IS NOT NULL AND rollback_epoch IS NOT NULL "
            "AND rolled_back_at IS NOT NULL)",
            name="ck_messaging_cutover_completion",
        ),
        sa.PrimaryKeyConstraint("cutover_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    cutovers = bind.execute(
        sa.text("SELECT COUNT(*) FROM messaging_cutovers")
    ).scalar_one()
    if cutovers:
        raise RuntimeError(
            "refusing to downgrade while messaging cutover progress is recorded"
        )
    op.drop_table("messaging_cutovers")
