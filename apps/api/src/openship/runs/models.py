import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base
from ..workspace.models import Project  # noqa: F401


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
    # A run's own provider configuration, frozen when it was submitted. Null
    # means "not recorded": a run from before profiles exists, and nothing is
    # guessed for it.
    model_selection = Column(Text, nullable=True)
    model_configuration = Column(Text, nullable=True)
    kind = Column(String(20), nullable=False, server_default="chat")
    graph_version = Column(Integer, nullable=False, server_default="1")
    execution_configuration = Column(Text, nullable=True)
    execution_started_at = Column(DateTime(timezone=True), nullable=True)
    execution_deadline_at = Column(DateTime(timezone=True), nullable=True)
    command_digest = Column(String(64), nullable=True)
    tool_command_json = Column(Text, nullable=True)
    # Digest of the original user-visible submission semantics. Null means
    # this run predates tool-mode request pinning and must remain unknown.
    submission_digest = Column(String(64), nullable=True)
    # Revision of the shared tool policy when a tool-enabled run was queued.
    # Null keeps pre-Phase-5B history explicitly unknown.
    tool_policy_revision = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'queued', 'running', 'waiting_connector', 'completed', 'failed', 'cancelled', 'timed_out', 'stale')",
            name="ck_runs_status",
        ),
        CheckConstraint("kind IN ('chat', 'tool')", name="ck_runs_kind"),
        CheckConstraint("graph_version >= 1", name="ck_runs_graph_version"),
        Index("ix_runs_conversation_id", "conversation_id"),
        Index("ix_runs_project_id", "project_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_ended_at", "ended_at"),
        Index(
            "uq_runs_project_conversation_id",
            "project_id",
            "conversation_id",
            "id",
            unique=True,
        ),
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
    dispatch_generation = Column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        Index("ix_run_jobs_run_id", "run_id"),
        Index("uq_run_jobs_run_id", "run_id", unique=True),
        Index("ix_run_jobs_available_at", "available_at"),
        Index("ix_run_jobs_owner_id", "owner_id"),
    )


class ProviderAttempt(Base):
    """One bounded generation request a worker made for a run.

    Metadata and outcomes only: no prompt, no provider response body, and no
    credential ever belongs here.
    """
    __tablename__ = "provider_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal = Column(Integer, nullable=False)
    fence = Column(Integer, nullable=False)
    profile_id = Column(String(128), nullable=False)
    profile_revision = Column(String(64), nullable=False)
    adapter = Column(String(32), nullable=False)
    requested_model = Column(String(128), nullable=False)
    actual_model = Column(String(128), nullable=True)
    destination_label = Column(String(120), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(32), nullable=False, server_default="started")
    error_code = Column(String(50), nullable=True)
    usage = Column(Text, nullable=True)
    reserved_cost_usd = Column(Numeric(12, 6), nullable=True)
    model_turn_ordinal = Column(Integer, nullable=True)
    reserved_input_tokens = Column(Integer, nullable=True)
    reserved_output_tokens = Column(Integer, nullable=True)
    reserved_total_tokens = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_provider_attempts_run_id_ordinal"),
        CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'cancelled')",
            name="ck_provider_attempts_outcome",
        ),
        CheckConstraint(
            "model_turn_ordinal IS NULL OR model_turn_ordinal >= 0",
            name="ck_provider_attempts_model_turn_ordinal",
        ),
        CheckConstraint(
            "reserved_input_tokens IS NULL OR reserved_input_tokens >= 0",
            name="ck_provider_attempts_reserved_input_tokens",
        ),
        CheckConstraint(
            "reserved_output_tokens IS NULL OR reserved_output_tokens >= 0",
            name="ck_provider_attempts_reserved_output_tokens",
        ),
        CheckConstraint(
            "reserved_total_tokens IS NULL OR reserved_total_tokens >= 0",
            name="ck_provider_attempts_reserved_total_tokens",
        ),
        Index("ix_provider_attempts_run_id", "run_id"),
    )


class RunTranscriptRecord(Base):
    """One complete normalized message in a tool-enabled run."""

    __tablename__ = "run_transcript_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    model_turn_ordinal = Column(Integer, nullable=False)
    message_json = Column(Text, nullable=False)
    logical_call_ids_json = Column(Text, nullable=False, server_default="[]")
    logical_call_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_run_transcript_run_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_run_transcript_ordinal"),
        CheckConstraint("model_turn_ordinal >= 0", name="ck_run_transcript_model_turn"),
        Index("ix_run_transcript_run_turn", "run_id", "model_turn_ordinal", "ordinal"),
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
    """Transactional records waiting for transport publication."""
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
    destination = Column(String(20), nullable=False, server_default="legacy")
    schema_version = Column(Integer, nullable=False, server_default="1")
    message_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    subject = Column(String(255), nullable=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=True)
    dispatch_generation = Column(Integer, nullable=True)
    transport_epoch = Column(UUID(as_uuid=True), nullable=True)
    publish_attempts = Column(Integer, nullable=False, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    publisher_claim_id = Column(UUID(as_uuid=True), nullable=True)
    publisher_lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(160), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "destination IN ('event', 'command', 'control', 'legacy', 'failure')",
            name="ck_outbox_destination",
        ),
        CheckConstraint("schema_version >= 1", name="ck_outbox_schema_version"),
        CheckConstraint("publish_attempts >= 0", name="ck_outbox_publish_attempts"),
        Index("uq_outbox_message_id", "message_id", unique=True),
        Index("ix_outbox_conversation_id", "conversation_id"),
        Index("ix_outbox_processed_at", "processed_at"),
        Index(
            "ix_outbox_publish_due",
            "destination",
            "processed_at",
            "next_attempt_at",
            "publisher_lease_until",
        ),
        Index(
            "uq_outbox_event_destination",
            "event_id",
            unique=True,
            postgresql_where=text("destination = 'event'"),
            sqlite_where=text("destination = 'event'"),
        ),
        Index(
            "uq_outbox_run_generation",
            "run_id",
            "dispatch_generation",
            unique=True,
            postgresql_where=text("destination = 'command'"),
            sqlite_where=text("destination = 'command'"),
        ),
    )
