"""Persist JetStream MaxDeliver observations and live-owner checks."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a017"
down_revision: str | None = "phase6a016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_exhaustions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("source_transport_epoch", sa.Uuid(), nullable=False),
        sa.Column("source_stream", sa.String(length=64), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("dispatch_generation", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.String(length=50), nullable=True),
        sa.Column("owner_fence", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
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
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_stream = 'OS_RUNS'", name="ck_exhaustion_stream"),
        sa.CheckConstraint("source_sequence > 0", name="ck_exhaustion_sequence"),
        sa.CheckConstraint("consumer_name = 'run-v1'", name="ck_exhaustion_consumer"),
        sa.CheckConstraint("delivery_count > 0", name="ck_exhaustion_deliveries"),
        sa.CheckConstraint(
            "state IN ('pending', 'watching_live_owner', 'settling', 'resolved', "
            "'source_missing', 'blocked')",
            name="ck_exhaustion_state",
        ),
        sa.CheckConstraint(
            "payload_digest IS NULL OR length(payload_digest) = 64",
            name="ck_exhaustion_digest",
        ),
        sa.CheckConstraint(
            "dispatch_generation IS NULL OR dispatch_generation > 0",
            name="ck_exhaustion_generation",
        ),
        sa.CheckConstraint(
            "owner_fence IS NULL OR owner_fence > 0",
            name="ck_exhaustion_owner_fence",
        ),
        sa.CheckConstraint(
            "state <> 'watching_live_owner' OR "
            "(run_id IS NOT NULL AND dispatch_generation IS NOT NULL "
            "AND owner_id IS NOT NULL AND owner_fence IS NOT NULL "
            "AND lease_until IS NOT NULL AND next_check_at IS NOT NULL)",
            name="ck_exhaustion_live_owner_fields",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            name="uq_message_exhaustion_source",
        ),
    )
    op.create_index(
        "ix_message_exhaustions_due",
        "message_exhaustions",
        ["state", "next_check_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    state_active = bind.execute(
        sa.text("SELECT active FROM messaging_state WHERE id = 1")
    ).scalar_one()
    observation_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM message_exhaustions")
    ).scalar_one()
    if state_active or observation_count:
        raise RuntimeError(
            "refusing to downgrade while messaging is active or exhaustion observations exist"
        )

    op.drop_index("ix_message_exhaustions_due", table_name="message_exhaustions")
    op.drop_table("message_exhaustions")
