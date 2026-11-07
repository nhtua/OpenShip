"""Database records for workflow-scoped OpenSandbox handle pointers."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class WorkflowSandbox(Base):
    """One reusable provider handle pointer per authorized root run.

    The row is a pointer only: OpenSandbox owns expiration, cleanup and deletion,
    and a replaced or lost pointer simply leaves its sandbox to the original
    provider TTL. No cleanup, capacity or orphan bookkeeping lives here.
    """

    __tablename__ = "workflow_sandboxes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    root_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_id = Column(String(128), nullable=False)
    sandbox_id = Column(String(255), nullable=True)
    requirements_key = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=True)
    intent_id = Column(UUID(as_uuid=True), nullable=True)
    command_uncertain_until = Column(DateTime(timezone=True), nullable=True)
    command_operation_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(16), nullable=False, server_default="requested")
    fence = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "project_id", "conversation_id", name="uq_workflow_sandboxes_scope"
        ),
        CheckConstraint("status IN ('requested', 'active')", name="ck_workflow_sandbox_status"),
        CheckConstraint("fence >= 1", name="ck_workflow_sandbox_fence"),
        CheckConstraint(
            "(status = 'requested' AND sandbox_id IS NULL AND expires_at IS NULL "
            "AND requested_at IS NOT NULL AND intent_id IS NOT NULL "
            "AND command_uncertain_until IS NULL AND command_operation_id IS NULL) "
            "OR (status = 'active' AND sandbox_id IS NOT NULL AND expires_at IS NOT NULL "
            "AND requested_at IS NULL AND intent_id IS NULL "
            "AND ((command_uncertain_until IS NULL AND command_operation_id IS NULL) "
            "OR (command_uncertain_until IS NOT NULL AND command_operation_id IS NOT NULL)))",
            name="ck_workflow_sandbox_state",
        ),
        Index(
            "ix_workflow_sandboxes_project_conversation",
            "project_id",
            "conversation_id",
        ),
    )
