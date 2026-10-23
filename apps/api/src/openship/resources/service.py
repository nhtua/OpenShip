"""Owned, local-only environment and AWS resource binding commands."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..connectors.contracts import ConnectorConfiguration
from ..connectors.models import Connector, ConnectorRevision, CredentialVersion
from ..workspace.lifecycle import ArchiveStateError, assert_active_project
from ..workspace.models import Project
from ..workspace.projects import ProjectLifecycleError, get_owned_project
from .contracts import (
    EnvironmentCreate,
    EnvironmentSummary,
    ResourceBindingCreate,
    ResourceBindingSummary,
    ResourceObservationSummary,
)
from .models import Environment, ResourceBinding, ResourceObject, ResourceObservation

MAX_ENVIRONMENTS_PER_PROJECT = 10
MAX_BINDINGS_PER_PROJECT = 100
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


class ResourceServiceError(RuntimeError):
    """Closed resource error code safe to project to an authenticated caller."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _ResourceCursor:
    created_at: datetime
    row_id: uuid.UUID


def create_environment(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    request: EnvironmentCreate,
) -> Environment:
    project = _owned_project(db, owner_id=owner_id, project_id=project_id, lock=True)
    _assert_active(project)
    if (
        db.query(Environment.id)
        .filter(Environment.project_id == project.id)
        .count()
        >= MAX_ENVIRONMENTS_PER_PROJECT
    ):
        raise ResourceServiceError("environment_limit_exceeded")
    if (
        db.query(Environment.id)
        .filter(Environment.project_id == project.id, Environment.name == request.name)
        .first()
        is not None
    ):
        raise ResourceServiceError("environment_conflict")
    environment = Environment(
        project_id=project.id,
        name=request.name,
        display_name=request.display_name,
        created_by_user_id=owner_id,
    )
    db.add(environment)
    db.flush()
    return environment


def list_environments(
    db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID
) -> list[EnvironmentSummary]:
    project = _owned_project(db, owner_id=owner_id, project_id=project_id)
    return [
        EnvironmentSummary(
            id=item.id,
            project_id=item.project_id,
            name=item.name,
            display_name=item.display_name,
            created_at=_utc(item.created_at),
        )
        for item in db.query(Environment)
        .filter(Environment.project_id == project.id)
        .order_by(Environment.name.asc(), Environment.id.asc())
        .limit(MAX_ENVIRONMENTS_PER_PROJECT + 1)
        .all()
    ]


def create_resource_binding(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    request: ResourceBindingCreate,
) -> tuple[ResourceObject, ResourceBinding]:
    project = _owned_project(db, owner_id=owner_id, project_id=project_id, lock=True)
    _assert_active(project)
    environment = (
        db.query(Environment)
        .filter(
            Environment.id == request.environment_id,
            Environment.project_id == project.id,
        )
        .with_for_update()
        .first()
    )
    if environment is None:
        raise ResourceServiceError("resource_scope_denied")

    connector = (
        db.query(Connector)
        .filter(
            Connector.id == request.connector_id,
            Connector.kind == "aws",
            Connector.visibility == "project",
            Connector.project_id == project.id,
        )
        .with_for_update()
        .first()
    )
    if connector is None:
        raise ResourceServiceError("resource_scope_denied")
    if connector.revoked_at is not None:
        raise ResourceServiceError("connector_revoked")
    if not connector.enabled:
        raise ResourceServiceError("connector_unavailable")
    if connector.current_revision != request.connector_revision:
        raise ResourceServiceError("connector_revision_changed")

    revision = (
        db.query(ConnectorRevision)
        .filter(
            ConnectorRevision.connector_id == connector.id,
            ConnectorRevision.revision == request.connector_revision,
            ConnectorRevision.project_id == project.id,
            ConnectorRevision.visibility == "project",
        )
        .first()
    )
    if revision is None:
        raise ResourceServiceError("resource_scope_denied")
    credential = (
        db.query(CredentialVersion)
        .filter(
            CredentialVersion.id == revision.credential_version_id,
            CredentialVersion.connector_id == connector.id,
            CredentialVersion.project_id == project.id,
            CredentialVersion.visibility == "project",
            CredentialVersion.kind == "aws_bootstrap",
            CredentialVersion.state == "active",
            CredentialVersion.revoked_at.is_(None),
        )
        .first()
    )
    if credential is None:
        raise ResourceServiceError("connector_unavailable")
    try:
        configuration = ConnectorConfiguration.model_validate_json(
            revision.configuration_json
        )
    except (TypeError, ValueError):
        raise ResourceServiceError("connector_unavailable") from None
    if configuration.kind != "aws" or not configuration.expected_account_id or not configuration.region:
        raise ResourceServiceError("resource_scope_denied")

    if (
        db.query(ResourceBinding.id)
        .filter(
            ResourceBinding.project_id == project.id,
        )
        .count()
        >= MAX_BINDINGS_PER_PROJECT
    ):
        raise ResourceServiceError("resource_limit_exceeded")
    duplicate = (
        db.query(ResourceBinding.id)
        .filter(
            ResourceBinding.project_id == project.id,
            ResourceBinding.environment_id == environment.id,
            ResourceBinding.account_id == configuration.expected_account_id,
            ResourceBinding.region == configuration.region,
            ResourceBinding.bucket_name == request.bucket_name,
            ResourceBinding.state == "active",
        )
        .first()
    )
    if duplicate is not None:
        raise ResourceServiceError("resource_binding_conflict")

    resource_id = uuid.uuid4()
    resource = ResourceObject(
        id=resource_id,
        project_id=project.id,
        kind="aws_s3_bucket",
        uri=f"openship://{project.id}/resource/{resource_id}",
        display_name=request.display_name,
        created_by_user_id=owner_id,
    )
    # Persist the parent before its binding; no ORM relationship orders the inserts.
    db.add(resource)
    db.flush()
    binding = ResourceBinding(
        project_id=project.id,
        resource_object_id=resource_id,
        environment_id=environment.id,
        connector_id=connector.id,
        connector_revision=revision.revision,
        revision=1,
        account_id=configuration.expected_account_id,
        region=configuration.region,
        bucket_name=request.bucket_name,
        bucket_arn=f"arn:aws:s3:::{request.bucket_name}",
        expected_owner=configuration.expected_account_id,
        management_method="external",
        state="active",
        created_by_user_id=owner_id,
    )
    db.add(binding)
    db.flush()
    return resource, binding


def list_resource_bindings(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> tuple[list[ResourceBindingSummary], str | None]:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ResourceServiceError("invalid_page_size")
    project = _owned_project(db, owner_id=owner_id, project_id=project_id)
    query = (
        db.query(ResourceBinding, ResourceObject, Environment)
        .join(
            ResourceObject,
            and_(
                ResourceObject.id == ResourceBinding.resource_object_id,
                ResourceObject.project_id == ResourceBinding.project_id,
            ),
        )
        .join(
            Environment,
            and_(
                Environment.id == ResourceBinding.environment_id,
                Environment.project_id == ResourceBinding.project_id,
            ),
        )
        .filter(
            ResourceBinding.project_id == project.id,
            ResourceBinding.state == "active",
        )
    )
    if cursor:
        decoded = _decode_cursor(cursor)
        query = query.filter(
            or_(
                ResourceBinding.created_at < decoded.created_at,
                and_(
                    ResourceBinding.created_at == decoded.created_at,
                    ResourceBinding.id < decoded.row_id,
                ),
            )
        )
    rows = (
        query.order_by(ResourceBinding.created_at.desc(), ResourceBinding.id.desc())
        .limit(limit + 1)
        .all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        _binding_summary(binding, resource, environment)
        for binding, resource, environment in rows
    ]
    next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id) if more and rows else None
    return items, next_cursor


def get_resource_binding_summary(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> ResourceBindingSummary:
    project = _owned_project(db, owner_id=owner_id, project_id=project_id)
    row = (
        db.query(ResourceBinding, ResourceObject, Environment)
        .join(
            ResourceObject,
            and_(
                ResourceObject.id == ResourceBinding.resource_object_id,
                ResourceObject.project_id == ResourceBinding.project_id,
            ),
        )
        .join(
            Environment,
            and_(
                Environment.id == ResourceBinding.environment_id,
                Environment.project_id == ResourceBinding.project_id,
            ),
        )
        .filter(
            ResourceBinding.id == binding_id,
            ResourceBinding.project_id == project.id,
        )
        .first()
    )
    if row is None:
        raise ResourceServiceError("resource_not_found")
    return _binding_summary(*row)


def list_resource_observations(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> tuple[list[ResourceObservationSummary], str | None]:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ResourceServiceError("invalid_page_size")
    project = _owned_project(db, owner_id=owner_id, project_id=project_id)
    resource = (
        db.query(ResourceObject.id)
        .filter(ResourceObject.id == resource_id, ResourceObject.project_id == project.id)
        .first()
    )
    if resource is None:
        raise ResourceServiceError("resource_not_found")
    query = db.query(ResourceObservation).filter(
        ResourceObservation.project_id == project.id,
        ResourceObservation.resource_object_id == resource_id,
    )
    if cursor:
        decoded = _decode_cursor(cursor)
        query = query.filter(
            or_(
                ResourceObservation.retrieved_at < decoded.created_at,
                and_(
                    ResourceObservation.retrieved_at == decoded.created_at,
                    ResourceObservation.id < decoded.row_id,
                ),
            )
        )
    rows = (
        query.order_by(ResourceObservation.retrieved_at.desc(), ResourceObservation.id.desc())
        .limit(limit + 1)
        .all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_observation_summary(db, row) for row in rows]
    next_cursor = _encode_cursor(rows[-1].retrieved_at, rows[-1].id) if more and rows else None
    return items, next_cursor


def _owned_project(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    lock: bool = False,
) -> Project:
    try:
        return get_owned_project(
            db,
            owner_id=owner_id,
            project_id=project_id,
            for_update=lock,
        )
    except ProjectLifecycleError:
        raise ResourceServiceError("project_not_found") from None


def _assert_active(project: Project) -> None:
    try:
        assert_active_project(project)
    except ArchiveStateError:
        raise ResourceServiceError("project_archived") from None


def _binding_summary(
    binding: ResourceBinding, resource: ResourceObject, environment: Environment
) -> ResourceBindingSummary:
    return ResourceBindingSummary(
        resource_id=resource.id,
        uri=resource.uri,
        display_name=resource.display_name,
        environment_id=environment.id,
        environment_name=environment.name,
        binding_id=binding.id,
        binding_revision=binding.revision,
        connector_id=binding.connector_id,
        connector_revision=binding.connector_revision,
        account_id=binding.account_id,
        region=binding.region,
        bucket_name=binding.bucket_name,
        bucket_arn=binding.bucket_arn,
        expected_owner=binding.expected_owner,
        management_method=binding.management_method,
        state=binding.state,
        created_at=_utc(binding.created_at),
    )


def _observation_summary(
    db: Session, observation: ResourceObservation
) -> ResourceObservationSummary:
    try:
        coverage = json.loads(observation.coverage_json)
    except (TypeError, json.JSONDecodeError):
        raise ResourceServiceError("resource_unavailable") from None
    if not isinstance(coverage, dict):
        raise ResourceServiceError("resource_unavailable")
    evidence = None
    evidence_status = None
    if observation.evidence_artifact_id is not None:
        artifact = (
            db.query(Artifact)
            .filter(
                Artifact.id == observation.evidence_artifact_id,
                Artifact.project_id == observation.project_id,
                Artifact.conversation_id == observation.conversation_id,
            )
            .first()
        )
        if artifact is None:
            raise ResourceServiceError("resource_unavailable")
        evidence = ArtifactRef(
            id=artifact.id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type=artifact.media_type,
        )
        evidence_status = artifact.status
    return ResourceObservationSummary(
        id=observation.id,
        resource_id=observation.resource_object_id,
        binding_id=observation.resource_binding_id,
        binding_revision=observation.binding_revision,
        run_id=observation.run_id,
        operation_id=observation.operation_id,
        source=observation.source,
        retrieved_at=_utc(observation.retrieved_at),
        measurement_at=(
            _utc(observation.measurement_at) if observation.measurement_at else None
        ),
        outcome=observation.outcome,
        coverage=coverage,
        evidence=evidence,
        evidence_status=evidence_status,
    )


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    value = f"{_utc(created_at).isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> _ResourceCursor:
    if not isinstance(value, str) or len(value) > 256:
        raise ResourceServiceError("invalid_cursor")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding).decode("utf-8")
        timestamp, row_id = decoded.split("|", 1)
        created_at = datetime.fromisoformat(timestamp)
        parsed_id = uuid.UUID(row_id)
    except (ValueError, UnicodeDecodeError):
        raise ResourceServiceError("invalid_cursor") from None
    if created_at.tzinfo is None:
        raise ResourceServiceError("invalid_cursor")
    return _ResourceCursor(_utc(created_at), parsed_id)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = [
    "MAX_BINDINGS_PER_PROJECT",
    "MAX_ENVIRONMENTS_PER_PROJECT",
    "ResourceServiceError",
    "create_environment",
    "create_resource_binding",
    "get_resource_binding_summary",
    "list_environments",
    "list_resource_bindings",
    "list_resource_observations",
]
