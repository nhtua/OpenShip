"""Bounded request and safe response contracts for owned cloud resources."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..artifacts.contracts import ArtifactRef

_BUCKET_LABEL = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ENVIRONMENT_NAME = r"^[a-z0-9](?:[a-z0-9_-]{0,62})$"


class ResourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EnvironmentCreate(ResourceContract):
    name: str = Field(min_length=1, max_length=64, pattern=_ENVIRONMENT_NAME)
    display_name: str = Field(min_length=1, max_length=255)

    @field_validator("display_name")
    @classmethod
    def _trim_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError("environment display name is invalid")
        return normalized


class EnvironmentSummary(ResourceContract):
    id: UUID
    project_id: UUID
    name: str
    display_name: str
    created_at: datetime


class ResourceBindingCreate(ResourceContract):
    environment_id: UUID = Field(strict=False)
    connector_id: UUID = Field(strict=False)
    connector_revision: int = Field(ge=1)
    bucket_name: str = Field(min_length=3, max_length=63)
    display_name: str = Field(min_length=1, max_length=255)

    @field_validator("bucket_name")
    @classmethod
    def _validate_bucket_name(cls, value: str) -> str:
        if (
            not _BUCKET_LABEL.fullmatch(value)
            or ".." in value
            or value.startswith(("xn--", "sthree-", "amzn-s3-demo-"))
            or value.endswith(("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3"))
        ):
            raise ValueError("bucket name is not a supported general-purpose S3 bucket name")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        raise ValueError("bucket name cannot be an IP address")

    @field_validator("display_name")
    @classmethod
    def _trim_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError("resource display name is invalid")
        return normalized


class ResourceBindingSummary(ResourceContract):
    resource_id: UUID
    uri: str
    display_name: str
    environment_id: UUID
    environment_name: str
    binding_id: UUID
    binding_revision: int = Field(ge=1)
    connector_id: UUID
    connector_revision: int = Field(ge=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1, max_length=64)
    bucket_name: str = Field(min_length=3, max_length=63)
    bucket_arn: str = Field(min_length=1, max_length=255)
    expected_owner: str = Field(pattern=r"^\d{12}$")
    management_method: Literal["external", "unknown"]
    state: Literal["active", "retired"]
    created_at: datetime


class ResourceObservationSummary(ResourceContract):
    id: UUID
    resource_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1)
    run_id: UUID
    operation_id: UUID
    source: str = Field(min_length=1, max_length=32)
    retrieved_at: datetime
    measurement_at: datetime | None
    outcome: Literal["measured", "no_data", "partial", "failed"]
    coverage: dict[str, object]
    evidence: ArtifactRef | None
    evidence_status: Literal[
        "staging", "ready", "deleting", "deleted", "unavailable"
    ] | None

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> ResourceObservationSummary:
        values = (self.retrieved_at, self.measurement_at)
        if any(value is not None and value.tzinfo is None for value in values):
            raise ValueError("resource observation timestamps must include a timezone")
        return self


class ResourcePage(ResourceContract):
    items: tuple[ResourceBindingSummary, ...]
    next_cursor: str | None


class ResourceObservationPage(ResourceContract):
    items: tuple[ResourceObservationSummary, ...]
    next_cursor: str | None


__all__ = [
    "EnvironmentCreate",
    "EnvironmentSummary",
    "ResourceBindingCreate",
    "ResourceBindingSummary",
    "ResourceObservationPage",
    "ResourceObservationSummary",
    "ResourcePage",
]
