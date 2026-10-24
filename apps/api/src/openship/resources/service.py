"""Historical resource reads and binding-free AWS observation publication."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    canonical_json,
)
from ..connectors.models import Connector, ConnectorRevision
from ..execution.models import ToolOperation
from ..tools.aws.contracts import AwsReadObservation
from ..workspace.models import Project
from ..workspace.projects import ProjectLifecycleError, get_owned_project
from .contracts import (
    EnvironmentSummary,
    ResourceBindingSummary,
    ResourceObservationSummary,
)
from .models import Environment, ResourceBinding, ResourceObject, ResourceObservation

MAX_ENVIRONMENTS_PER_PROJECT = 10
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


def authorize_resource_project(
    db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Check ownership before returning the retired setup response."""
    _owned_project(db, owner_id=owner_id, project_id=project_id)


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
        if db.get_bind().dialect.name == "sqlite":
            binding_time = func.strftime(
                "%Y-%m-%d %H:%M:%f", ResourceBinding.created_at
            )
            cursor_time = func.strftime("%Y-%m-%d %H:%M:%f", decoded.created_at)
        else:
            binding_time = ResourceBinding.created_at
            cursor_time = decoded.created_at
        query = query.filter(
            or_(
                binding_time < cursor_time,
                and_(
                    binding_time == cursor_time,
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
    next_cursor = (
        _encode_cursor(rows[-1][0].created_at, rows[-1][0].id)
        if more and rows
        else None
    )
    return items, next_cursor


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
        .filter(
            ResourceObject.id == resource_id, ResourceObject.project_id == project.id
        )
        .first()
    )
    if resource is None:
        raise ResourceServiceError("resource_not_found")
    query = db.query(ResourceObservation).filter(
        ResourceObservation.project_id == project.id,
        ResourceObservation.resource_object_id == resource_id,
        ResourceObservation.schema_version == 1,
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
        query.order_by(
            ResourceObservation.retrieved_at.desc(), ResourceObservation.id.desc()
        )
        .limit(limit + 1)
        .all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_observation_summary(db, row) for row in rows]
    next_cursor = (
        _encode_cursor(rows[-1].retrieved_at, rows[-1].id) if more and rows else None
    )
    return items, next_cursor


def record_aws_read_observation(
    db: Session,
    *,
    authorization: AuthorizationContext,
    operation: ToolOperation,
    observation: AwsReadObservation,
    artifact: ArtifactRef,
) -> ResourceObservation:
    """Stage one binding-free AWS observation inside the caller's transaction.

    This helper performs only database validation and model staging. Artifact
    bytes are written by the publication pipeline, and the caller owns commit
    or rollback for the whole publication transaction.
    """
    try:
        value = AwsReadObservation.model_validate(observation)
    except Exception as exc:
        raise ResourceServiceError("invalid_observation") from exc
    return record_aws_direct_observation(
        db,
        authorization=authorization,
        operation=operation,
        tool=value.tool,
        connector_id=value.connector_id,
        connector_revision=value.connector_revision,
        account_id=value.account_id,
        service=value.service,
        operation_name=value.operation,
        region=value.region,
        target=value.target,
        catalog_digest=value.catalog_digest,
        retrieved_at=value.retrieved_at,
        measurement_at=None,
        outcome=value.outcome,
        coverage={
            **value.coverage,
            "request_count": value.request_count,
            "next_cursor": value.next_cursor,
        },
        artifact=artifact,
    )


def record_aws_direct_observation(
    db: Session,
    *,
    authorization: AuthorizationContext,
    operation: ToolOperation,
    tool: CapabilityRef,
    connector_id: uuid.UUID,
    connector_revision: int,
    account_id: str,
    service: str,
    operation_name: str,
    region: str,
    target: dict,
    catalog_digest: str,
    retrieved_at: datetime,
    measurement_at: datetime | None,
    outcome: str,
    coverage: dict,
    artifact: ArtifactRef,
) -> ResourceObservation:
    """Stage one direct-target observation with no resource binding dependency."""
    if (
        authorization.action != "execute_tool"
        or authorization.principal_id != operation.principal_id
        or authorization.project_id != operation.project_id
        or authorization.conversation_id != operation.conversation_id
        or authorization.target_id != str(operation.run_id)
        or tool.id != operation.capability_id
        or tool.version != operation.capability_version
        or tool.manifest_digest != operation.manifest_digest
        or not isinstance(connector_id, uuid.UUID)
        or not 1 <= connector_revision
        or not isinstance(account_id, str)
        or len(account_id) != 12
        or not isinstance(service, str)
        or not 1 <= len(service) <= 64
        or not isinstance(operation_name, str)
        or not 1 <= len(operation_name) <= 128
        or not isinstance(region, str)
        or not 1 <= len(region) <= 64
        or not isinstance(catalog_digest, str)
        or len(catalog_digest) != 64
        or outcome not in {"measured", "no_data", "partial", "failed"}
        or len(artifact.sha256) != 64
    ):
        raise ResourceServiceError("observation_identity_mismatch")
    revision = (
        db.query(ConnectorRevision.id)
        .join(Connector, Connector.id == ConnectorRevision.connector_id)
        .filter(
            ConnectorRevision.project_id == operation.project_id,
            ConnectorRevision.connector_id == connector_id,
            ConnectorRevision.revision == connector_revision,
            ConnectorRevision.visibility == "project",
            Connector.kind == "aws",
            Connector.visibility == "project",
            Connector.project_id == operation.project_id,
        )
        .first()
    )
    if revision is None:
        raise ResourceServiceError("connector_revision_not_found")

    target_json = canonical_json(target).decode("utf-8")
    coverage_json = canonical_json(coverage).decode("utf-8")
    if (
        len(target_json.encode("utf-8")) > 16 * 1024
        or len(coverage_json.encode("utf-8")) > 16 * 1024
    ):
        raise ResourceServiceError("invalid_observation")
    row = ResourceObservation(
        project_id=operation.project_id,
        conversation_id=operation.conversation_id,
        run_id=operation.run_id,
        operation_id=operation.id,
        schema_version=2,
        connector_id=connector_id,
        connector_revision=connector_revision,
        account_id=account_id,
        service=service,
        operation=operation_name,
        region=region,
        target_json=target_json,
        catalog_digest=catalog_digest,
        source=operation.capability_id,
        retrieved_at=retrieved_at,
        measurement_at=measurement_at,
        coverage_json=coverage_json,
        outcome=outcome,
        evidence_artifact_id=artifact.id,
        evidence_sha256=artifact.sha256,
    )
    db.add(row)
    return row


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
    "MAX_ENVIRONMENTS_PER_PROJECT",
    "ResourceServiceError",
    "authorize_resource_project",
    "list_environments",
    "list_resource_bindings",
    "list_resource_observations",
    "record_aws_read_observation",
]
