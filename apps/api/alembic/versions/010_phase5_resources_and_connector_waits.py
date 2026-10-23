"""Add project-owned cloud resources, observations and connector blockers.

Revision ID: phase5a010
Revises: phase5a009f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a010"
down_revision: str | None = "phase5a009f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_composite_reference_indexes()
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ('created', 'queued', 'running', 'waiting_connector', "
            "'completed', 'failed', 'cancelled', 'timed_out', 'stale')",
        )
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_kind",
            "kind IN ('search_result', 'upload', 'cloud_observation')",
        )

    _create_tables()
    _create_integrity_triggers()


def _add_composite_reference_indexes() -> None:
    indexes = (
        ("uq_conversations_project_id", "conversations", ["project_id", "id"]),
        (
            "uq_runs_project_conversation_id",
            "runs",
            ["project_id", "conversation_id", "id"],
        ),
        (
            "uq_tool_operations_project_conversation_id",
            "tool_operations",
            ["project_id", "conversation_id", "id"],
        ),
        (
            "uq_artifacts_project_conversation_id",
            "artifacts",
            ["project_id", "conversation_id", "id"],
        ),
        (
            "uq_connector_revisions_project_connector_revision",
            "connector_revisions",
            ["project_id", "connector_id", "revision"],
        ),
        (
            "uq_connector_revisions_project_connector_revision_credential",
            "connector_revisions",
            ["project_id", "connector_id", "revision", "credential_version_id"],
        ),
    )
    for name, table, columns in indexes:
        op.create_index(name, table, columns, unique=True)


def _create_tables() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 64", name="ck_environments_name_length"),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 255",
            name="ck_environments_display_name_length",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_environments_project_name"),
    )
    op.create_index("uq_environments_project_id", "environments", ["project_id", "id"], unique=True)

    op.create_table(
        "resource_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind = 'aws_s3_bucket'", name="ck_resource_objects_kind"),
        sa.CheckConstraint("length(uri) BETWEEN 1 AND 255", name="ck_resource_objects_uri"),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 255",
            name="ck_resource_objects_display_name",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "uri", name="uq_resource_objects_project_uri"),
    )
    op.create_index("uq_resource_objects_project_id", "resource_objects", ["project_id", "id"], unique=True)

    op.create_table(
        "resource_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("resource_object_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("bucket_name", sa.String(length=63), nullable=False),
        sa.Column("bucket_arn", sa.String(length=255), nullable=False),
        sa.Column("expected_owner", sa.String(length=12), nullable=False),
        sa.Column("management_method", sa.String(length=16), server_default="external", nullable=False),
        sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 1", name="ck_resource_bindings_revision"),
        sa.CheckConstraint(
            "connector_revision >= 1", name="ck_resource_bindings_connector_revision"
        ),
        sa.CheckConstraint("length(account_id) = 12", name="ck_resource_bindings_account_id"),
        sa.CheckConstraint(
            "length(expected_owner) = 12", name="ck_resource_bindings_expected_owner"
        ),
        sa.CheckConstraint(
            "account_id = expected_owner",
            name="ck_resource_bindings_owner_matches_account",
        ),
        sa.CheckConstraint("length(region) BETWEEN 1 AND 64", name="ck_resource_bindings_region"),
        sa.CheckConstraint(
            "length(bucket_name) BETWEEN 3 AND 63",
            name="ck_resource_bindings_bucket_name",
        ),
        sa.CheckConstraint(
            "length(bucket_arn) BETWEEN 1 AND 255",
            name="ck_resource_bindings_bucket_arn",
        ),
        sa.CheckConstraint(
            "management_method IN ('external', 'unknown')",
            name="ck_resource_bindings_management_method",
        ),
        sa.CheckConstraint("state IN ('active', 'retired')", name="ck_resource_bindings_state"),
        sa.CheckConstraint(
            "(state = 'active' AND retired_at IS NULL) OR "
            "(state = 'retired' AND retired_at IS NOT NULL)",
            name="ck_resource_bindings_retired_shape",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "resource_object_id"],
            ["resource_objects.project_id", "resource_objects.id"],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_object",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "environment_id"],
            ["environments.project_id", "environments.id"],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_environment",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "connector_id", "connector_revision"],
            [
                "connector_revisions.project_id",
                "connector_revisions.connector_id",
                "connector_revisions.revision",
            ],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_connector_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_resource_bindings_project_id", "resource_bindings", ["project_id", "id"], unique=True)
    op.create_index(
        "uq_resource_bindings_object_environment_revision",
        "resource_bindings",
        ["project_id", "resource_object_id", "environment_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_resource_bindings_observation_reference",
        "resource_bindings",
        ["project_id", "resource_object_id", "id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_resource_bindings_active_object_environment",
        "resource_bindings",
        ["project_id", "resource_object_id", "environment_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "uq_resource_bindings_active_target",
        "resource_bindings",
        ["project_id", "environment_id", "account_id", "region", "bucket_name"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "ix_resource_bindings_connector",
        "resource_bindings",
        ["project_id", "connector_id", "connector_revision"],
    )

    op.create_table(
        "resource_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("resource_object_id", sa.Uuid(), nullable=False),
        sa.Column("resource_binding_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measurement_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("evidence_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "binding_revision >= 1", name="ck_resource_observations_binding_revision"
        ),
        sa.CheckConstraint("length(source) BETWEEN 1 AND 32", name="ck_resource_observations_source"),
        sa.CheckConstraint(
            "length(coverage_json) <= 16384",
            name="ck_resource_observations_coverage_size",
        ),
        sa.CheckConstraint(
            "outcome IN ('measured', 'no_data', 'partial', 'failed')",
            name="ck_resource_observations_outcome",
        ),
        sa.CheckConstraint(
            "(evidence_artifact_id IS NULL AND evidence_sha256 IS NULL) OR "
            "(evidence_artifact_id IS NOT NULL AND length(evidence_sha256) = 64)",
            name="ck_resource_observations_evidence_shape",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "conversation_id", "run_id"],
            ["runs.project_id", "runs.conversation_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_run",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "conversation_id", "operation_id"],
            ["tool_operations.project_id", "tool_operations.conversation_id", "tool_operations.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_operation",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "resource_object_id"],
            ["resource_objects.project_id", "resource_objects.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_object",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "resource_object_id", "resource_binding_id", "binding_revision"],
            [
                "resource_bindings.project_id",
                "resource_bindings.resource_object_id",
                "resource_bindings.id",
                "resource_bindings.revision",
            ],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_binding",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "conversation_id", "evidence_artifact_id"],
            ["artifacts.project_id", "artifacts.conversation_id", "artifacts.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_conversation_artifact",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_resource_observations_project_id", "resource_observations", ["project_id", "id"], unique=True)
    op.create_index(
        "ix_resource_observations_resource_retrieved",
        "resource_observations",
        ["project_id", "resource_object_id", "retrieved_at", "id"],
    )
    op.create_index(
        "ix_resource_observations_conversation_created",
        "resource_observations",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "connector_blockers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_revision", sa.Integer(), nullable=False),
        sa.Column("credential_version_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="waiting", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("connector_revision >= 1", name="ck_connector_blockers_revision"),
        sa.CheckConstraint("length(configuration_digest) = 64", name="ck_connector_blockers_digest"),
        sa.CheckConstraint(
            "reason IN ('connector_required', 'connector_ambiguous', 'connector_unavailable', "
            "'connector_expired', 'credential_store_unavailable')",
            name="ck_connector_blockers_reason",
        ),
        sa.CheckConstraint(
            "state IN ('waiting', 'resumed', 'cancelled', 'dismissed', 'expired')",
            name="ck_connector_blockers_state",
        ),
        sa.CheckConstraint(
            "(state = 'waiting' AND consumed_at IS NULL AND closed_at IS NULL) OR "
            "(state = 'resumed' AND consumed_at IS NOT NULL AND closed_at IS NOT NULL) OR "
            "(state IN ('cancelled', 'dismissed', 'expired') AND consumed_at IS NULL "
            "AND closed_at IS NOT NULL)",
            name="ck_connector_blockers_closed_shape",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "conversation_id", "run_id"],
            ["runs.project_id", "runs.conversation_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_connector_blockers_project_run",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "connector_id", "connector_revision", "credential_version_id"],
            [
                "connector_revisions.project_id",
                "connector_revisions.connector_id",
                "connector_revisions.revision",
                "connector_revisions.credential_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_connector_blockers_connector_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_connector_blockers_project_request"),
    )
    op.create_index(
        "uq_connector_blockers_waiting_run",
        "connector_blockers",
        ["project_id", "run_id"],
        unique=True,
        postgresql_where=sa.text("state = 'waiting'"),
        sqlite_where=sa.text("state = 'waiting'"),
    )
    op.create_index("ix_connector_blockers_expiry", "connector_blockers", ["state", "expires_at"])


def _create_integrity_triggers() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE FUNCTION openship_resource_binding_guard() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'resource bindings are retained';
                END IF;
                IF OLD.state = 'active' AND NEW.state = 'retired'
                    AND NEW.retired_at IS NOT NULL
                    AND NEW.id = OLD.id
                    AND NEW.project_id = OLD.project_id
                    AND NEW.resource_object_id = OLD.resource_object_id
                    AND NEW.environment_id = OLD.environment_id
                    AND NEW.connector_id = OLD.connector_id
                    AND NEW.connector_revision = OLD.connector_revision
                    AND NEW.revision = OLD.revision
                    AND NEW.account_id = OLD.account_id
                    AND NEW.region = OLD.region
                    AND NEW.bucket_name = OLD.bucket_name
                    AND NEW.bucket_arn = OLD.bucket_arn
                    AND NEW.expected_owner = OLD.expected_owner
                    AND NEW.management_method = OLD.management_method
                    AND NEW.created_by_user_id = OLD.created_by_user_id
                    AND NEW.created_at = OLD.created_at THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'resource bindings are immutable';
            END $$"""
        )
        op.execute(
            """CREATE TRIGGER trg_resource_bindings_immutable
            BEFORE UPDATE OR DELETE ON resource_bindings
            FOR EACH ROW EXECUTE FUNCTION openship_resource_binding_guard()"""
        )
        op.execute(
            """CREATE FUNCTION openship_resource_binding_aws_scope() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM connectors c
                    WHERE c.id = NEW.connector_id AND c.project_id = NEW.project_id
                      AND c.visibility = 'project' AND c.kind = 'aws'
                ) THEN
                    RAISE EXCEPTION 'resource binding requires a project AWS Connector';
                END IF;
                RETURN NEW;
            END $$"""
        )
        op.execute(
            """CREATE TRIGGER trg_resource_bindings_aws_insert
            BEFORE INSERT ON resource_bindings
            FOR EACH ROW EXECUTE FUNCTION openship_resource_binding_aws_scope()"""
        )
        op.execute(
            """CREATE FUNCTION openship_resource_observation_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'resource observations are append-only';
            END $$"""
        )
        op.execute(
            """CREATE TRIGGER trg_resource_observations_append_only
            BEFORE UPDATE OR DELETE ON resource_observations
            FOR EACH ROW EXECUTE FUNCTION openship_resource_observation_append_only()"""
        )
        return

    op.execute(
        """CREATE TRIGGER trg_resource_bindings_immutable_update BEFORE UPDATE ON resource_bindings
        WHEN NOT (
            OLD.state = 'active' AND NEW.state = 'retired' AND NEW.retired_at IS NOT NULL
            AND NEW.id IS OLD.id AND NEW.project_id IS OLD.project_id
            AND NEW.resource_object_id IS OLD.resource_object_id
            AND NEW.environment_id IS OLD.environment_id AND NEW.connector_id IS OLD.connector_id
            AND NEW.connector_revision IS OLD.connector_revision AND NEW.revision IS OLD.revision
            AND NEW.account_id IS OLD.account_id AND NEW.region IS OLD.region
            AND NEW.bucket_name IS OLD.bucket_name AND NEW.bucket_arn IS OLD.bucket_arn
            AND NEW.expected_owner IS OLD.expected_owner
            AND NEW.management_method IS OLD.management_method
            AND NEW.created_by_user_id IS OLD.created_by_user_id
            AND NEW.created_at IS OLD.created_at
        ) BEGIN SELECT RAISE(ABORT, 'resource bindings are immutable'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_resource_bindings_immutable_delete BEFORE DELETE ON resource_bindings
        BEGIN SELECT RAISE(ABORT, 'resource bindings are retained'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_resource_bindings_aws_insert BEFORE INSERT ON resource_bindings
        WHEN NOT EXISTS (
            SELECT 1 FROM connectors c WHERE c.id = NEW.connector_id
              AND c.project_id = NEW.project_id AND c.visibility = 'project' AND c.kind = 'aws'
        ) BEGIN SELECT RAISE(ABORT, 'resource binding requires a project AWS Connector'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_resource_observations_append_only_update BEFORE UPDATE ON resource_observations
        BEGIN SELECT RAISE(ABORT, 'resource observations are append-only'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_resource_observations_append_only_delete BEFORE DELETE ON resource_observations
        BEGIN SELECT RAISE(ABORT, 'resource observations are retained'); END"""
    )


def downgrade() -> None:
    bind = op.get_bind()
    protected = {}
    for table in (
        "environments",
        "resource_objects",
        "resource_bindings",
        "resource_observations",
        "connector_blockers",
    ):
        protected[table] = int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
    protected["cloud_observation artifacts"] = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM artifacts WHERE kind = 'cloud_observation'")
        ).scalar_one()
    )
    protected["waiting_connector runs"] = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM runs WHERE status = 'waiting_connector'")
        ).scalar_one()
    )
    populated = {name: count for name, count in protected.items() if count}
    if populated:
        summary = ", ".join(f"{name}: {count}" for name, count in populated.items())
        raise RuntimeError(
            "refusing to downgrade: Phase 5 resource or connector-wait data would be lost "
            f"({summary})"
        )

    _drop_integrity_triggers()
    for table in (
        "connector_blockers",
        "resource_observations",
        "resource_bindings",
        "resource_objects",
        "environments",
    ):
        op.drop_table(table)

    for name, table in (
        ("uq_artifacts_project_conversation_id", "artifacts"),
        ("uq_tool_operations_project_conversation_id", "tool_operations"),
        ("uq_runs_project_conversation_id", "runs"),
        ("uq_conversations_project_id", "conversations"),
        (
            "uq_connector_revisions_project_connector_revision_credential",
            "connector_revisions",
        ),
        ("uq_connector_revisions_project_connector_revision", "connector_revisions"),
    ):
        op.drop_index(name, table_name=table)

    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_kind", "kind IN ('search_result', 'upload')"
        )
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ('created', 'queued', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out', 'stale')",
        )


def _drop_integrity_triggers() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_resource_bindings_immutable ON resource_bindings")
        op.execute("DROP TRIGGER trg_resource_bindings_aws_insert ON resource_bindings")
        op.execute("DROP TRIGGER trg_resource_observations_append_only ON resource_observations")
        op.execute("DROP FUNCTION openship_resource_binding_guard()")
        op.execute("DROP FUNCTION openship_resource_binding_aws_scope()")
        op.execute("DROP FUNCTION openship_resource_observation_append_only()")
        return

    for name, table in (
        ("trg_resource_bindings_immutable_update", "resource_bindings"),
        ("trg_resource_bindings_immutable_delete", "resource_bindings"),
        ("trg_resource_bindings_aws_insert", "resource_bindings"),
        ("trg_resource_observations_append_only_update", "resource_observations"),
        ("trg_resource_observations_append_only_delete", "resource_observations"),
    ):
        op.execute(f"DROP TRIGGER {name}")
