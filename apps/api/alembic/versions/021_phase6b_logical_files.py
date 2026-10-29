"""Add logical file identities and immutable version references."""

import sqlalchemy as sa

from alembic import op

revision = "phase6b021"
down_revision = "phase6a020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "logical_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "file_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "file_id",
            sa.Uuid(),
            sa.ForeignKey("logical_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("base_version_id", sa.Uuid()),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "conversation_id", "request_key", name="uq_file_versions_request"
        ),
        sa.UniqueConstraint("file_id", "id", name="uq_file_versions_file_id"),
    )
    constraint_options = {
        "deferrable": True,
        "initially": "DEFERRED",
    }
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("logical_files", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_logical_files_current_version",
                "file_versions",
                ["id", "current_version_id"],
                ["file_id", "id"],
                **constraint_options,
            )
    else:
        op.create_foreign_key(
            "fk_logical_files_current_version",
            "logical_files",
            "file_versions",
            ["id", "current_version_id"],
            ["file_id", "id"],
            **constraint_options,
        )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("logical_files", recreate="always") as batch_op:
            batch_op.drop_constraint(
                "fk_logical_files_current_version", type_="foreignkey"
            )
    else:
        op.drop_constraint(
            "fk_logical_files_current_version", "logical_files", type_="foreignkey"
        )
    op.drop_table("file_versions")
    op.drop_table("logical_files")
