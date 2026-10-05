import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, CheckConstraint, UniqueConstraint, func
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