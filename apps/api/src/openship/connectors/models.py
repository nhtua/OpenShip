"""Durable scope, revision, encrypted-secret and audit records."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from ..auth.models import Base
from ..workspace.models import Project  # noqa: F401


class Connector(Base):
    """Stable, explicitly scoped provider connection; secret values live elsewhere."""

    __tablename__ = "connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(32), nullable=False)
    visibility = Column(String(16), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name = Column(String(255), nullable=False)
    current_revision = Column(Integer, nullable=False, server_default="1")
    enabled = Column(Boolean, nullable=False, server_default="true")
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    managed_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('brave', 'tavily', 'aws')", name="ck_connectors_kind"),
        CheckConstraint("visibility IN ('global', 'project')", name="ck_connectors_visibility"),
        CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connectors_owner_shape",
        ),
        CheckConstraint(
            "NOT (kind = 'aws' AND visibility = 'global')",
            name="ck_connectors_aws_project_only",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 255", name="ck_connectors_name"),
        CheckConstraint("current_revision >= 1", name="ck_connectors_current_revision"),
        Index("ix_connectors_project_kind", "project_id", "kind"),
        Index("ix_connectors_visibility_kind", "visibility", "kind"),
    )


class CredentialVersion(Base):
    """Encrypted, bounded payload for one Connector-owned credential version."""

    __tablename__ = "connector_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id = Column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="RESTRICT"), nullable=False
    )
    visibility = Column(String(16), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    kind = Column(String(64), nullable=False)
    schema_version = Column(Integer, nullable=False, server_default="1")
    key_id = Column(String(64), nullable=False)
    nonce = Column(LargeBinary, nullable=False)
    ciphertext = Column(LargeBinary, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    state = Column(String(16), nullable=False, server_default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("connector_id", "id", name="uq_connector_credentials_connector_id"),
        CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_credentials_visibility"),
        CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_credentials_owner_shape",
        ),
        CheckConstraint(
            "kind IN ('brave_api_key', 'tavily_api_key', 'aws_bootstrap')",
            name="ck_connector_credentials_kind",
        ),
        CheckConstraint(
            "NOT (kind = 'aws_bootstrap' AND visibility = 'global')",
            name="ck_connector_credentials_aws_project_only",
        ),
        CheckConstraint("schema_version = 1", name="ck_connector_credentials_schema_version"),
        CheckConstraint("length(key_id) BETWEEN 1 AND 64", name="ck_connector_credentials_key_id"),
        CheckConstraint("length(nonce) = 12", name="ck_connector_credentials_nonce"),
        CheckConstraint(
            "length(ciphertext) BETWEEN 16 AND 16400",
            name="ck_connector_credentials_ciphertext_size",
        ),
        CheckConstraint("state IN ('active', 'revoked')", name="ck_connector_credentials_state"),
        Index("ix_connector_credentials_connector_state", "connector_id", "state"),
    )


class ConnectorRevision(Base):
    """Immutable non-secret configuration revision bound to one credential version."""

    __tablename__ = "connector_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id = Column(UUID(as_uuid=True), nullable=False)
    revision = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, server_default="1")
    visibility = Column(String(16), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    configuration_json = Column(Text, nullable=False)
    configuration_digest = Column(String(64), nullable=False)
    credential_version_id = Column(UUID(as_uuid=True), nullable=False)
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "credential_version_id"],
            ["connector_credentials.connector_id", "connector_credentials.id"],
            ondelete="RESTRICT",
            name="fk_connector_revisions_credential_owner",
        ),
        UniqueConstraint("connector_id", "revision", name="uq_connector_revisions_revision"),
        Index(
            "uq_connector_revisions_project_connector_revision",
            "project_id",
            "connector_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_connector_revisions_project_connector_revision_credential",
            "project_id",
            "connector_id",
            "revision",
            "credential_version_id",
            unique=True,
        ),
        CheckConstraint("revision >= 1", name="ck_connector_revisions_revision"),
        CheckConstraint("schema_version = 1", name="ck_connector_revisions_schema_version"),
        CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_revisions_visibility"),
        CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_revisions_owner_shape",
        ),
        CheckConstraint("length(configuration_json) <= 16384", name="ck_connector_revisions_config_size"),
        CheckConstraint("length(configuration_digest) = 64", name="ck_connector_revisions_config_digest"),
    )


class ProjectConnectorDefault(Base):
    """One project-wide search default with optimistic configuration revisioning."""

    __tablename__ = "project_connector_defaults"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    service = Column(String(16), nullable=False)
    connector_id = Column(UUID(as_uuid=True), nullable=True)
    connector_revision = Column(Integer, nullable=True)
    settings_revision = Column(Integer, nullable=False, server_default="1")
    updated_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "connector_revision"],
            ["connector_revisions.connector_id", "connector_revisions.revision"],
            ondelete="RESTRICT",
            name="fk_project_connector_defaults_revision",
        ),
        UniqueConstraint("project_id", name="uq_project_connector_defaults_project"),
        CheckConstraint("service IN ('brave', 'tavily')", name="ck_project_connector_defaults_service"),
        CheckConstraint("connector_revision >= 1", name="ck_project_connector_defaults_connector_revision"),
        CheckConstraint("settings_revision >= 1", name="ck_project_connector_defaults_settings_revision"),
        CheckConstraint(
            "(connector_id IS NULL AND connector_revision IS NULL) OR "
            "(connector_id IS NOT NULL AND connector_revision IS NOT NULL)",
            name="ck_project_connector_defaults_target_shape",
        ),
        Index("ix_project_connector_defaults_connector", "connector_id"),
    )


class ConnectorRequestLedger(Base):
    """Short-lived idempotency records containing keyed digests and safe results only."""

    __tablename__ = "connector_request_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visibility = Column(String(16), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    connector_id = Column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True
    )
    key_id = Column(String(64), nullable=False)
    request_key_hmac = Column(String(64), nullable=False)
    payload_hmac = Column(String(64), nullable=False)
    result_json = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_request_ledger_visibility"),
        CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_request_ledger_owner_shape",
        ),
        CheckConstraint("length(key_id) BETWEEN 1 AND 64", name="ck_connector_request_ledger_key_id"),
        CheckConstraint("length(request_key_hmac) = 64", name="ck_connector_request_ledger_key_hmac"),
        CheckConstraint("length(payload_hmac) = 64", name="ck_connector_request_ledger_payload_hmac"),
        Index("ix_connector_request_ledger_expires_at", "expires_at"),
        Index(
            "uq_connector_request_ledger_global_key",
            "actor_user_id",
            "key_id",
            "request_key_hmac",
            unique=True,
            postgresql_where=(visibility == "global"),
            sqlite_where=(visibility == "global"),
        ),
        Index(
            "uq_connector_request_ledger_project_key",
            "project_id",
            "actor_user_id",
            "key_id",
            "request_key_hmac",
            unique=True,
            postgresql_where=(visibility == "project"),
            sqlite_where=(visibility == "project"),
        ),
    )


class ConnectorAuditEvent(Base):
    """Append-only audit record at installation-global or project scope."""

    __tablename__ = "connector_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visibility = Column(String(16), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    consuming_project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    connector_id = Column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True
    )
    connector_revision = Column(Integer, nullable=True)
    actor_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action = Column(String(48), nullable=False)
    outcome = Column(String(24), nullable=False)
    details_json = Column(Text, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("visibility IN ('global', 'project')", name="ck_connector_audit_visibility"),
        CheckConstraint(
            "(visibility = 'global' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_connector_audit_owner_shape",
        ),
        CheckConstraint("length(action) BETWEEN 1 AND 48", name="ck_connector_audit_action"),
        CheckConstraint("length(outcome) BETWEEN 1 AND 24", name="ck_connector_audit_outcome"),
        CheckConstraint("length(details_json) <= 16384", name="ck_connector_audit_details_size"),
        Index("ix_connector_audit_project_created", "project_id", "created_at"),
        Index("ix_connector_audit_connector_created", "connector_id", "created_at"),
    )


class ConnectorAvailability(Base):
    """Fresh readiness for one consuming project and worker, independent of permission."""

    __tablename__ = "connector_availability"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connector_id = Column(UUID(as_uuid=True), nullable=False)
    connector_revision = Column(Integer, nullable=False)
    worker_id = Column(String(128), nullable=False)
    available = Column(Boolean, nullable=False)
    reason = Column(String(48), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "connector_revision"],
            ["connector_revisions.connector_id", "connector_revisions.revision"],
            ondelete="CASCADE",
            name="fk_connector_availability_revision",
        ),
        UniqueConstraint(
            "project_id",
            "connector_id",
            "connector_revision",
            "worker_id",
            name="uq_connector_availability_project_worker",
        ),
        CheckConstraint("length(worker_id) BETWEEN 1 AND 128", name="ck_connector_availability_worker"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('missing_credentials', 'expired', 'revoked', 'runtime_unavailable', 'configuration_error')",
            name="ck_connector_availability_reason",
        ),
        CheckConstraint("available OR reason IS NOT NULL", name="ck_connector_availability_reason_required"),
        Index("ix_connector_availability_observed_at", "observed_at"),
    )


def _immutable_revision_update(_mapper, _connection, _target) -> None:
    raise ValueError("Connector revisions are immutable")


def _immutable_audit_update(_mapper, _connection, _target) -> None:
    raise ValueError("Connector audit events are append-only")


event.listen(ConnectorRevision, "before_update", _immutable_revision_update)
event.listen(ConnectorAuditEvent, "before_update", _immutable_audit_update)
