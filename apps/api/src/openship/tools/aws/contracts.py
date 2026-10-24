"""Secret-free contracts for the first managed AWS capabilities."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
)

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import CapabilityRef, canonical_json
from src.openship.resources.contracts import ResourceBindingSummary

_SHA256 = r"^[0-9a-f]{64}$"


class AwsToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AwsBoundInventoryResultV1(AwsToolContract):
    """Read-only projection for completed binding inventory runs from history."""

    source: Literal["stored_bindings"] = "stored_bindings"
    items: tuple[ResourceBindingSummary, ...]
    next_cursor: str | None = None


class AwsReadArguments(AwsToolContract):
    """One explicitly selected, catalog-approved AWS operation and target."""

    connector_id: UUID | None = None
    service: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    operation: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    region: str | None = Field(default=None, min_length=1, max_length=64)
    parameters: dict[str, object] = Field(default_factory=dict)
    cursor: str | None = Field(default=None, max_length=4096)
    page_size: StrictInt = Field(default=20, ge=1, le=50)

    @field_validator("connector_id", mode="before")
    @classmethod
    def _parse_connector_id(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("connector_id must be a UUID") from exc
        return value

    @field_validator("parameters")
    @classmethod
    def _bounded_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS operation parameters exceed the input limit")
        return value


class AwsReadObservation(AwsToolContract):
    """Secret-filtered provider evidence before it is published as an artifact."""

    schema_version: Literal[2] = 2
    tool: CapabilityRef
    operation_id: UUID
    project_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    catalog_digest: str = Field(pattern=_SHA256)
    account_id: str = Field(pattern=r"^\d{12}$")
    service: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    operation: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    region: str = Field(min_length=1, max_length=64)
    target: dict[str, object]
    retrieved_at: datetime
    request_count: StrictInt = Field(ge=1, le=8)
    data: dict[str, object]
    outcome: Literal["measured", "no_data", "partial"]
    coverage: dict[str, object] = Field(default_factory=dict)
    next_cursor: str | None = Field(default=None, max_length=4096)

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("AWS observation timestamp must include a timezone")
        return value

    @field_validator("target")
    @classmethod
    def _bounded_target(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS observation target exceeds the storage limit")
        return value

    @field_validator("coverage")
    @classmethod
    def _bounded_coverage(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS observation coverage exceeds the storage limit")
        return value

    @field_validator("data")
    @classmethod
    def _bounded_data(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 64 * 1024:
            raise ValueError("AWS normalized evidence exceeds the storage limit")
        return value


class AwsReadResult(AwsToolContract):
    """Compact public result; the complete bounded page lives in its artifact."""

    tool: CapabilityRef
    operation_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    catalog_digest: str = Field(pattern=_SHA256)
    account_id: str = Field(pattern=r"^\d{12}$")
    service: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    operation: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    region: str = Field(min_length=1, max_length=64)
    retrieved_at: datetime
    outcome: Literal["measured", "no_data", "partial"]
    preview: dict[str, object]
    preview_truncated: StrictBool
    next_cursor: str | None = Field(default=None, max_length=4096)
    artifact: ArtifactRef

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("AWS result timestamp must include a timezone")
        return value

    @field_validator("preview")
    @classmethod
    def _bounded_preview(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS model preview exceeds the result limit")
        return value


class AwsCheckArguments(AwsToolContract):
    connector_id: UUID

    @field_validator("connector_id", mode="before")
    @classmethod
    def _parse_ids(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("connector references must be UUIDs") from exc
        return value


class AwsCheckResult(AwsToolContract):
    operation_id: str = Field(min_length=1, max_length=64)
    status: Literal["ready"]
    account_id: str = Field(pattern=r"^\d{12}$")
    role_arn: str = Field(min_length=1, max_length=2048)
    region: str = Field(min_length=1, max_length=64)
    checked_at: datetime


class AwsCheckResultV1(AwsToolContract):
    """Historical output contract for pre-discovery role and bucket checks."""

    operation_id: str = Field(min_length=1, max_length=64)
    status: Literal["ready"]
    account_id: str = Field(pattern=r"^\d{12}$")
    role_arn: str = Field(min_length=1, max_length=2048)
    region: str = Field(min_length=1, max_length=64)
    bucket_verified: StrictBool = False
    checked_at: datetime


class AwsStorageArguments(AwsToolContract):
    resource_binding_id: UUID

    @field_validator("resource_binding_id", mode="before")
    @classmethod
    def _parse_binding_id(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("resource_binding_id must be a UUID") from exc
        return value


class StorageTypeMeasurement(AwsToolContract):
    storage_type: str = Field(min_length=1, max_length=64)
    measured_at: datetime
    value_bytes: StrictFloat = Field(ge=0)
    stale: StrictBool = False


class StorageCoverage(AwsToolContract):
    queried_types: tuple[str, ...] = Field(max_length=22)
    reported_types: tuple[str, ...] = Field(max_length=22)
    missing_types: tuple[str, ...] = Field(max_length=22)
    partial_types: tuple[str, ...] = Field(max_length=22)
    query_count: StrictInt = Field(ge=0, le=64)
    truncated: StrictBool = False


class NormalizedStorageMetrics(AwsToolContract):
    outcome: Literal["measured", "no_data", "partial"]
    stale: StrictBool = False
    measurement_at: datetime | None = None
    reported_storage_subtotal_bytes: StrictFloat | None = Field(default=None, ge=0)
    storage_types: tuple[StorageTypeMeasurement, ...] = Field(max_length=1024)
    coverage: StorageCoverage


class AwsStorageObservation(AwsToolContract):
    schema_version: Literal[1] = 1
    tool: CapabilityRef
    operation_id: str = Field(min_length=1, max_length=64)
    project_id: UUID
    resource_id: UUID
    resource_binding_id: UUID
    binding_revision: StrictInt = Field(ge=1)
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    retrieved_at: datetime
    metrics: NormalizedStorageMetrics


class AwsStorageResult(AwsToolContract):
    operation_id: str = Field(min_length=1, max_length=64)
    resource_id: UUID
    resource_binding_id: UUID
    binding_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    retrieved_at: datetime
    outcome: Literal["measured", "no_data", "partial"]
    stale: StrictBool
    measurement_at: datetime | None = None
    reported_storage_subtotal_bytes: StrictFloat | None = Field(default=None, ge=0)
    coverage: StorageCoverage
    artifact: ArtifactRef


class AwsS3MetadataArguments(AwsToolContract):
    resource_binding_id: UUID

    @field_validator("resource_binding_id", mode="before")
    @classmethod
    def _parse_binding_id(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("resource_binding_id must be a UUID") from exc
        return value


class AwsS3MetadataObservation(AwsToolContract):
    schema_version: Literal[1] = 1
    tool: CapabilityRef
    operation_id: str = Field(min_length=1, max_length=64)
    project_id: UUID
    resource_id: UUID
    resource_binding_id: UUID
    binding_revision: StrictInt = Field(ge=1)
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    versioning_status: Literal["enabled", "suspended", "not_enabled"]
    mfa_delete_status: Literal["enabled", "disabled", "not_reported"]
    request_count: StrictInt = Field(ge=1, le=8)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metadata retrieval timestamp must include a timezone")
        return value


class AwsS3MetadataResult(AwsToolContract):
    operation_id: str = Field(min_length=1, max_length=64)
    resource_id: UUID
    resource_binding_id: UUID
    binding_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    versioning_status: Literal["enabled", "suspended", "not_enabled"]
    mfa_delete_status: Literal["enabled", "disabled", "not_reported"]
    request_count: StrictInt = Field(ge=1, le=8)
    retrieved_at: datetime
    artifact: ArtifactRef

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metadata retrieval timestamp must include a timezone")
        return value


class AwsS3BucketTargetArguments(AwsToolContract):
    """Direct bucket target for maintained S3 helpers; no resource binding ID."""

    connector_id: UUID
    bucket_name: str = Field(min_length=3, max_length=63)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    expected_owner: str | None = Field(default=None, pattern=r"^\d{12}$")

    @field_validator("connector_id", mode="before")
    @classmethod
    def _parse_connector_id(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("connector_id must be a UUID") from exc
        return value


class AwsStorageArgumentsV2(AwsS3BucketTargetArguments):
    """Direct-target arguments for the specialized storage helper."""


class AwsS3MetadataArgumentsV2(AwsS3BucketTargetArguments):
    """Direct-target arguments for the specialized metadata helper."""


class AwsStorageObservationV2(AwsToolContract):
    """Direct-target evidence for the earlier specialized storage helper."""

    schema_version: Literal[2] = 2
    tool: CapabilityRef
    operation_id: UUID
    project_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    catalog_digest: str = Field(pattern=_SHA256)
    account_id: str = Field(pattern=r"^\d{12}$")
    service: Literal["cloudwatch"] = "cloudwatch"
    operation: Literal["get_metric_data"] = "get_metric_data"
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    target: dict[str, object]
    retrieved_at: datetime
    request_count: StrictInt = Field(ge=1, le=8)
    metrics: NormalizedStorageMetrics

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("storage retrieval timestamp must include a timezone")
        return value

    @field_validator("target")
    @classmethod
    def _bounded_target(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS observation target exceeds the storage limit")
        return value


class AwsStorageResultV2(AwsToolContract):
    operation_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    retrieved_at: datetime
    outcome: Literal["measured", "no_data", "partial"]
    stale: StrictBool
    measurement_at: datetime | None = None
    reported_storage_subtotal_bytes: StrictFloat | None = Field(default=None, ge=0)
    coverage: StorageCoverage
    artifact: ArtifactRef


class AwsS3MetadataObservationV2(AwsToolContract):
    schema_version: Literal[2] = 2
    tool: CapabilityRef
    operation_id: UUID
    project_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    catalog_digest: str = Field(pattern=_SHA256)
    account_id: str = Field(pattern=r"^\d{12}$")
    service: Literal["s3"] = "s3"
    operation: Literal["get_bucket_versioning"] = "get_bucket_versioning"
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    target: dict[str, object]
    versioning_status: Literal["enabled", "suspended", "not_enabled"]
    mfa_delete_status: Literal["enabled", "disabled", "not_reported"]
    request_count: StrictInt = Field(ge=1, le=8)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metadata retrieval timestamp must include a timezone")
        return value

    @field_validator("target")
    @classmethod
    def _bounded_target(cls, value: dict[str, object]) -> dict[str, object]:
        if len(canonical_json(value)) > 16 * 1024:
            raise ValueError("AWS observation target exceeds the storage limit")
        return value


class AwsS3MetadataResultV2(AwsToolContract):
    operation_id: UUID
    connector_id: UUID
    connector_revision: StrictInt = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    versioning_status: Literal["enabled", "suspended", "not_enabled"]
    mfa_delete_status: Literal["enabled", "disabled", "not_reported"]
    request_count: StrictInt = Field(ge=1, le=8)
    retrieved_at: datetime
    artifact: ArtifactRef


class AwsToolError(RuntimeError):
    """Fixed safe failure from a maintained AWS tool."""

    _CODES = frozenset(
        {
            "aws_account_mismatch",
            "aws_identity_mismatch",
            "aws_region_mismatch",
            "aws_resource_unavailable",
            "aws_invalid_metric_response",
            "aws_invalid_metadata_response",
            "aws_request_limit_exceeded",
            "aws_output_limit_exceeded",
            "aws_unavailable",
            "aws_access_denied",
            "aws_not_found",
            "aws_throttled",
            "aws_invalid_parameters",
            "aws_operation_not_supported",
            "aws_unsupported_protocol",
            "aws_unsupported_output",
            "aws_invalid_response",
            "aws_invalid_cursor",
            "aws_catalog_mismatch",
            "aws_configuration_error",
            "aws_trust_denied",
            "aws_credentials_expired",
            "aws_credentials_invalid",
            "aws_timeout",
            "aws_cancelled",
            "aws_destination_denied",
            "aws_redirect",
        }
    )

    def __init__(self, code: str):
        if code not in self._CODES:
            raise ValueError("unknown AWS tool error code")
        self.code = code
        super().__init__(code)
