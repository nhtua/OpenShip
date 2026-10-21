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


class Project(Base):
    """A project owned by a user. All conversations and runs belong to a project."""
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("owner_user_id", name="uq_projects_owner_user"),
        Index("ix_projects_owner_user_id", "owner_user_id"),
    )


class ConversationWorkspace(Base):
    """Stable association between one owned conversation and artifact storage."""

    __tablename__ = "conversation_workspaces"

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
    schema_version = Column(Integer, nullable=False, server_default="1")
    relative_root = Column(String(255), nullable=False)
    storage_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(16), nullable=False, server_default="available")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_workspaces_conversation"),
        CheckConstraint("schema_version = 1", name="ck_conversation_workspaces_schema_version"),
        CheckConstraint(
            "status IN ('available', 'unavailable', 'deleted')",
            name="ck_conversation_workspaces_status",
        ),
        Index("ix_conversation_workspaces_project_id", "project_id"),
    )
