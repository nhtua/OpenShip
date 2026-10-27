"""Record capacity-scan provenance for exhausted run sources.

Revision ID: phase6a019
Revises: phase6a018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6a019"
down_revision: str | None = "phase6a018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("message_exhaustions") as batch:
        batch.drop_constraint("ck_exhaustion_deliveries", type_="check")
        batch.add_column(
            sa.Column(
                "discovery_method",
                sa.String(length=24),
                server_default="advisory",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("recovery_operator", sa.String(length=255)))
        batch.create_check_constraint(
            "ck_exhaustion_deliveries",
            "delivery_count >= 0 AND "
            "(discovery_method = 'capacity_scan' OR delivery_count > 0)",
        )
        batch.create_check_constraint(
            "ck_exhaustion_discovery_method",
            "discovery_method IN ('advisory', 'capacity_scan')",
        )
        batch.create_check_constraint(
            "ck_exhaustion_recovery_operator",
            "(discovery_method = 'capacity_scan' AND recovery_operator IS NOT NULL "
            "AND length(trim(recovery_operator)) BETWEEN 1 AND 255) OR "
            "(discovery_method = 'advisory' AND recovery_operator IS NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    scanned_observations = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM message_exhaustions "
            "WHERE discovery_method = 'capacity_scan'"
        )
    ).scalar_one()
    if scanned_observations:
        raise RuntimeError(
            "refusing to downgrade while capacity-scan exhaustion audit rows exist"
        )

    with op.batch_alter_table("message_exhaustions") as batch:
        batch.drop_constraint("ck_exhaustion_recovery_operator", type_="check")
        batch.drop_constraint("ck_exhaustion_discovery_method", type_="check")
        batch.drop_constraint("ck_exhaustion_deliveries", type_="check")
        batch.drop_column("recovery_operator")
        batch.drop_column("discovery_method")
        batch.create_check_constraint(
            "ck_exhaustion_deliveries", "delivery_count > 0"
        )
