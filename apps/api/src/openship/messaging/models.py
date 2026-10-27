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
    UniqueConstraint,
    func,
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
        CheckConstraint("delivery_count > 0", name="ck_exhaustion_deliveries"),
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
