"""Persist normalized model/tool transcripts and generation reservations.

Revision ID: phase4b007
Revises: phase4a006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase4b007"
down_revision: str | None = "phase4a006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _protected_transcript_data(bind) -> int:
    """Count durable 4B semantics that a downgrade would silently erase."""
    count = bind.execute(sa.text("SELECT COUNT(*) FROM run_transcript_entries")).scalar_one()
    count += bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM provider_attempts WHERE "
            "model_turn_ordinal IS NOT NULL OR reserved_input_tokens IS NOT NULL OR "
            "reserved_output_tokens IS NOT NULL OR reserved_total_tokens IS NOT NULL"
        )
    ).scalar_one()
    count += bind.execute(
        sa.text("SELECT COUNT(*) FROM runs WHERE submission_digest IS NOT NULL OR graph_version > 1")
    ).scalar_one()
    return int(count)


def upgrade() -> None:
    if not _has_column("runs", "submission_digest"):
        op.add_column("runs", sa.Column("submission_digest", sa.String(64), nullable=True))

    for name in (
        "model_turn_ordinal",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_total_tokens",
    ):
        if not _has_column("provider_attempts", name):
            op.add_column("provider_attempts", sa.Column(name, sa.Integer(), nullable=True))

    with op.batch_alter_table("provider_attempts") as batch:
        batch.create_check_constraint(
            "ck_provider_attempts_model_turn_ordinal",
            "model_turn_ordinal IS NULL OR model_turn_ordinal >= 0",
        )
        for field in ("reserved_input_tokens", "reserved_output_tokens", "reserved_total_tokens"):
            batch.create_check_constraint(
                f"ck_provider_attempts_{field}", f"{field} IS NULL OR {field} >= 0"
            )

    op.create_table(
        "run_transcript_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("model_turn_ordinal", sa.Integer(), nullable=False),
        sa.Column("message_json", sa.Text(), nullable=False),
        sa.Column("logical_call_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("logical_call_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_run_transcript_run_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_run_transcript_ordinal"),
        sa.CheckConstraint("model_turn_ordinal >= 0", name="ck_run_transcript_model_turn"),
    )
    op.create_index(
        "ix_run_transcript_run_turn",
        "run_transcript_entries",
        ["run_id", "model_turn_ordinal", "ordinal"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    protected = _protected_transcript_data(bind)
    if protected:
        raise ValueError(
            "refusing to downgrade: Phase 4B contains transcript entries, "
            f"tool-enabled requests, or generation reservation metadata ({protected} records). "
            "Keep this revision or explicitly remove the protected data first."
        )

    op.drop_index("ix_run_transcript_run_turn", table_name="run_transcript_entries")
    op.drop_table("run_transcript_entries")
    with op.batch_alter_table("provider_attempts") as batch:
        batch.drop_constraint("ck_provider_attempts_model_turn_ordinal", type_="check")
        for field in ("reserved_input_tokens", "reserved_output_tokens", "reserved_total_tokens"):
            batch.drop_constraint(f"ck_provider_attempts_{field}", type_="check")
            batch.drop_column(field)
        batch.drop_column("model_turn_ordinal")
    op.drop_column("runs", "submission_digest")
