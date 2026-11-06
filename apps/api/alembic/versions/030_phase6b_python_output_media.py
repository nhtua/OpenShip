"""Allow validated image media for tool-owned artifact rows.

General python execution may publish validated PNG/JPEG outputs; uploads
remain restricted to plain text and JSON at the service gate, and the DB
check keeps that invariant for rows reaching it.

Revision ID: phase6b030
Revises: phase6b029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6b030"
down_revision: str | None = "phase6b029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT_MEDIA = "media_type IN ('application/json', 'text/plain')"
IMAGE_MEDIA = "media_type IN ('image/jpeg', 'image/png')"


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_media_type", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_media_type",
            f"{TEXT_MEDIA} OR (source = 'tool' AND {IMAGE_MEDIA})",
        )


def downgrade() -> None:
    bind = op.get_bind()
    images = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM artifacts WHERE {IMAGE_MEDIA}")
    ).scalar_one()
    if images:
        raise RuntimeError(
            "refusing downgrade while tool-owned image artifact rows exist"
        )
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("ck_artifacts_media_type", type_="check")
        batch.create_check_constraint(
            "ck_artifacts_media_type",
            TEXT_MEDIA,
        )
