import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, CheckConstraint, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class Run(Base):
    """A durable run within a conversation/project. Tracks execution state and fencing."""
    __tablename__ = "runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    graph_thread_id = Column(String(255), nullable=True)
    client_request_id = Column(String(255), nullable=True)
    status = Column(
        String(20),
        nullable=False,
        server_default="created",
    )
    attempt = Column(Integer, nullable=False, server_default="0")
    fence = Column(Integer, nullable=False, server_default="0")
    provider_started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(50), nullable=True)
    usage = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out', 'stale')",
            name="ck_runs_status",
        ),
        Index("ix_runs_conversation_id", "conversation_id"),
        Index("ix_runs_project_id", "project_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_ended_at", "ended_at"),
    )


class RunJob(Base):
    """A job entry for a run, used for queueing and distributed execution."""
    __tablename__ = "run_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_until = Column(DateTime(timezone=True), nullable=True)
    owner_id = Column(String(50), nullable=True)
    fence = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        Index("ix_run_jobs_run_id", "run_id"),
        Index("ix_run_jobs_available_at", "available_at"),
        Index("ix_run_jobs_owner_id", "owner_id"),
    )


class Event(Base):
    """An event in a conversation's lifecycle, with sequence numbering."""
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence = Column(Integer, nullable=False)
    type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)
    actor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_events_conversation_sequence"),
        Index("ix_events_conversation_id_sequence", "conversation_id", "sequence"),
    )


class Outbox(Base):
    """Outbox pattern for reliable SSE notifications."""
    __tablename__ = "outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=True,
    )
    type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_outbox_conversation_id", "conversation_id"),
        Index("ix_outbox_processed_at", "processed_at"),
    )