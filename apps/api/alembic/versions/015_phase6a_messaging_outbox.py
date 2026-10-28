"""Add transactional messaging records without activating delivery.

Revision ID: phase6a015
Revises: phase5b014
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a015"
down_revision: str | None = "phase5b014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT run_id, COUNT(*) AS job_count FROM run_jobs "
            "GROUP BY run_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "refusing Phase 6A migration: duplicate run_jobs rows exist; "
            f"run {duplicate.run_id} has {duplicate.job_count} jobs"
        )

    op.create_table(
        "messaging_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("transport_epoch", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("admission_open", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("execution_open", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("cutover_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_messaging_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id", name="uq_messaging_state_installation_id"),
    )
    op.create_table(
        "message_dispositions",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("handler_version", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "disposition IN ('claimed', 'completed', 'deferred', 'disposed', 'quarantined')",
            name="ck_message_disposition_kind",
        ),
        sa.CheckConstraint("handler_version >= 1", name="ck_message_handler_version"),
        sa.PrimaryKeyConstraint("message_id"),
    )

    installation_id = uuid.uuid4()
    state = sa.table(
        "messaging_state",
        sa.column("id", sa.Integer()),
        sa.column("installation_id", sa.Uuid()),
        sa.column("transport_epoch", sa.Uuid()),
        sa.column("active", sa.Boolean()),
        sa.column("admission_open", sa.Boolean()),
        sa.column("execution_open", sa.Boolean()),
    )
    op.bulk_insert(
        state,
        [
            {
                "id": 1,
                "installation_id": installation_id,
                "transport_epoch": uuid.uuid4(),
                "active": False,
                "admission_open": True,
                "execution_open": True,
            }
        ],
    )

    op.add_column(
        "run_jobs",
        sa.Column("dispatch_generation", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("uq_run_jobs_run_id", "run_jobs", ["run_id"], unique=True)

    with op.batch_alter_table("outbox") as batch:
        batch.add_column(sa.Column("destination", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("schema_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("message_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("subject", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("dispatch_generation", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("transport_epoch", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("publish_attempts", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("publisher_claim_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("publisher_lease_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_error", sa.String(length=160), nullable=True))
        batch.create_foreign_key(
            "fk_outbox_run_id_runs", "runs", ["run_id"], ["id"], ondelete="CASCADE"
        )

    outbox = sa.table(
        "outbox",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("destination", sa.String()),
        sa.column("schema_version", sa.Integer()),
        sa.column("message_id", sa.Uuid()),
        sa.column("publish_attempts", sa.Integer()),
        sa.column("next_attempt_at", sa.DateTime(timezone=True)),
    )
    for (row_id, created_at) in bind.execute(
        sa.select(outbox.c.id, outbox.c.created_at)
    ).all():
        bind.execute(
            outbox.update()
            .where(outbox.c.id == row_id)
            .values(
                destination="legacy",
                schema_version=1,
                message_id=uuid.uuid4(),
                publish_attempts=0,
                next_attempt_at=created_at,
            )
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("outbox", recreate="always") as batch:
            batch.alter_column(
                "destination", existing_type=sa.String(length=20), nullable=False,
                server_default="legacy",
            )
            batch.alter_column(
                "schema_version", existing_type=sa.Integer(), nullable=False,
                server_default="1",
            )
            batch.alter_column("message_id", existing_type=sa.Uuid(), nullable=False)
            batch.alter_column(
                "publish_attempts", existing_type=sa.Integer(), nullable=False,
                server_default="0",
            )
    else:
        op.alter_column(
            "outbox", "destination", existing_type=sa.String(length=20), nullable=False,
            server_default="legacy",
        )
        op.alter_column(
            "outbox", "schema_version", existing_type=sa.Integer(), nullable=False,
            server_default="1",
        )
        op.alter_column("outbox", "message_id", existing_type=sa.Uuid(), nullable=False)
        op.alter_column(
            "outbox", "publish_attempts", existing_type=sa.Integer(), nullable=False,
            server_default="0",
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("outbox", recreate="always") as batch:
            batch.create_check_constraint(
                "ck_outbox_destination",
                "destination IN ('event', 'command', 'control', 'legacy', 'failure')",
            )
            batch.create_check_constraint(
                "ck_outbox_schema_version", "schema_version >= 1"
            )
            batch.create_check_constraint(
                "ck_outbox_publish_attempts", "publish_attempts >= 0"
            )
    else:
        op.create_check_constraint(
            "ck_outbox_destination",
            "outbox",
            "destination IN ('event', 'command', 'control', 'legacy', 'failure')",
        )
        op.create_check_constraint("ck_outbox_schema_version", "outbox", "schema_version >= 1")
        op.create_check_constraint("ck_outbox_publish_attempts", "outbox", "publish_attempts >= 0")
    op.create_index("uq_outbox_message_id", "outbox", ["message_id"], unique=True)
    op.create_index(
        "ix_outbox_publish_due",
        "outbox",
        ["destination", "processed_at", "next_attempt_at", "publisher_lease_until"],
    )
    op.create_index(
        "uq_outbox_event_destination",
        "outbox",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("destination = 'event'"),
        sqlite_where=sa.text("destination = 'event'"),
    )
    op.create_index(
        "uq_outbox_run_generation",
        "outbox",
        ["run_id", "dispatch_generation"],
        unique=True,
        postgresql_where=sa.text("destination = 'command'"),
        sqlite_where=sa.text("destination = 'command'"),
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            """CREATE OR REPLACE FUNCTION openship_notify_outbox() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_notify('openship_outbox', '');
                RETURN NEW;
            END;
            $$"""
        )
        op.execute(
            """CREATE TRIGGER trg_outbox_notify AFTER INSERT ON outbox
            FOR EACH ROW EXECUTE FUNCTION openship_notify_outbox()"""
        )


def downgrade() -> None:
    bind = op.get_bind()
    active = bind.execute(
        sa.text("SELECT active FROM messaging_state WHERE id = 1")
    ).scalar_one()
    if active:
        raise RuntimeError("refusing to downgrade while JetStream transport is active")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_outbox_notify ON outbox")
        op.execute("DROP FUNCTION IF EXISTS openship_notify_outbox()")
    op.drop_index("uq_outbox_run_generation", table_name="outbox")
    op.drop_index("uq_outbox_event_destination", table_name="outbox")
    op.drop_index("ix_outbox_publish_due", table_name="outbox")
    op.drop_index("uq_outbox_message_id", table_name="outbox")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("outbox", recreate="always") as batch:
            batch.drop_constraint("ck_outbox_publish_attempts", type_="check")
            batch.drop_constraint("ck_outbox_schema_version", type_="check")
            batch.drop_constraint("ck_outbox_destination", type_="check")
            batch.drop_constraint("fk_outbox_run_id_runs", type_="foreignkey")
            for name in (
                "last_error",
                "publisher_lease_until",
                "publisher_claim_id",
                "next_attempt_at",
                "publish_attempts",
                "transport_epoch",
                "dispatch_generation",
                "run_id",
                "subject",
                "message_id",
                "schema_version",
                "destination",
            ):
                batch.drop_column(name)
    else:
        op.drop_constraint("ck_outbox_publish_attempts", "outbox", type_="check")
        op.drop_constraint("ck_outbox_schema_version", "outbox", type_="check")
        op.drop_constraint("ck_outbox_destination", "outbox", type_="check")
        with op.batch_alter_table("outbox") as batch:
            batch.drop_constraint("fk_outbox_run_id_runs", type_="foreignkey")
            for name in (
                "last_error",
                "publisher_lease_until",
                "publisher_claim_id",
                "next_attempt_at",
                "publish_attempts",
                "transport_epoch",
                "dispatch_generation",
                "run_id",
                "subject",
                "message_id",
                "schema_version",
                "destination",
            ):
                batch.drop_column(name)
    op.drop_index("uq_run_jobs_run_id", table_name="run_jobs")
    op.drop_column("run_jobs", "dispatch_generation")
    op.drop_table("message_dispositions")
    op.drop_table("messaging_state")
