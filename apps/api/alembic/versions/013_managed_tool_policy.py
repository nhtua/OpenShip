"""Persist one shared operator policy for installed maintained tools.

Revision ID: phase5b013
Revises: phase5a012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phase5b013"
down_revision: str | None = "phase5a012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_policy_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("legacy_cutover_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "legacy_cutover_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="ck_tool_policy_state_singleton"),
        sa.CheckConstraint("revision >= 1", name="ck_tool_policy_state_revision"),
    )
    op.create_table(
        "tool_policy_overrides",
        sa.Column("capability_id", sa.String(length=64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "tool_policy_audit_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("capability_id", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "action IN ('enabled', 'disabled', 'legacy_cutover')",
            name="ck_tool_policy_audit_action",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tool_policy_audit_revision"),
    )
    op.add_column(
        "tool_operations", sa.Column("tool_policy_revision", sa.Integer(), nullable=True)
    )
    op.add_column("runs", sa.Column("tool_policy_revision", sa.Integer(), nullable=True))
    op.execute(
        sa.text("INSERT INTO tool_policy_state (id, revision) VALUES (1, 1)")
    )


def downgrade() -> None:
    bind = op.get_bind()
    audit_count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM tool_policy_audit_events")).scalar_one()
    )
    override_count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM tool_policy_overrides")).scalar_one()
    )
    if audit_count or override_count:
        raise RuntimeError(
            "refusing to downgrade: managed tool policy history cannot be restored"
        )
    op.drop_column("runs", "tool_policy_revision")
    op.drop_column("tool_operations", "tool_policy_revision")
    op.drop_table("tool_policy_audit_events")
    op.drop_table("tool_policy_overrides")
    op.drop_table("tool_policy_state")
