import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    # Copilot is the migration and creation default. The revision makes
    # preference writes a compare-and-swap rather than last-writer-wins.
    agent_mode = Column(
        String(16), nullable=False, server_default="copilot", default="copilot"
    )
    agent_mode_revision = Column(
        Integer, nullable=False, server_default="1", default=1
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "agent_mode IN ('auto', 'copilot')", name="ck_users_agent_mode"
        ),
        CheckConstraint(
            "agent_mode_revision >= 1", name="ck_users_agent_mode_revision"
        ),
    )
