"""Allow retained sandbox analysis outputs in the artifact kind vocabulary.

Revision ID: phase6b027
Revises: phase6b026
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6b027"
down_revision: str | None = "phase6b026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_kind",
            "kind IN ('search_result', 'upload', 'cloud_observation', 'analysis_output')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    retained = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM artifacts WHERE kind = 'analysis_output'"
        )
    ).scalar_one()
    if retained:
        raise RuntimeError(
            "refusing downgrade while retained analysis outputs exist"
        )
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_kind",
            "kind IN ('search_result', 'upload', 'cloud_observation')",
        )
