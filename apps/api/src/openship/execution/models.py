"""Database records for fenced tool operations and shared execution capacity."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class ToolOperation(Base):
    __tablename__ = "tool_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    principal_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    logical_call_id = Column(String(128), nullable=False)
    capability_id = Column(String(64), nullable=False)
    capability_version = Column(String(32), nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    arguments_json = Column(Text, nullable=False)
    arguments_digest = Column(String(64), nullable=False)
    request_digest = Column(String(64), nullable=False)
    input_artifact_refs = Column(Text, nullable=False, server_default="[]")
    credential_binding_ref = Column(String(128), nullable=True)
    configuration_revision = Column(String(64), nullable=False)
    execution_bundle_digest = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, server_default="admitted")
    error_code = Column(String(64), nullable=True)
    result_json = Column(Text, nullable=True)
    result_digest = Column(String(64), nullable=True)
    output_artifact_ids = Column(Text, nullable=False, server_default="[]")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "logical_call_id", name="uq_tool_operations_run_logical_call"),
        Index(
            "uq_tool_operations_project_conversation_id",
            "project_id",
            "conversation_id",
            "id",
            unique=True,
        ),
        CheckConstraint(
            "status IN ('admitted', 'dispatching', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_operations_status",
        ),
        Index("ix_tool_operations_conversation_created", "conversation_id", "created_at"),
        Index("ix_tool_operations_run_id", "run_id"),
    )


class ToolAttempt(Base):
    __tablename__ = "tool_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(
        UUID(as_uuid=True), ForeignKey("tool_operations.id", ondelete="CASCADE"), nullable=False
    )
    ordinal = Column(Integer, nullable=False)
    fence = Column(Integer, nullable=False)
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    request_digest = Column(String(64), nullable=True)
    response_digest = Column(String(64), nullable=True)
    outcome = Column(String(24), nullable=False, server_default="started")
    error_code = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("operation_id", "ordinal", name="uq_tool_attempts_operation_ordinal"),
        UniqueConstraint("operation_id", name="uq_tool_attempts_one_physical_attempt"),
        CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_attempts_outcome",
        ),
        Index("ix_tool_attempts_operation_id", "operation_id"),
    )


class ExecutionSlot(Base):
    __tablename__ = "execution_slots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slot_number = Column(Integer, nullable=False)
    operation_id = Column(
        UUID(as_uuid=True), ForeignKey("tool_operations.id", ondelete="SET NULL"), nullable=True
    )
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=True)
    fence = Column(Integer, nullable=True)
    owner_id = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("slot_number", name="uq_execution_slots_slot_number"),
        CheckConstraint("slot_number >= 0", name="ck_execution_slots_number"),
        CheckConstraint(
            "(operation_id IS NULL AND run_id IS NULL AND fence IS NULL AND owner_id IS NULL AND lease_until IS NULL) "
            "OR (operation_id IS NOT NULL AND run_id IS NOT NULL AND fence IS NOT NULL AND owner_id IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_execution_slots_lease_shape",
        ),
        Index("ix_execution_slots_lease_until", "lease_until"),
        Index(
            "uq_execution_slots_operation",
            "operation_id",
            unique=True,
            postgresql_where=(operation_id.is_not(None)),
            sqlite_where=(operation_id.is_not(None)),
        ),
    )


class ToolAvailability(Base):
    __tablename__ = "tool_availability"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    capability_id = Column(String(64), nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    worker_id = Column(String(128), nullable=False)
    available = Column(Boolean, nullable=False)
    reason = Column(String(32), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("capability_id", "worker_id", name="uq_tool_availability_capability_worker"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('missing_credentials', 'disabled', 'runtime_unavailable', 'configuration_error')",
            name="ck_tool_availability_reason",
        ),
        Index("ix_tool_availability_observed_at", "observed_at"),
    )
