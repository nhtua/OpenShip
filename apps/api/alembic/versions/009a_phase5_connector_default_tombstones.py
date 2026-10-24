"""Retain cleared project Connector defaults for monotonic preconditions.

Revision ID: phase5a009b
Revises: phase5a009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a009b"
down_revision: str | None = "phase5a009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project_connector_defaults") as batch:
        batch.alter_column("connector_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("connector_revision", existing_type=sa.Integer(), nullable=True)
        batch.create_check_constraint(
            "ck_project_connector_defaults_target_shape",
            "(connector_id IS NULL AND connector_revision IS NULL) OR "
            "(connector_id IS NOT NULL AND connector_revision IS NOT NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    cleared = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM project_connector_defaults "
            "WHERE connector_id IS NULL OR connector_revision IS NULL"
        )
    ).scalar_one()
    if cleared:
        raise ValueError(
            "refusing to downgrade: cleared Connector defaults retain revision history"
        )
    with op.batch_alter_table("project_connector_defaults") as batch:
        batch.drop_constraint("ck_project_connector_defaults_target_shape", type_="check")
        batch.alter_column("connector_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("connector_revision", existing_type=sa.Integer(), nullable=False)
