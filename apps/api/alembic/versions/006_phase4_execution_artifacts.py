"""Add model-free tool execution, shared slots, artifacts and quotas.

Revision ID: phase4a006
Revises: hij012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase4a006"
down_revision: str | None = "hij012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _preflight_request_ids(bind) -> None:
    duplicate = bind.execute(
        sa.text(
            "SELECT client_request_id, COUNT(*) AS records FROM runs "
            "WHERE client_request_id IS NOT NULL "
            "GROUP BY client_request_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate:
        raise RuntimeError(
            "Phase 4 migration stopped: duplicate non-null runs.client_request_id "
            f"({duplicate.client_request_id}, {duplicate.records} records). Repair the "
            "duplicate request IDs before upgrading; no tool history was created."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_request_ids(bind)

    run_columns = {
        "kind": sa.Column("kind", sa.String(20), nullable=False, server_default="chat"),
        "graph_version": sa.Column("graph_version", sa.Integer(), nullable=False, server_default="1"),
        "execution_configuration": sa.Column("execution_configuration", sa.Text(), nullable=True),
        "execution_started_at": sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        "execution_deadline_at": sa.Column("execution_deadline_at", sa.DateTime(timezone=True), nullable=True),
        "command_digest": sa.Column("command_digest", sa.String(64), nullable=True),
        "tool_command_json": sa.Column("tool_command_json", sa.Text(), nullable=True),
    }
    for name, column in run_columns.items():
        if not _has_column("runs", name):
            op.add_column("runs", column)
    with op.batch_alter_table("runs") as batch:
        batch.create_check_constraint("ck_runs_kind", "kind IN ('chat', 'tool')")
        batch.create_check_constraint("ck_runs_graph_version", "graph_version >= 1")

    indexes = {item["name"]: item for item in sa.inspect(bind).get_indexes("runs")}
    request_index = indexes.get("ix_runs_client_request_id")
    if request_index is not None and not request_index.get("unique"):
        op.drop_index("ix_runs_client_request_id", table_name="runs")
        request_index = None
    if request_index is None:
        op.create_index(
            "ix_runs_client_request_id",
            "runs",
            ["client_request_id"],
            unique=True,
            sqlite_where=sa.text("client_request_id IS NOT NULL"),
            postgresql_where=sa.text("client_request_id IS NOT NULL"),
        )

    op.create_table(
        "tool_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("logical_call_id", sa.String(128), nullable=False),
        sa.Column("capability_id", sa.String(64), nullable=False),
        sa.Column("capability_version", sa.String(32), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("input_artifact_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("credential_binding_ref", sa.String(128), nullable=True),
        sa.Column("configuration_revision", sa.String(64), nullable=False),
        sa.Column("execution_bundle_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="admitted"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("output_artifact_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["principal_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "logical_call_id", name="uq_tool_operations_run_logical_call"),
        sa.CheckConstraint(
            "status IN ('admitted', 'dispatching', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_operations_status",
        ),
    )
    op.create_index("ix_tool_operations_conversation_created", "tool_operations", ["conversation_id", "created_at"])
    op.create_index("ix_tool_operations_run_id", "tool_operations", ["run_id"])

    op.create_table(
        "tool_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_digest", sa.String(64), nullable=True),
        sa.Column("response_digest", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False, server_default="started"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["operation_id"], ["tool_operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "ordinal", name="uq_tool_attempts_operation_ordinal"),
        sa.UniqueConstraint("operation_id", name="uq_tool_attempts_one_physical_attempt"),
        sa.CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_attempts_outcome",
        ),
    )
    op.create_index("ix_tool_attempts_operation_id", "tool_attempts", ["operation_id"])

    op.create_table(
        "execution_slots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("fence", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["operation_id"], ["tool_operations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slot_number", name="uq_execution_slots_slot_number"),
        sa.CheckConstraint("slot_number >= 0", name="ck_execution_slots_number"),
        sa.CheckConstraint(
            "(operation_id IS NULL AND run_id IS NULL AND fence IS NULL AND owner_id IS NULL AND lease_until IS NULL) "
            "OR (operation_id IS NOT NULL AND run_id IS NOT NULL AND fence IS NOT NULL AND owner_id IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_execution_slots_lease_shape",
        ),
    )
    op.create_index("ix_execution_slots_lease_until", "execution_slots", ["lease_until"])
    op.create_index(
        "uq_execution_slots_operation",
        "execution_slots",
        ["operation_id"],
        unique=True,
        sqlite_where=sa.text("operation_id IS NOT NULL"),
        postgresql_where=sa.text("operation_id IS NOT NULL"),
    )

    op.create_table(
        "tool_availability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_id", "worker_id", name="uq_tool_availability_capability_worker"),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('missing_credentials', 'disabled', 'runtime_unavailable', 'configuration_error')",
            name="ck_tool_availability_reason",
        ),
    )
    op.create_index("ix_tool_availability_observed_at", "tool_availability", ["observed_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("originating_run_id", sa.Uuid(), nullable=True),
        sa.Column("originating_operation_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("media_type", sa.String(127), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("relative_storage_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="staging"),
        sa.Column("mutability", sa.String(16), nullable=False, server_default="immutable"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("provenance", sa.String(2048), nullable=True),
        sa.Column("request_key", sa.String(255), nullable=True),
        sa.Column("reserved_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["originating_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["originating_operation_id"], ["tool_operations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_storage_key", name="uq_artifacts_storage_key"),
        sa.CheckConstraint("kind IN ('search_result', 'upload')", name="ck_artifacts_kind"),
        sa.CheckConstraint("media_type IN ('application/json', 'text/plain')", name="ck_artifacts_media_type"),
        sa.CheckConstraint("size_bytes >= 0 AND reserved_bytes >= 0", name="ck_artifacts_size"),
        sa.CheckConstraint("status IN ('staging', 'ready', 'deleting', 'deleted', 'unavailable')", name="ck_artifacts_status"),
        sa.CheckConstraint("mutability = 'immutable'", name="ck_artifacts_mutability"),
    )
    op.create_index("ix_artifacts_conversation_created", "artifacts", ["conversation_id", "created_at", "id"])
    op.create_index("ix_artifacts_project_status", "artifacts", ["project_id", "status"])
    op.create_index(
        "uq_artifacts_conversation_request_key",
        "artifacts",
        ["conversation_id", "request_key"],
        unique=True,
        sqlite_where=sa.text("request_key IS NOT NULL"),
        postgresql_where=sa.text("request_key IS NOT NULL"),
    )

    op.create_table(
        "artifact_quotas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("committed_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("committed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("scope IN ('project', 'conversation')", name="ck_artifact_quotas_scope"),
        sa.CheckConstraint(
            "(scope = 'project' AND conversation_id IS NULL) OR "
            "(scope = 'conversation' AND conversation_id IS NOT NULL)",
            name="ck_artifact_quotas_owner_shape",
        ),
        sa.CheckConstraint(
            "committed_bytes >= 0 AND reserved_bytes >= 0 AND committed_count >= 0 AND reserved_count >= 0",
            name="ck_artifact_quotas_counters",
        ),
    )
    op.create_index(
        "uq_artifact_quotas_project_scope", "artifact_quotas", ["project_id"], unique=True,
        sqlite_where=sa.text("scope = 'project'"), postgresql_where=sa.text("scope = 'project'"),
    )
    op.create_index(
        "uq_artifact_quotas_conversation_scope", "artifact_quotas", ["conversation_id"], unique=True,
        sqlite_where=sa.text("scope = 'conversation'"), postgresql_where=sa.text("scope = 'conversation'"),
    )


def _protected_records(bind) -> int:
    total = 0
    for table in ("tool_operations", "tool_attempts", "tool_availability", "artifacts"):
        total += int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    total += int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM runs WHERE kind = 'tool' OR execution_configuration IS NOT NULL")
        ).scalar()
        or 0
    )
    total += int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM artifact_quotas WHERE committed_bytes > 0 OR reserved_bytes > 0 OR committed_count > 0 OR reserved_count > 0")
        ).scalar()
        or 0
    )
    return total


def downgrade() -> None:
    bind = op.get_bind()
    protected = _protected_records(bind)
    if protected:
        raise RuntimeError(
            "refusing to downgrade Phase 4 execution: "
            f"{protected} protected tool/artifact records exist; export or explicitly remove them first"
        )

    op.drop_index("uq_artifact_quotas_conversation_scope", table_name="artifact_quotas")
    op.drop_index("uq_artifact_quotas_project_scope", table_name="artifact_quotas")
    op.drop_table("artifact_quotas")
    op.drop_index("uq_artifacts_conversation_request_key", table_name="artifacts")
    op.drop_index("ix_artifacts_project_status", table_name="artifacts")
    op.drop_index("ix_artifacts_conversation_created", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_tool_availability_observed_at", table_name="tool_availability")
    op.drop_table("tool_availability")
    op.drop_index("ix_execution_slots_lease_until", table_name="execution_slots")
    op.drop_index("uq_execution_slots_operation", table_name="execution_slots")
    op.drop_table("execution_slots")
    op.drop_index("ix_tool_attempts_operation_id", table_name="tool_attempts")
    op.drop_table("tool_attempts")
    op.drop_index("ix_tool_operations_run_id", table_name="tool_operations")
    op.drop_index("ix_tool_operations_conversation_created", table_name="tool_operations")
    op.drop_table("tool_operations")

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_graph_version", type_="check")
        batch.drop_constraint("ck_runs_kind", type_="check")
        for column in (
            "command_digest",
            "tool_command_json",
            "execution_deadline_at",
            "execution_started_at",
            "execution_configuration",
            "graph_version",
            "kind",
        ):
            batch.drop_column(column)
