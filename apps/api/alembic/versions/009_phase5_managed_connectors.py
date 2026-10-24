"""Add scoped managed Connectors and encrypted credential-version records.

Revision ID: phase5a009
Revises: phase4c008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a009"
down_revision: str | None = "phase4c008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("managed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('brave', 'tavily', 'aws')", name="ck_connectors_kind"),
        sa.CheckConstraint("visibility IN ('global', 'project')", name="ck_connectors_visibility"),
        sa.CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connectors_owner_shape",
        ),
        sa.CheckConstraint(
            "NOT (kind = 'aws' AND visibility = 'global')",
            name="ck_connectors_aws_project_only",
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 255", name="ck_connectors_name"),
        sa.CheckConstraint("current_revision >= 1", name="ck_connectors_current_revision"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["managed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connectors_project_kind", "connectors", ["project_id", "kind"])
    op.create_index("ix_connectors_visibility_kind", "connectors", ["visibility", "kind"])

    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_credentials_visibility"),
        sa.CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_credentials_owner_shape",
        ),
        sa.CheckConstraint(
            "kind IN ('brave_api_key', 'tavily_api_key', 'aws_bootstrap')",
            name="ck_connector_credentials_kind",
        ),
        sa.CheckConstraint(
            "NOT (kind = 'aws_bootstrap' AND visibility = 'global')",
            name="ck_connector_credentials_aws_project_only",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_connector_credentials_schema_version"),
        sa.CheckConstraint("length(key_id) BETWEEN 1 AND 64", name="ck_connector_credentials_key_id"),
        sa.CheckConstraint("length(nonce) = 12", name="ck_connector_credentials_nonce"),
        sa.CheckConstraint(
            "length(ciphertext) BETWEEN 16 AND 16400",
            name="ck_connector_credentials_ciphertext_size",
        ),
        sa.CheckConstraint("state IN ('active', 'revoked')", name="ck_connector_credentials_state"),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector_id", "id", name="uq_connector_credentials_connector_id"),
    )
    op.create_index(
        "ix_connector_credentials_connector_state",
        "connector_credentials",
        ["connector_id", "state"],
    )

    op.create_table(
        "connector_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("configuration_json", sa.Text(), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("credential_version_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_connector_revisions_revision"),
        sa.CheckConstraint("schema_version = 1", name="ck_connector_revisions_schema_version"),
        sa.CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_revisions_visibility"),
        sa.CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_revisions_owner_shape",
        ),
        sa.CheckConstraint("length(configuration_json) <= 16384", name="ck_connector_revisions_config_size"),
        sa.CheckConstraint("length(configuration_digest) = 64", name="ck_connector_revisions_config_digest"),
        sa.ForeignKeyConstraint(
            ["connector_id", "credential_version_id"],
            ["connector_credentials.connector_id", "connector_credentials.id"],
            ondelete="RESTRICT",
            name="fk_connector_revisions_credential_owner",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector_id", "revision", name="uq_connector_revisions_revision"),
    )

    op.create_table(
        "project_connector_defaults",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_revision", sa.Integer(), nullable=False),
        sa.Column("settings_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("service IN ('brave', 'tavily')", name="ck_project_connector_defaults_service"),
        sa.CheckConstraint("connector_revision >= 1", name="ck_project_connector_defaults_connector_revision"),
        sa.CheckConstraint("settings_revision >= 1", name="ck_project_connector_defaults_settings_revision"),
        sa.ForeignKeyConstraint(
            ["connector_id", "connector_revision"],
            ["connector_revisions.connector_id", "connector_revisions.revision"],
            ondelete="RESTRICT",
            name="fk_project_connector_defaults_revision",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "service", name="uq_project_connector_defaults_service"),
    )
    op.create_index("ix_project_connector_defaults_connector", "project_connector_defaults", ["connector_id"])

    op.create_table(
        "connector_request_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=True),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("request_key_hmac", sa.String(length=64), nullable=False),
        sa.Column("payload_hmac", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_request_ledger_visibility"),
        sa.CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_request_ledger_owner_shape",
        ),
        sa.CheckConstraint("length(key_id) BETWEEN 1 AND 64", name="ck_connector_request_ledger_key_id"),
        sa.CheckConstraint("length(request_key_hmac) = 64", name="ck_connector_request_ledger_key_hmac"),
        sa.CheckConstraint("length(payload_hmac) = 64", name="ck_connector_request_ledger_payload_hmac"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_request_ledger_expires_at", "connector_request_ledger", ["expires_at"])
    op.create_index(
        "uq_connector_request_ledger_global_key",
        "connector_request_ledger",
        ["actor_user_id", "key_id", "request_key_hmac"],
        unique=True,
        postgresql_where=sa.text("visibility = 'global'"),
        sqlite_where=sa.text("visibility = 'global'"),
    )
    op.create_index(
        "uq_connector_request_ledger_project_key",
        "connector_request_ledger",
        ["project_id", "actor_user_id", "key_id", "request_key_hmac"],
        unique=True,
        postgresql_where=sa.text("visibility = 'project'"),
        sqlite_where=sa.text("visibility = 'project'"),
    )

    op.create_table(
        "connector_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("consuming_project_id", sa.Uuid(), nullable=True),
        sa.Column("connector_id", sa.Uuid(), nullable=True),
        sa.Column("connector_revision", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("details_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_audit_visibility"),
        sa.CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_audit_owner_shape",
        ),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 48", name="ck_connector_audit_action"),
        sa.CheckConstraint("length(outcome) BETWEEN 1 AND 24", name="ck_connector_audit_outcome"),
        sa.CheckConstraint("length(details_json) <= 16384", name="ck_connector_audit_details_size"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["consuming_project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_audit_project_created", "connector_audit_events", ["project_id", "created_at"])
    op.create_index("ix_connector_audit_connector_created", "connector_audit_events", ["connector_id", "created_at"])

    op.create_table(
        "connector_availability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_revision", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(worker_id) BETWEEN 1 AND 128", name="ck_connector_availability_worker"),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('missing_credentials', 'expired', 'revoked', 'runtime_unavailable', 'configuration_error')",
            name="ck_connector_availability_reason",
        ),
        sa.CheckConstraint("available OR reason IS NOT NULL", name="ck_connector_availability_reason_required"),
        sa.ForeignKeyConstraint(
            ["connector_id", "connector_revision"],
            ["connector_revisions.connector_id", "connector_revisions.revision"],
            ondelete="CASCADE",
            name="fk_connector_availability_revision",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "connector_id",
            "connector_revision",
            "worker_id",
            name="uq_connector_availability_project_worker",
        ),
    )
    op.create_index("ix_connector_availability_observed_at", "connector_availability", ["observed_at"])

    _create_integrity_triggers()


def _create_integrity_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """CREATE FUNCTION openship_connector_scope_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.visibility IS DISTINCT FROM OLD.visibility
                   OR NEW.project_id IS DISTINCT FROM OLD.project_id THEN
                    RAISE EXCEPTION 'Connector identity scope is immutable';
                END IF;
                RETURN NEW;
            END $$"""
        )
        op.execute(
            "CREATE TRIGGER trg_connector_scope_immutable BEFORE UPDATE ON connectors "
            "FOR EACH ROW EXECUTE FUNCTION openship_connector_scope_immutable()"
        )
        op.execute(
            """CREATE FUNCTION openship_connector_credential_scope() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM connectors c
                    WHERE c.id = NEW.connector_id
                      AND c.visibility = NEW.visibility
                      AND c.project_id IS NOT DISTINCT FROM NEW.project_id
                      AND ((c.kind = 'brave' AND NEW.kind = 'brave_api_key')
                        OR (c.kind = 'tavily' AND NEW.kind = 'tavily_api_key')
                        OR (c.kind = 'aws' AND NEW.kind = 'aws_bootstrap'))
                ) THEN
                    RAISE EXCEPTION 'Connector credential scope or kind mismatch';
                END IF;
                RETURN NEW;
            END $$"""
        )
        op.execute(
            "CREATE TRIGGER trg_connector_credential_scope BEFORE INSERT OR UPDATE ON connector_credentials "
            "FOR EACH ROW EXECUTE FUNCTION openship_connector_credential_scope()"
        )
        op.execute(
            """CREATE FUNCTION openship_connector_revision_scope() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM connectors c
                    JOIN connector_credentials v ON v.connector_id = c.id
                    WHERE c.id = NEW.connector_id
                      AND c.visibility = NEW.visibility
                      AND c.project_id IS NOT DISTINCT FROM NEW.project_id
                      AND v.id = NEW.credential_version_id
                      AND v.visibility = NEW.visibility
                      AND v.project_id IS NOT DISTINCT FROM NEW.project_id
                      AND ((c.kind = 'brave' AND v.kind = 'brave_api_key')
                        OR (c.kind = 'tavily' AND v.kind = 'tavily_api_key')
                        OR (c.kind = 'aws' AND v.kind = 'aws_bootstrap'))
                ) THEN
                    RAISE EXCEPTION 'Connector revision scope or credential mismatch';
                END IF;
                RETURN NEW;
            END $$"""
        )
        op.execute(
            "CREATE TRIGGER trg_connector_revision_scope BEFORE INSERT OR UPDATE ON connector_revisions "
            "FOR EACH ROW EXECUTE FUNCTION openship_connector_revision_scope()"
        )
        op.execute(
            """CREATE FUNCTION openship_connector_revision_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Connector revisions are immutable';
            END $$"""
        )
        op.execute(
            "CREATE TRIGGER trg_connector_revision_immutable BEFORE UPDATE ON connector_revisions "
            "FOR EACH ROW EXECUTE FUNCTION openship_connector_revision_immutable()"
        )
        op.execute(
            """CREATE FUNCTION openship_connector_audit_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Connector audit events are append-only';
            END $$"""
        )
        op.execute(
            "CREATE TRIGGER trg_connector_audit_append_only BEFORE UPDATE OR DELETE ON connector_audit_events "
            "FOR EACH ROW EXECUTE FUNCTION openship_connector_audit_append_only()"
        )
        return

    op.execute(
        """CREATE TRIGGER trg_connector_scope_immutable BEFORE UPDATE OF kind, visibility, project_id ON connectors
        WHEN NEW.kind IS NOT OLD.kind OR NEW.visibility IS NOT OLD.visibility OR NEW.project_id IS NOT OLD.project_id
        BEGIN SELECT RAISE(ABORT, 'Connector identity scope is immutable'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_credential_scope_insert BEFORE INSERT ON connector_credentials
        WHEN NOT EXISTS (
            SELECT 1 FROM connectors c WHERE c.id = NEW.connector_id
              AND c.visibility = NEW.visibility AND c.project_id IS NEW.project_id
              AND ((c.kind = 'brave' AND NEW.kind = 'brave_api_key')
                OR (c.kind = 'tavily' AND NEW.kind = 'tavily_api_key')
                OR (c.kind = 'aws' AND NEW.kind = 'aws_bootstrap'))
        ) BEGIN SELECT RAISE(ABORT, 'Connector credential scope or kind mismatch'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_credential_scope_update BEFORE UPDATE OF connector_id, visibility, project_id, kind ON connector_credentials
        WHEN NOT EXISTS (
            SELECT 1 FROM connectors c WHERE c.id = NEW.connector_id
              AND c.visibility = NEW.visibility AND c.project_id IS NEW.project_id
              AND ((c.kind = 'brave' AND NEW.kind = 'brave_api_key')
                OR (c.kind = 'tavily' AND NEW.kind = 'tavily_api_key')
                OR (c.kind = 'aws' AND NEW.kind = 'aws_bootstrap'))
        ) BEGIN SELECT RAISE(ABORT, 'Connector credential scope or kind mismatch'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_revision_scope_insert BEFORE INSERT ON connector_revisions
        WHEN NOT EXISTS (
            SELECT 1 FROM connectors c JOIN connector_credentials v ON v.connector_id = c.id
            WHERE c.id = NEW.connector_id AND c.visibility = NEW.visibility
              AND c.project_id IS NEW.project_id AND v.id = NEW.credential_version_id
              AND v.visibility = NEW.visibility AND v.project_id IS NEW.project_id
              AND ((c.kind = 'brave' AND v.kind = 'brave_api_key')
                OR (c.kind = 'tavily' AND v.kind = 'tavily_api_key')
                OR (c.kind = 'aws' AND v.kind = 'aws_bootstrap'))
        ) BEGIN SELECT RAISE(ABORT, 'Connector revision scope or credential mismatch'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_revision_scope_update BEFORE UPDATE ON connector_revisions
        WHEN NOT EXISTS (
            SELECT 1 FROM connectors c JOIN connector_credentials v ON v.connector_id = c.id
            WHERE c.id = NEW.connector_id AND c.visibility = NEW.visibility
              AND c.project_id IS NEW.project_id AND v.id = NEW.credential_version_id
              AND v.visibility = NEW.visibility AND v.project_id IS NEW.project_id
              AND ((c.kind = 'brave' AND v.kind = 'brave_api_key')
                OR (c.kind = 'tavily' AND v.kind = 'tavily_api_key')
                OR (c.kind = 'aws' AND v.kind = 'aws_bootstrap'))
        ) BEGIN SELECT RAISE(ABORT, 'Connector revision scope or credential mismatch'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_revision_immutable BEFORE UPDATE ON connector_revisions
        BEGIN SELECT RAISE(ABORT, 'Connector revisions are immutable'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_audit_append_only_update BEFORE UPDATE ON connector_audit_events
        BEGIN SELECT RAISE(ABORT, 'Connector audit events are append-only'); END"""
    )
    op.execute(
        """CREATE TRIGGER trg_connector_audit_append_only_delete BEFORE DELETE ON connector_audit_events
        BEGIN SELECT RAISE(ABORT, 'Connector audit events are append-only'); END"""
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = (
        "connector_availability",
        "connector_audit_events",
        "connector_request_ledger",
        "project_connector_defaults",
        "connector_revisions",
        "connector_credentials",
        "connectors",
    )
    retained = {
        table: bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in tables
    }
    total = sum(retained.values())
    if total:
        kinds = ", ".join(f"{table}={count}" for table, count in retained.items() if count)
        raise ValueError(
            "refusing to downgrade: managed Connector records contain retained data "
            f"({kinds}). Keep this revision or explicitly retire the records first."
        )

    _drop_integrity_triggers()
    op.drop_index("ix_connector_availability_observed_at", table_name="connector_availability")
    op.drop_table("connector_availability")
    op.drop_index("ix_connector_audit_connector_created", table_name="connector_audit_events")
    op.drop_index("ix_connector_audit_project_created", table_name="connector_audit_events")
    op.drop_table("connector_audit_events")
    op.drop_index("uq_connector_request_ledger_project_key", table_name="connector_request_ledger")
    op.drop_index("uq_connector_request_ledger_global_key", table_name="connector_request_ledger")
    op.drop_index("ix_connector_request_ledger_expires_at", table_name="connector_request_ledger")
    op.drop_table("connector_request_ledger")
    op.drop_index("ix_project_connector_defaults_connector", table_name="project_connector_defaults")
    op.drop_table("project_connector_defaults")
    op.drop_table("connector_revisions")
    op.drop_index("ix_connector_credentials_connector_state", table_name="connector_credentials")
    op.drop_table("connector_credentials")
    op.drop_index("ix_connectors_visibility_kind", table_name="connectors")
    op.drop_index("ix_connectors_project_kind", table_name="connectors")
    op.drop_table("connectors")


def _drop_integrity_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER trg_connector_audit_append_only ON connector_audit_events")
        op.execute("DROP TRIGGER trg_connector_revision_immutable ON connector_revisions")
        op.execute("DROP TRIGGER trg_connector_revision_scope ON connector_revisions")
        op.execute("DROP TRIGGER trg_connector_credential_scope ON connector_credentials")
        op.execute("DROP TRIGGER trg_connector_scope_immutable ON connectors")
        op.execute("DROP FUNCTION openship_connector_audit_append_only()")
        op.execute("DROP FUNCTION openship_connector_revision_immutable()")
        op.execute("DROP FUNCTION openship_connector_revision_scope()")
        op.execute("DROP FUNCTION openship_connector_credential_scope()")
        op.execute("DROP FUNCTION openship_connector_scope_immutable()")
        return
    for name, table in (
        ("trg_connector_audit_append_only_delete", "connector_audit_events"),
        ("trg_connector_audit_append_only_update", "connector_audit_events"),
        ("trg_connector_revision_immutable", "connector_revisions"),
        ("trg_connector_revision_scope_update", "connector_revisions"),
        ("trg_connector_revision_scope_insert", "connector_revisions"),
        ("trg_connector_credential_scope_update", "connector_credentials"),
        ("trg_connector_credential_scope_insert", "connector_credentials"),
        ("trg_connector_scope_immutable", "connectors"),
    ):
        op.execute(f"DROP TRIGGER {name}")
