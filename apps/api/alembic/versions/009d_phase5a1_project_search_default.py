"""Keep one shared search default for each project.

Revision ID: phase5a009d
Revises: phase5a009c
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a009d"
down_revision: str | None = "phase5a009c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    defaults = sa.table(
        "project_connector_defaults",
        sa.column("id", sa.Uuid()),
        sa.column("project_id", sa.Uuid()),
        sa.column("service", sa.String(16)),
        sa.column("settings_revision", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            defaults.c.id,
            defaults.c.project_id,
            defaults.c.service,
        ).order_by(
            defaults.c.project_id,
            defaults.c.updated_at.desc(),
            defaults.c.settings_revision.desc(),
            sa.case((defaults.c.service == "brave", 0), else_=1),
            defaults.c.id,
        )
    ).all()
    seen_projects = set()
    redundant_ids = []
    for row in rows:
        if row.project_id in seen_projects:
            redundant_ids.append(row.id)
        else:
            seen_projects.add(row.project_id)
    if redundant_ids:
        bind.execute(
            sa.delete(defaults).where(defaults.c.id.in_(redundant_ids))
        )

    with op.batch_alter_table("project_connector_defaults") as batch:
        batch.drop_constraint("uq_project_connector_defaults_service", type_="unique")
        batch.create_unique_constraint(
            "uq_project_connector_defaults_project", ["project_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("project_connector_defaults") as batch:
        batch.drop_constraint("uq_project_connector_defaults_project", type_="unique")
        batch.create_unique_constraint(
            "uq_project_connector_defaults_service", ["project_id", "service"]
        )
