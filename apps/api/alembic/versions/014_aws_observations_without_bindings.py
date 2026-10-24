"""Allow AWS read observations without resource bindings.

Revision ID: phase5b014
Revises: phase5b013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5b014"
down_revision: str | None = "phase5b013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "resource_observations"
_APPEND_ONLY_CHECK = "resource observations are append-only"


def upgrade() -> None:
    _drop_append_only_trigger()
    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.drop_constraint(
            "ck_resource_observations_binding_revision", type_="check"
        )
        batch.alter_column("resource_object_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column(
            "resource_binding_id", existing_type=sa.Uuid(), nullable=True
        )
        batch.alter_column(
            "binding_revision", existing_type=sa.Integer(), nullable=True
        )
        batch.add_column(
            sa.Column(
                "schema_version", sa.Integer(), server_default="1", nullable=False
            )
        )
        batch.add_column(sa.Column("connector_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("connector_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("account_id", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("service", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("operation", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("region", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("target_json", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("catalog_digest", sa.String(length=64), nullable=True)
        )
        batch.create_foreign_key(
            "fk_resource_observations_project_connector_revision",
            "connector_revisions",
            ["project_id", "connector_id", "connector_revision"],
            ["project_id", "connector_id", "revision"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_resource_observations_schema_shape",
            "(schema_version = 1 AND resource_object_id IS NOT NULL "
            "AND resource_binding_id IS NOT NULL AND binding_revision >= 1 "
            "AND connector_id IS NULL AND connector_revision IS NULL AND account_id IS NULL "
            "AND service IS NULL AND operation IS NULL AND region IS NULL "
            "AND target_json IS NULL AND catalog_digest IS NULL) OR "
            "(schema_version = 2 AND resource_object_id IS NULL "
            "AND resource_binding_id IS NULL AND binding_revision IS NULL "
            "AND connector_id IS NOT NULL AND connector_revision >= 1 "
            "AND length(account_id) = 12 AND length(service) BETWEEN 1 AND 64 "
            "AND length(operation) BETWEEN 1 AND 128 AND length(region) BETWEEN 1 AND 64 "
            "AND target_json IS NOT NULL AND length(target_json) <= 16384 "
            "AND length(catalog_digest) = 64)",
        )
        batch.create_check_constraint(
            "ck_resource_observations_schema_version", "schema_version IN (1, 2)"
        )
        batch.create_check_constraint(
            "ck_resource_observations_connector_revision",
            "connector_revision IS NULL OR connector_revision >= 1",
        )
    op.create_index(
        "ix_resource_observations_connector_retrieved",
        _TABLE,
        ["project_id", "connector_id", "retrieved_at", "id"],
    )
    _create_append_only_trigger()


def downgrade() -> None:
    bind = op.get_bind()
    generic_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM resource_observations WHERE schema_version = 2"
            )
        ).scalar_one()
    )
    if generic_count:
        raise RuntimeError(
            "refusing to downgrade: binding-free AWS observations cannot be represented "
            "by the legacy schema"
        )

    _drop_append_only_trigger()
    op.drop_index("ix_resource_observations_connector_retrieved", table_name=_TABLE)
    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.drop_constraint("ck_resource_observations_schema_shape", type_="check")
        batch.drop_constraint("ck_resource_observations_schema_version", type_="check")
        batch.drop_constraint(
            "ck_resource_observations_connector_revision", type_="check"
        )
        batch.drop_constraint(
            "fk_resource_observations_project_connector_revision", type_="foreignkey"
        )
        for column in (
            "schema_version",
            "connector_id",
            "connector_revision",
            "account_id",
            "service",
            "operation",
            "region",
            "target_json",
            "catalog_digest",
        ):
            batch.drop_column(column)
        batch.alter_column(
            "resource_object_id", existing_type=sa.Uuid(), nullable=False
        )
        batch.alter_column(
            "resource_binding_id", existing_type=sa.Uuid(), nullable=False
        )
        batch.alter_column(
            "binding_revision", existing_type=sa.Integer(), nullable=False
        )
        batch.create_check_constraint(
            "ck_resource_observations_binding_revision", "binding_revision >= 1"
        )
    _create_append_only_trigger()


def _drop_append_only_trigger() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_resource_observations_append_only ON resource_observations"
        )
    else:
        op.execute(
            "DROP TRIGGER IF EXISTS trg_resource_observations_append_only_update"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_resource_observations_append_only_delete"
        )


def _create_append_only_trigger() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """CREATE TRIGGER trg_resource_observations_append_only
            BEFORE UPDATE OR DELETE ON resource_observations
            FOR EACH ROW EXECUTE FUNCTION openship_resource_observation_append_only()"""
        )
        return
    op.execute(
        f"""CREATE TRIGGER trg_resource_observations_append_only_update
        BEFORE UPDATE ON resource_observations
        BEGIN SELECT RAISE(ABORT, '{_APPEND_ONLY_CHECK}'); END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_resource_observations_append_only_delete
        BEFORE DELETE ON resource_observations
        BEGIN SELECT RAISE(ABORT, '{_APPEND_ONLY_CHECK}'); END"""
    )
