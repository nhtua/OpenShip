"""Add content-free failure ledger and reference-only outbox scope."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a016"
down_revision: str | None = "phase6a015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("source_transport_epoch", sa.Uuid(), nullable=False),
        sa.Column("source_stream", sa.String(length=64), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_digest", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_stream = 'OS_RUNS'", name="ck_message_failure_stream"
        ),
        sa.CheckConstraint("source_sequence > 0", name="ck_message_failure_sequence"),
        sa.CheckConstraint(
            "length(body_digest) = 64", name="ck_message_failure_digest"
        ),
        sa.CheckConstraint(
            "classification IN ('quarantine', 'reconcile')",
            name="ck_message_failure_classification",
        ),
        sa.CheckConstraint(
            "length(error_code) BETWEEN 1 AND 80",
            name="ck_message_failure_error_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            name="uq_message_failure_source",
        ),
    )
    op.create_index(
        "ix_message_failures_created_at", "message_failures", ["created_at"]
    )

    op.create_table(
        "message_failure_conflicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("source_transport_epoch", sa.Uuid(), nullable=False),
        sa.Column("source_stream", sa.String(length=64), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_digest", sa.String(length=64), nullable=False),
        sa.Column("observed_classification", sa.String(length=20), nullable=False),
        sa.Column("observed_error_code", sa.String(length=80), nullable=False),
        sa.Column("conflict_code", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_stream = 'OS_RUNS'", name="ck_failure_conflict_stream"
        ),
        sa.CheckConstraint("source_sequence > 0", name="ck_failure_conflict_sequence"),
        sa.CheckConstraint(
            "length(observed_digest) = 64", name="ck_failure_conflict_digest"
        ),
        sa.CheckConstraint(
            "observed_classification IN ('quarantine', 'reconcile')",
            name="ck_failure_conflict_classification",
        ),
        sa.CheckConstraint(
            "length(observed_error_code) BETWEEN 1 AND 80",
            name="ck_failure_conflict_error_code",
        ),
        sa.CheckConstraint(
            "conflict_code IN ('source_digest_conflict', 'failure_row_mismatch', 'outbox_missing')",
            name="ck_failure_conflict_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            "observed_digest",
            "observed_classification",
            "observed_error_code",
            name="uq_message_failure_conflict_observation",
        ),
    )
    op.create_index(
        "ix_message_failure_conflicts_created_at",
        "message_failure_conflicts",
        ["created_at"],
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("outbox", recreate="always") as batch:
            batch.alter_column(
                "conversation_id",
                existing_type=sa.Uuid(),
                nullable=True,
            )
            batch.add_column(sa.Column("failure_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_outbox_failure_id_message_failures",
                "message_failures",
                ["failure_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_check_constraint(
                "ck_outbox_failure_scope",
                "(destination = 'failure' AND failure_id IS NOT NULL "
                "AND conversation_id IS NULL AND run_id IS NULL AND event_id IS NULL "
                "AND dispatch_generation IS NULL) OR "
                "(destination <> 'failure' AND failure_id IS NULL "
                "AND conversation_id IS NOT NULL)",
            )
    else:
        op.alter_column(
            "outbox", "conversation_id", existing_type=sa.Uuid(), nullable=True
        )
        op.add_column("outbox", sa.Column("failure_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_outbox_failure_id_message_failures",
            "outbox",
            "message_failures",
            ["failure_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            "ck_outbox_failure_scope",
            "outbox",
            "(destination = 'failure' AND failure_id IS NOT NULL "
            "AND conversation_id IS NULL AND run_id IS NULL AND event_id IS NULL "
            "AND dispatch_generation IS NULL) OR "
            "(destination <> 'failure' AND failure_id IS NULL "
            "AND conversation_id IS NOT NULL)",
        )

    op.create_index(
        "uq_outbox_failure_id",
        "outbox",
        ["failure_id"],
        unique=True,
        postgresql_where=sa.text("destination = 'failure'"),
        sqlite_where=sa.text("destination = 'failure'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    state_active = bind.execute(
        sa.text("SELECT active FROM messaging_state WHERE id = 1")
    ).scalar_one()
    failure_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM message_failures")
    ).scalar_one()
    conflict_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM message_failure_conflicts")
    ).scalar_one()
    if state_active or failure_count or conflict_count:
        raise RuntimeError(
            "refusing to downgrade while messaging is active or failure records exist"
        )

    op.drop_index("uq_outbox_failure_id", table_name="outbox")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("outbox", recreate="always") as batch:
            batch.drop_constraint("ck_outbox_failure_scope", type_="check")
            batch.drop_constraint(
                "fk_outbox_failure_id_message_failures", type_="foreignkey"
            )
            batch.drop_column("failure_id")
            batch.alter_column(
                "conversation_id",
                existing_type=sa.Uuid(),
                nullable=False,
            )
    else:
        op.drop_constraint("ck_outbox_failure_scope", "outbox", type_="check")
        op.drop_constraint(
            "fk_outbox_failure_id_message_failures", "outbox", type_="foreignkey"
        )
        op.drop_column("outbox", "failure_id")
        op.alter_column(
            "outbox", "conversation_id", existing_type=sa.Uuid(), nullable=False
        )

    op.drop_index(
        "ix_message_failure_conflicts_created_at",
        table_name="message_failure_conflicts",
    )
    op.drop_table("message_failure_conflicts")
    op.drop_index("ix_message_failures_created_at", table_name="message_failures")
    op.drop_table("message_failures")
