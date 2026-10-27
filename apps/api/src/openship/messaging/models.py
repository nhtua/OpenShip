"""Database state shared by the transactional messaging components."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
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
