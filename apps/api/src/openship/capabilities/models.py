"""Database records for managed installation-wide tool availability."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class ToolPolicyState(Base):
    __tablename__ = "tool_policy_state"

    id = Column(Integer, primary_key=True, default=1)
    revision = Column(Integer, nullable=False, server_default="1")
    updated_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    legacy_cutover_at = Column(DateTime(timezone=True), nullable=True)
    legacy_cutover_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_tool_policy_state_singleton"),
        CheckConstraint("revision >= 1", name="ck_tool_policy_state_revision"),
    )


class ToolPolicyOverride(Base):
    __tablename__ = "tool_policy_overrides"

    capability_id = Column(String(64), primary_key=True)
    enabled = Column(Boolean, nullable=False)
    updated_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ToolPolicyAuditEvent(Base):
    __tablename__ = "tool_policy_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision = Column(Integer, nullable=False)
    actor_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action = Column(String(32), nullable=False)
    capability_id = Column(String(64), nullable=True)
    enabled = Column(Boolean, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "action IN ('enabled', 'disabled', 'legacy_cutover')",
            name="ck_tool_policy_audit_action",
        ),
        CheckConstraint("revision >= 1", name="ck_tool_policy_audit_revision"),
    )
