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
from src.openship.capabilities.contracts import CapabilityRef


class AwsToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AwsCheckArguments(AwsToolContract):
    connector_id: UUID
    resource_binding_id: UUID | None = None

    @field_validator("connector_id", "resource_binding_id", mode="before")
    @classmethod
    def _parse_ids(cls, value):
        if value is None or isinstance(value, UUID):
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
        }
    )

    def __init__(self, code: str):
        if code not in self._CODES:
            raise ValueError("unknown AWS tool error code")
        self.code = code
        super().__init__(code)
