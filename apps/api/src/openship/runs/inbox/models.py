"""Durable admission order and immutable routing for conversation messages."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from ...auth.models import Base


class ConversationInboxEntry(Base):
    __tablename__ = "conversation_inbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    client_request_id = Column(String(255), nullable=False)
    request_digest = Column(String(64), nullable=False)
    sequence = Column(Integer, nullable=False)
    disposition = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False)
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("task_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_json = Column(Text, nullable=False)
    failure_code = Column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_inbox_conversation_request"
        ),
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_inbox_conversation_sequence"
        ),
        CheckConstraint(
            "disposition IN ('clarification', 'queued')", name="ck_inbox_disposition"
        ),
        CheckConstraint(
            "status IN ('attached', 'queued', 'promoted', 'failed')",
            name="ck_inbox_status",
        ),
        Index(
            "ix_inbox_conversation_status_sequence",
            "conversation_id",
            "status",
            "sequence",
        ),
    )
