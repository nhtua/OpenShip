"""Project-owned environments, resource bindings and cloud observations."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from ..artifacts.models import Artifact  # noqa: F401
from ..auth.models import Base, User  # noqa: F401
from ..chat.models import Conversation  # noqa: F401
from ..connectors.models import ConnectorRevision  # noqa: F401
from ..execution.models import ToolOperation  # noqa: F401
from ..runs.models import Run  # noqa: F401
from ..workspace.models import Project  # noqa: F401


class Environment(Base):
    """A non-authoritative label for grouping project resource bindings."""

    __tablename__ = "environments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    name = Column(String(64), nullable=False)
    display_name = Column(String(255), nullable=False)
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_environments_project_name"),
        Index("uq_environments_project_id", "project_id", "id", unique=True),
        CheckConstraint("length(name) BETWEEN 1 AND 64", name="ck_environments_name_length"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 255",
            name="ck_environments_display_name_length",
        ),
    )


class ResourceObject(Base):
    """Stable project-owned identity for an existing cloud resource."""

    __tablename__ = "resource_objects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    kind = Column(String(32), nullable=False)
    uri = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("uq_resource_objects_project_id", "project_id", "id", unique=True),
        UniqueConstraint("project_id", "uri", name="uq_resource_objects_project_uri"),
        CheckConstraint("kind = 'aws_s3_bucket'", name="ck_resource_objects_kind"),
        CheckConstraint("length(uri) BETWEEN 1 AND 255", name="ck_resource_objects_uri"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 255",
            name="ck_resource_objects_display_name",
        ),
    )


class ResourceBinding(Base):
    """One immutable target revision; state may move active → retired only."""

    __tablename__ = "resource_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    resource_object_id = Column(UUID(as_uuid=True), nullable=False)
    environment_id = Column(UUID(as_uuid=True), nullable=False)
    connector_id = Column(UUID(as_uuid=True), nullable=False)
    connector_revision = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, server_default="1")
    account_id = Column(String(12), nullable=False)
    region = Column(String(64), nullable=False)
    bucket_name = Column(String(63), nullable=False)
    bucket_arn = Column(String(255), nullable=False)
    expected_owner = Column(String(12), nullable=False)
    management_method = Column(String(16), nullable=False, server_default="external")
    state = Column(String(16), nullable=False, server_default="active")
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retired_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "resource_object_id"],
            ["resource_objects.project_id", "resource_objects.id"],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_object",
        ),
        ForeignKeyConstraint(
            ["project_id", "environment_id"],
            ["environments.project_id", "environments.id"],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_environment",
        ),
        ForeignKeyConstraint(
            ["project_id", "connector_id", "connector_revision"],
            [
                "connector_revisions.project_id",
                "connector_revisions.connector_id",
                "connector_revisions.revision",
            ],
            ondelete="RESTRICT",
            name="fk_resource_bindings_project_connector_revision",
        ),
        Index("uq_resource_bindings_project_id", "project_id", "id", unique=True),
        Index(
            "uq_resource_bindings_object_environment_revision",
            "project_id",
            "resource_object_id",
            "environment_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_resource_bindings_observation_reference",
            "project_id",
            "resource_object_id",
            "id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_resource_bindings_active_object_environment",
            "project_id",
            "resource_object_id",
            "environment_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        Index(
            "uq_resource_bindings_active_target",
            "project_id",
            "environment_id",
            "account_id",
            "region",
            "bucket_name",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        Index("ix_resource_bindings_connector", "project_id", "connector_id", "connector_revision"),
        CheckConstraint("revision >= 1", name="ck_resource_bindings_revision"),
        CheckConstraint("connector_revision >= 1", name="ck_resource_bindings_connector_revision"),
        CheckConstraint("length(account_id) = 12", name="ck_resource_bindings_account_id"),
        CheckConstraint("length(expected_owner) = 12", name="ck_resource_bindings_expected_owner"),
        CheckConstraint("account_id = expected_owner", name="ck_resource_bindings_owner_matches_account"),
        CheckConstraint("length(region) BETWEEN 1 AND 64", name="ck_resource_bindings_region"),
        CheckConstraint("length(bucket_name) BETWEEN 3 AND 63", name="ck_resource_bindings_bucket_name"),
        CheckConstraint("length(bucket_arn) BETWEEN 1 AND 255", name="ck_resource_bindings_bucket_arn"),
        CheckConstraint(
            "management_method IN ('external', 'unknown')",
            name="ck_resource_bindings_management_method",
        ),
        CheckConstraint("state IN ('active', 'retired')", name="ck_resource_bindings_state"),
        CheckConstraint(
            "(state = 'active' AND retired_at IS NULL) OR "
            "(state = 'retired' AND retired_at IS NOT NULL)",
            name="ck_resource_bindings_retired_shape",
        ),
    )


class ResourceObservation(Base):
    """Immutable, secret-free result metadata linked to one run and operation."""

    __tablename__ = "resource_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    resource_object_id = Column(UUID(as_uuid=True), nullable=False)
    resource_binding_id = Column(UUID(as_uuid=True), nullable=False)
    binding_revision = Column(Integer, nullable=False)
    source = Column(String(32), nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    measurement_at = Column(DateTime(timezone=True), nullable=True)
    coverage_json = Column(Text, nullable=False, server_default="{}")
    outcome = Column(String(16), nullable=False)
    evidence_artifact_id = Column(UUID(as_uuid=True), nullable=True)
    evidence_sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "conversation_id", "run_id"],
            ["runs.project_id", "runs.conversation_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_run",
        ),
        ForeignKeyConstraint(
            ["project_id", "conversation_id", "operation_id"],
            ["tool_operations.project_id", "tool_operations.conversation_id", "tool_operations.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_operation",
        ),
        ForeignKeyConstraint(
            ["project_id", "resource_object_id"],
            ["resource_objects.project_id", "resource_objects.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_object",
        ),
        ForeignKeyConstraint(
            ["project_id", "resource_object_id", "resource_binding_id", "binding_revision"],
            [
                "resource_bindings.project_id",
                "resource_bindings.resource_object_id",
                "resource_bindings.id",
                "resource_bindings.revision",
            ],
            ondelete="RESTRICT",
            name="fk_resource_observations_project_binding",
        ),
        ForeignKeyConstraint(
            ["project_id", "conversation_id", "evidence_artifact_id"],
            ["artifacts.project_id", "artifacts.conversation_id", "artifacts.id"],
            ondelete="RESTRICT",
            name="fk_resource_observations_conversation_artifact",
        ),
        Index("uq_resource_observations_project_id", "project_id", "id", unique=True),
        Index("ix_resource_observations_resource_retrieved", "project_id", "resource_object_id", "retrieved_at", "id"),
        Index("ix_resource_observations_conversation_created", "conversation_id", "created_at"),
        CheckConstraint("binding_revision >= 1", name="ck_resource_observations_binding_revision"),
        CheckConstraint("length(source) BETWEEN 1 AND 32", name="ck_resource_observations_source"),
        CheckConstraint(
            "length(coverage_json) <= 16384",
            name="ck_resource_observations_coverage_size",
        ),
        CheckConstraint(
            "outcome IN ('measured', 'no_data', 'partial', 'failed')",
            name="ck_resource_observations_outcome",
        ),
        CheckConstraint(
            "(evidence_artifact_id IS NULL AND evidence_sha256 IS NULL) OR "
            "(evidence_artifact_id IS NOT NULL AND length(evidence_sha256) = 64)",
            name="ck_resource_observations_evidence_shape",
        ),
    )


class ConnectorBlocker(Base):
    """One exact pre-dispatch connector wait for a run; never an approval grant."""

    __tablename__ = "connector_blockers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    connector_id = Column(UUID(as_uuid=True), nullable=False)
    connector_revision = Column(Integer, nullable=False)
    credential_version_id = Column(UUID(as_uuid=True), nullable=False)
    configuration_digest = Column(String(64), nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String(48), nullable=False)
    state = Column(String(16), nullable=False, server_default="waiting")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "conversation_id", "run_id"],
            ["runs.project_id", "runs.conversation_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_connector_blockers_project_run",
        ),
        ForeignKeyConstraint(
            ["project_id", "connector_id", "connector_revision", "credential_version_id"],
            [
                "connector_revisions.project_id",
                "connector_revisions.connector_id",
                "connector_revisions.revision",
                "connector_revisions.credential_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_connector_blockers_connector_revision",
        ),
        UniqueConstraint("project_id", "request_id", name="uq_connector_blockers_project_request"),
        Index(
            "uq_connector_blockers_waiting_run",
            "project_id",
            "run_id",
            unique=True,
            postgresql_where=text("state = 'waiting'"),
            sqlite_where=text("state = 'waiting'"),
        ),
        Index("ix_connector_blockers_expiry", "state", "expires_at"),
        CheckConstraint("connector_revision >= 1", name="ck_connector_blockers_revision"),
        CheckConstraint("length(configuration_digest) = 64", name="ck_connector_blockers_digest"),
        CheckConstraint(
            "reason IN ('connector_required', 'connector_ambiguous', 'connector_unavailable', "
            "'connector_expired', 'credential_store_unavailable')",
            name="ck_connector_blockers_reason",
        ),
        CheckConstraint(
            "state IN ('waiting', 'resumed', 'cancelled', 'dismissed', 'expired')",
            name="ck_connector_blockers_state",
        ),
        CheckConstraint(
            "(state = 'waiting' AND consumed_at IS NULL AND closed_at IS NULL) OR "
            "(state = 'resumed' AND consumed_at IS NOT NULL AND closed_at IS NOT NULL) OR "
            "(state IN ('cancelled', 'dismissed', 'expired') AND consumed_at IS NULL "
            "AND closed_at IS NOT NULL)",
            name="ck_connector_blockers_closed_shape",
        ),
    )


__all__ = [
    "ConnectorBlocker",
    "Environment",
    "ResourceBinding",
    "ResourceObject",
    "ResourceObservation",
]
