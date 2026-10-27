"""Database state shared by the transactional messaging components."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class MessageTransportState(Base):
    """Installation identity and durable admission/execution gates."""

    __tablename__ = "messaging_state"

    id = Column(Integer, primary_key=True)
    installation_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    transport_epoch = Column(UUID(as_uuid=True), nullable=False)
    active = Column(Boolean, nullable=False, default=False, server_default="false")
    admission_open = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    execution_open = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cutover_id = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (CheckConstraint("id = 1", name="ck_messaging_state_singleton"),)


class RetentionTask(Base):
    """Durable operator request and outcome for bounded retention work."""

    __tablename__ = "retention_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True)
    requested_by = Column(String(255), nullable=False)
    event_retention_days = Column(Integer, nullable=False)
    checkpoint_retention_days = Column(Integer, nullable=False)
    messaging_retention_days = Column(Integer, nullable=False)
    batch_size = Column(Integer, nullable=False)
    dry_run = Column(Boolean, nullable=False, server_default="false")
    state = Column(String(16), nullable=False, server_default="requested")
    attempts = Column(Integer, nullable=False, server_default="0")
    requested_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    consumer_acked_at = Column(DateTime(timezone=True), nullable=True)
    consumer_stream_sequence = Column(BigInteger, nullable=True)
    consumer_sequence = Column(BigInteger, nullable=True)
    ack_pending_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(80), nullable=True)
    result_json = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "length(trim(requested_by)) BETWEEN 1 AND 255",
            name="ck_retention_requested_by",
        ),
        CheckConstraint(
            "event_retention_days BETWEEN 1 AND 36500", name="ck_retention_event_days"
        ),
        CheckConstraint(
            "checkpoint_retention_days BETWEEN 1 AND 36500",
            name="ck_retention_checkpoint_days",
        ),
        CheckConstraint(
            "messaging_retention_days BETWEEN 1 AND 36500",
            name="ck_retention_messaging_days",
        ),
        CheckConstraint(
            "batch_size BETWEEN 1 AND 10000", name="ck_retention_batch_size"
        ),
        CheckConstraint(
            "state IN ('requested', 'running', 'completed', 'failed')",
            name="ck_retention_task_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_retention_task_attempts"),
        CheckConstraint(
            "state = 'failed' OR error_code IS NULL", name="ck_retention_task_error"
        ),
        CheckConstraint(
            "(consumer_stream_sequence IS NULL AND consumer_sequence IS NULL "
            "AND ack_pending_at IS NULL AND consumer_acked_at IS NULL) OR "
            "(consumer_stream_sequence > 0 AND consumer_sequence > 0 "
            "AND ack_pending_at IS NOT NULL)",
            name="ck_retention_task_ack_intent",
        ),
        CheckConstraint(
            "consumer_acked_at IS NULL OR state IN ('completed', 'failed')",
            name="ck_retention_task_ack_state",
        ),
        Index(
            "ix_retention_task_ack_reconcile",
            "consumer_acked_at",
            "consumer_sequence",
            "consumer_stream_sequence",
            postgresql_where=text("consumer_acked_at IS NULL"),
            sqlite_where=text("consumer_acked_at IS NULL"),
        ),
    )


class MessageDisposition(Base):
    """Content-free durable disposition for a delivered transport message."""

    __tablename__ = "message_dispositions"

    message_id = Column(UUID(as_uuid=True), primary_key=True)
    disposition = Column(String(32), nullable=False)
    payload_digest = Column(String(64), nullable=False)
    handler_version = Column(Integer, nullable=False)
    error_code = Column(String(80), nullable=True)
    observed_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "disposition IN ('claimed', 'completed', 'deferred', 'disposed', 'quarantined')",
            name="ck_message_disposition_kind",
        ),
        CheckConstraint("handler_version >= 1", name="ck_message_handler_version"),
    )


class MessageFailure(Base):
    """Content-free canonical failure keyed to one source delivery."""

    __tablename__ = "message_failures"

    id = Column(UUID(as_uuid=True), primary_key=True)
    installation_id = Column(UUID(as_uuid=True), nullable=False)
    source_transport_epoch = Column(UUID(as_uuid=True), nullable=False)
    source_stream = Column(String(64), nullable=False)
    source_sequence = Column(BigInteger, nullable=False)
    source_timestamp = Column(DateTime(timezone=True), nullable=False)
    body_digest = Column(String(64), nullable=False)
    classification = Column(String(20), nullable=False)
    error_code = Column(String(80), nullable=False)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    run_id = Column(UUID(as_uuid=True), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            name="uq_message_failure_source",
        ),
        CheckConstraint("source_stream = 'OS_RUNS'", name="ck_message_failure_stream"),
        CheckConstraint("source_sequence > 0", name="ck_message_failure_sequence"),
        CheckConstraint("length(body_digest) = 64", name="ck_message_failure_digest"),
        CheckConstraint(
            "classification IN ('quarantine', 'reconcile')",
            name="ck_message_failure_classification",
        ),
        CheckConstraint(
            "length(error_code) BETWEEN 1 AND 80",
            name="ck_message_failure_error_code",
        ),
    )


class MessageFailureConflict(Base):
    """Content-free observation of a conflicting delivery identity."""

    __tablename__ = "message_failure_conflicts"

    id = Column(UUID(as_uuid=True), primary_key=True)
    installation_id = Column(UUID(as_uuid=True), nullable=False)
    source_transport_epoch = Column(UUID(as_uuid=True), nullable=False)
    source_stream = Column(String(64), nullable=False)
    source_sequence = Column(BigInteger, nullable=False)
    source_timestamp = Column(DateTime(timezone=True), nullable=False)
    observed_digest = Column(String(64), nullable=False)
    observed_classification = Column(String(20), nullable=False)
    observed_error_code = Column(String(80), nullable=False)
    conflict_code = Column(String(40), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            "observed_digest",
            "observed_classification",
            "observed_error_code",
            name="uq_message_failure_conflict_observation",
        ),
        CheckConstraint("source_stream = 'OS_RUNS'", name="ck_failure_conflict_stream"),
        CheckConstraint("source_sequence > 0", name="ck_failure_conflict_sequence"),
        CheckConstraint(
            "length(observed_digest) = 64", name="ck_failure_conflict_digest"
        ),
        CheckConstraint(
            "observed_classification IN ('quarantine', 'reconcile')",
            name="ck_failure_conflict_classification",
        ),
        CheckConstraint(
            "length(observed_error_code) BETWEEN 1 AND 80",
            name="ck_failure_conflict_error_code",
        ),
        CheckConstraint(
            "conflict_code IN ('source_digest_conflict', 'failure_row_mismatch', 'outbox_missing')",
            name="ck_failure_conflict_code",
        ),
    )


class MessageExhaustion(Base):
    """Content-free observation of a MaxDeliver-exhausted run command."""

    __tablename__ = "message_exhaustions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    installation_id = Column(UUID(as_uuid=True), nullable=False)
    source_transport_epoch = Column(UUID(as_uuid=True), nullable=False)
    source_stream = Column(String(64), nullable=False)
    source_sequence = Column(BigInteger, nullable=False)
    source_timestamp = Column(DateTime(timezone=True), nullable=True)
    consumer_name = Column(String(64), nullable=False)
    delivery_count = Column(Integer, nullable=False)
    discovery_method = Column(
        String(24), nullable=False, default="advisory", server_default="advisory"
    )
    recovery_operator = Column(String(255), nullable=True)
    state = Column(String(32), nullable=False)
    payload_digest = Column(String(64), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    run_id = Column(UUID(as_uuid=True), nullable=True)
    dispatch_generation = Column(Integer, nullable=True)
    owner_id = Column(String(50), nullable=True)
    owner_fence = Column(Integer, nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    next_check_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(80), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "source_transport_epoch",
            "source_stream",
            "source_sequence",
            name="uq_message_exhaustion_source",
        ),
        CheckConstraint("source_stream = 'OS_RUNS'", name="ck_exhaustion_stream"),
        CheckConstraint("source_sequence > 0", name="ck_exhaustion_sequence"),
        CheckConstraint("consumer_name = 'run-v1'", name="ck_exhaustion_consumer"),
        CheckConstraint(
            "delivery_count >= 0 AND "
            "(discovery_method = 'capacity_scan' OR delivery_count > 0)",
            name="ck_exhaustion_deliveries",
        ),
        CheckConstraint(
            "discovery_method IN ('advisory', 'capacity_scan')",
            name="ck_exhaustion_discovery_method",
        ),
        CheckConstraint(
            "(discovery_method = 'capacity_scan' AND recovery_operator IS NOT NULL "
            "AND length(trim(recovery_operator)) BETWEEN 1 AND 255) OR "
            "(discovery_method = 'advisory' AND recovery_operator IS NULL)",
            name="ck_exhaustion_recovery_operator",
        ),
        CheckConstraint(
            "state IN ('pending', 'watching_live_owner', 'settling', 'resolved', "
            "'source_missing', 'blocked')",
            name="ck_exhaustion_state",
        ),
        CheckConstraint(
            "payload_digest IS NULL OR length(payload_digest) = 64",
            name="ck_exhaustion_digest",
        ),
        CheckConstraint(
            "dispatch_generation IS NULL OR dispatch_generation > 0",
            name="ck_exhaustion_generation",
        ),
        CheckConstraint(
            "owner_fence IS NULL OR owner_fence > 0", name="ck_exhaustion_owner_fence"
        ),
        CheckConstraint(
            "state <> 'watching_live_owner' OR "
            "(run_id IS NOT NULL AND dispatch_generation IS NOT NULL "
            "AND owner_id IS NOT NULL AND owner_fence IS NOT NULL "
            "AND lease_until IS NOT NULL AND next_check_at IS NOT NULL)",
            name="ck_exhaustion_live_owner_fields",
        ),
        Index("ix_message_exhaustions_due", "state", "next_check_at"),
    )
