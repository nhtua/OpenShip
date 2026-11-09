"""SQLAlchemy records for durable task plans, attempts and decisions.

These tables are an index over the existing run, operation and artifact
ledgers; committed operation and artifact rows remain authoritative for
execution outcomes. Task rows only bind the logical plan identity to those
records and never copy their payloads.

Task 6 declares the decision tables so later tasks can build on the same
migration, but this module performs no decision transition.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from ...auth.models import Base

TASK_STATUSES = (
    "unknown",
    "planned",
    "running",
    "waiting",
    "verification_pending",
    "completed",
    "blocked",
    "failed",
    "cancelled",
)
PLAN_STATUSES = ("proposed", "validated")
ATTEMPT_STATUSES = ("running", "succeeded", "failed", "interrupted", "uncertain")
DECISION_KINDS = ("plan_confirmation", "clarification", "configuration")
DECISION_STATES = ("open", "confirmed", "rejected", "answered", "expired", "cancelled")
DECISION_ACTIONS = ("confirm", "reject", "answer", "resume_configuration")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class TaskRecord(Base):
    """One durable task projection for one v5 root run."""

    __tablename__ = "task_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
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
    initiator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope = Column(String(16), nullable=False, server_default="conversation")
    mode = Column(String(16), nullable=False, server_default="copilot")
    graph_version = Column(Integer, nullable=False, server_default="5")
    current_revision = Column(Integer, nullable=False, server_default="1")
    status = Column(String(32), nullable=False, server_default="unknown")
    digest = Column(String(64), nullable=True)
    fence = Column(Integer, nullable=False, server_default="0")
    context_json = Column(Text, nullable=False, server_default="{}")
    counters_json = Column(Text, nullable=False, server_default="{}")
    accepted_evidence_json = Column(Text, nullable=False, server_default="[]")
    verified_outputs_json = Column(Text, nullable=False, server_default="[]")
    verified_steps_json = Column(Text, nullable=False, server_default="[]")
    last_disposition = Column(String(24), nullable=False, server_default="none")
    active_ms = Column(BigInteger, nullable=False, server_default="0")
    active_started_at = Column(DateTime(timezone=True), nullable=True)
    active_deadline_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_task_records_run_id"),
        CheckConstraint("graph_version = 5", name="ck_task_records_graph_version"),
        CheckConstraint("current_revision >= 0", name="ck_task_records_revision"),
        CheckConstraint("fence >= 0", name="ck_task_records_fence"),
        CheckConstraint("active_ms >= 0", name="ck_task_records_active_ms"),
        CheckConstraint(
            "scope IN ('conversation', 'project')",
            name="ck_task_records_scope",
        ),
        CheckConstraint("mode IN ('auto', 'copilot')", name="ck_task_records_mode"),
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_task_records_status"),
        CheckConstraint(
            _in(
                "last_disposition",
                (
                    "none",
                    "known_failure",
                    "material_revision",
                    "uncertain",
                    "denied",
                    "policy_revoked",
                    "budget_exhausted",
                ),
            ),
            name="ck_task_records_last_disposition",
        ),
        Index("ix_task_records_project_id", "project_id"),
        Index("ix_task_records_conversation_id", "conversation_id"),
        Index("ix_task_records_status", "status"),
    )


class TaskPlanRecord(Base):
    """An immutable reviewed plan revision for a task."""

    __tablename__ = "task_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision = Column(Integer, nullable=False)
    digest = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, server_default="validated")
    plan_json = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("run_id", "revision", name="uq_task_plans_run_revision"),
        CheckConstraint("revision >= 1", name="ck_task_plans_revision"),
        CheckConstraint(_in("status", PLAN_STATUSES), name="ck_task_plans_status"),
        Index("ix_task_plans_task_id", "task_id"),
    )


class TaskStepAttempt(Base):
    """One logical step attempt bound to one committed tool operation."""

    __tablename__ = "task_step_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_revision = Column(Integer, nullable=False)
    step_id = Column(String(64), nullable=False)
    ordinal = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)
    operation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tool_operations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_digest = Column(String(64), nullable=True)
    result_digest = Column(String(64), nullable=True)
    evidence_json = Column(Text, nullable=False, server_default="[]")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "step_id",
            "ordinal",
            name="uq_task_step_attempts_run_step_ordinal",
        ),
        UniqueConstraint(
            "run_id",
            "operation_id",
            name="uq_task_step_attempts_run_operation",
        ),
        CheckConstraint("plan_revision >= 1", name="ck_task_step_attempts_revision"),
        CheckConstraint(
            "ordinal >= 1 AND ordinal <= 3", name="ck_task_step_attempts_ordinal"
        ),
        CheckConstraint(
            _in("status", ATTEMPT_STATUSES), name="ck_task_step_attempts_status"
        ),
        Index("ix_task_step_attempts_task_id", "task_id"),
        Index("ix_task_step_attempts_run_id", "run_id"),
        Index("ix_task_step_attempts_operation_id", "operation_id"),
    )


class TaskDecisionRecord(Base):
    """One exact durable decision request; identity is stable across replays."""

    __tablename__ = "task_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(String(24), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    expected_digest = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False, server_default="open")
    request_json = Column(Text, nullable=False, server_default="{}")
    actor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "request_id", name="uq_task_decisions_run_request"),
        CheckConstraint(
            "plan_revision >= 0 AND (kind != 'plan_confirmation' OR plan_revision >= 1)",
            name="ck_task_decisions_revision",
        ),
        CheckConstraint(_in("kind", DECISION_KINDS), name="ck_task_decisions_kind"),
        CheckConstraint(_in("state", DECISION_STATES), name="ck_task_decisions_state"),
        Index("ix_task_decisions_task_id", "task_id"),
        Index("ix_task_decisions_run_id", "run_id"),
        Index(
            "uq_task_decisions_open_run",
            "run_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
            sqlite_where=text("state = 'open'"),
        ),
    )


class TaskDecisionCommandRecord(Base):
    """One exact decision command and the result it produced.

    This is the durable idempotency ledger, distinct from the public API
    ``TaskDecisionCommand`` contract: it also records the outcome so a lost
    response can replay the same result without consuming the request twice.
    """

    __tablename__ = "task_decision_commands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_request_id = Column(UUID(as_uuid=True), nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    action = Column(String(24), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    expected_digest = Column(String(64), nullable=False)
    answer = Column(Text, nullable=True)
    result_json = Column(Text, nullable=False, server_default="{}")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "client_request_id",
            name="uq_task_decision_commands_run_client_request",
        ),
        CheckConstraint(
            _in("action", DECISION_ACTIONS), name="ck_task_decision_commands_action"
        ),
        CheckConstraint(
            "plan_revision >= 0", name="ck_task_decision_commands_revision"
        ),
        Index("ix_task_decision_commands_task_id", "task_id"),
        Index("ix_task_decision_commands_decision_id", "decision_id"),
    )
