"""Persistent metadata and quota counters for local immutable artifacts."""

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


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    originating_run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    originating_operation_id = Column(
        UUID(as_uuid=True), ForeignKey("tool_operations.id", ondelete="SET NULL"), nullable=True
    )
    kind = Column(String(32), nullable=False)
    media_type = Column(String(127), nullable=False)
    display_name = Column(String(255), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    relative_storage_key = Column(String(1024), nullable=False)
    status = Column(String(24), nullable=False, server_default="staging")
    mutability = Column(String(16), nullable=False, server_default="immutable")
    source = Column(String(32), nullable=False)
    provenance = Column(String(2048), nullable=True)
    request_key = Column(String(255), nullable=True)
    reserved_bytes = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('search_result', 'upload')", name="ck_artifacts_kind"),
        CheckConstraint(
            "media_type IN ('application/json', 'text/plain')", name="ck_artifacts_media_type"
        ),
        CheckConstraint("size_bytes >= 0 AND reserved_bytes >= 0", name="ck_artifacts_size"),
        CheckConstraint(
            "status IN ('staging', 'ready', 'deleting', 'deleted', 'unavailable')",
            name="ck_artifacts_status",
        ),
        CheckConstraint("mutability = 'immutable'", name="ck_artifacts_mutability"),
        UniqueConstraint("relative_storage_key", name="uq_artifacts_storage_key"),
        Index("ix_artifacts_conversation_created", "conversation_id", "created_at", "id"),
        Index("ix_artifacts_project_status", "project_id", "status"),
        Index(
            "uq_artifacts_conversation_request_key",
            "conversation_id",
            "request_key",
            unique=True,
            postgresql_where=(request_key.is_not(None)),
            sqlite_where=(request_key.is_not(None)),
        ),
    )


class ArtifactQuota(Base):
    __tablename__ = "artifact_quotas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope = Column(String(16), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    committed_bytes = Column(Integer, nullable=False, server_default="0")
    reserved_bytes = Column(Integer, nullable=False, server_default="0")
    committed_count = Column(Integer, nullable=False, server_default="0")
    reserved_count = Column(Integer, nullable=False, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope IN ('project', 'conversation')", name="ck_artifact_quotas_scope"),
        CheckConstraint(
            "(scope = 'project' AND conversation_id IS NULL) OR "
            "(scope = 'conversation' AND conversation_id IS NOT NULL)",
            name="ck_artifact_quotas_owner_shape",
        ),
        CheckConstraint(
            "committed_bytes >= 0 AND reserved_bytes >= 0 AND committed_count >= 0 AND reserved_count >= 0",
            name="ck_artifact_quotas_counters",
        ),
        Index("uq_artifact_quotas_project_scope", "project_id", unique=True, postgresql_where=(scope == "project"), sqlite_where=(scope == "project")),
        Index(
            "uq_artifact_quotas_conversation_scope",
            "conversation_id",
            unique=True,
            postgresql_where=(scope == "conversation"),
            sqlite_where=(scope == "conversation"),
        ),
    )
