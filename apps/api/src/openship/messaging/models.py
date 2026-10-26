"""Database state shared by the transactional messaging components."""


from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base


class MessageTransportState(Base):
    """Installation identity and durable admission/execution gates."""

    __tablename__ = "messaging_state"

    id = Column(Integer, primary_key=True)
    installation_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    transport_epoch = Column(UUID(as_uuid=True), nullable=False)
    active = Column(Boolean, nullable=False, default=False, server_default="false")
    admission_open = Column(Boolean, nullable=False, default=True, server_default="true")
    execution_open = Column(Boolean, nullable=False, default=True, server_default="true")
    cutover_id = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_messaging_state_singleton"),
    )


class MessageDisposition(Base):
    """Content-free durable disposition for a delivered transport message."""

    __tablename__ = "message_dispositions"

    message_id = Column(UUID(as_uuid=True), primary_key=True)
    disposition = Column(String(32), nullable=False)
    payload_digest = Column(String(64), nullable=False)
    handler_version = Column(Integer, nullable=False)
    error_code = Column(String(80), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "disposition IN ('claimed', 'completed', 'deferred', 'disposed', 'quarantined')",
            name="ck_message_disposition_kind",
        ),
        CheckConstraint("handler_version >= 1", name="ck_message_handler_version"),
    )
