"""Persist one reusable OpenSandbox handle pointer per authorized root run.

Revision ID: phase6b026
Revises: phase6a020
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase6b026"
down_revision: str | None = "phase6a020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_sandboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("root_run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("sandbox_id", sa.String(length=255), nullable=True),
        sa.Column("requirements_key", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="requested", nullable=False
        ),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["root_run_id"], ["runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "root_run_id", name="uq_workflow_sandboxes_root_run"
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'active')", name="ck_workflow_sandbox_status"
        ),
        sa.CheckConstraint("fence >= 1", name="ck_workflow_sandbox_fence"),
        sa.CheckConstraint(
            "length(trim(provider_id)) BETWEEN 1 AND 128 "
            "AND length(requirements_key) = 64",
            name="ck_workflow_sandbox_identity",
        ),
        sa.CheckConstraint(
            "(status = 'requested' AND sandbox_id IS NULL AND expires_at IS NULL) "
            "OR (status = 'active' AND sandbox_id IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_workflow_sandbox_state",
        ),
    )
    op.create_index(
        "ix_workflow_sandboxes_project_conversation",
        "workflow_sandboxes",
        ["project_id", "conversation_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    pointers = bind.execute(sa.text("SELECT COUNT(*) FROM workflow_sandboxes")).scalar_one()
    if pointers:
        raise RuntimeError(
            "refusing downgrade while workflow sandbox pointers exist; the provider"
            " still owns their expiration and cleanup"
        )
    op.drop_index("ix_workflow_sandboxes_project_conversation", table_name="workflow_sandboxes")
    op.drop_table("workflow_sandboxes")
