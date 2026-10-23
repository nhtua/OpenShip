"""Allow connector waits to pin global as well as project Connectors.

Revision ID: phase5a011
Revises: phase5a010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a011"
down_revision: str | None = "phase5a010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_connector_revisions_connector_revision_credential",
        "connector_revisions",
        ["connector_id", "revision", "credential_version_id"],
        unique=True,
    )
    op.add_column(
        "connector_blockers",
        sa.Column("connector_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "connector_blockers",
        sa.Column("connector_visibility", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "connector_blockers",
        sa.Column("connector_project_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE connector_blockers SET "
            "connector_kind = (SELECT kind FROM connectors "
            "WHERE connectors.id = connector_blockers.connector_id), "
            "connector_visibility = (SELECT visibility FROM connectors "
            "WHERE connectors.id = connector_blockers.connector_id), "
            "connector_project_id = (SELECT project_id FROM connectors "
            "WHERE connectors.id = connector_blockers.connector_id)"
        )
    )
    with op.batch_alter_table("connector_blockers") as batch:
        batch.alter_column("connector_kind", nullable=False)
        batch.alter_column("connector_visibility", nullable=False)
        batch.drop_constraint(
            "fk_connector_blockers_connector_revision", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_connector_blockers_connector_revision_identity",
            "connector_revisions",
            ["connector_id", "connector_revision", "credential_version_id"],
            ["connector_id", "revision", "credential_version_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_connector_blockers_connector_revision",
            "connector_revisions",
            [
                "connector_project_id",
                "connector_id",
                "connector_revision",
                "credential_version_id",
            ],
            ["project_id", "connector_id", "revision", "credential_version_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_connector_blockers_connector_kind",
            "connector_kind IN ('brave', 'tavily', 'aws')",
        )
        batch.create_check_constraint(
            "ck_connector_blockers_connector_scope",
            "(connector_visibility = 'global' AND connector_project_id IS NULL) OR "
            "(connector_visibility = 'project' AND connector_project_id = project_id)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    global_blockers = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM connector_blockers "
                "WHERE connector_visibility = 'global'"
            )
        ).scalar_one()
    )
    if global_blockers:
        raise RuntimeError(
            "refusing to downgrade: global Connector blocker data would lose its owner scope"
        )

    with op.batch_alter_table("connector_blockers") as batch:
        batch.drop_constraint("ck_connector_blockers_connector_scope", type_="check")
        batch.drop_constraint("ck_connector_blockers_connector_kind", type_="check")
        batch.drop_constraint(
            "fk_connector_blockers_connector_revision_identity", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_connector_blockers_connector_revision", type_="foreignkey"
        )
        batch.drop_column("connector_visibility")
        batch.drop_column("connector_project_id")
        batch.drop_column("connector_kind")
        batch.create_foreign_key(
            "fk_connector_blockers_connector_revision",
            "connector_revisions",
            [
                "project_id",
                "connector_id",
                "connector_revision",
                "credential_version_id",
            ],
            ["project_id", "connector_id", "revision", "credential_version_id"],
            ondelete="RESTRICT",
        )
    op.drop_index(
        "uq_connector_revisions_connector_revision_credential",
        table_name="connector_revisions",
    )
