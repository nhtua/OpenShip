"""Allow owners to create multiple independent projects.

Revision ID: phase5a009c
Revises: phase5a009b
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5a009c"
down_revision: str | None = "phase5a009b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("uq_projects_owner_user", type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    projects = sa.table("projects", sa.column("owner_user_id"))
    owner_user_id = projects.c.owner_user_id
    multiple_projects = bind.execute(
        sa.select(owner_user_id)
        .select_from(projects)
        .group_by(owner_user_id)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if multiple_projects is not None:
        raise RuntimeError(
            "Refusing to downgrade multiple projects per owner; consolidate projects explicitly first"
        )

    with op.batch_alter_table("projects") as batch_op:
        batch_op.create_unique_constraint(
            "uq_projects_owner_user", ["owner_user_id"]
        )
