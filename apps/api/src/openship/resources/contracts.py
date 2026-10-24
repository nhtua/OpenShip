"""Bounded request and safe response contracts for owned cloud resources."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..artifacts.contracts import ArtifactRef


class ResourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EnvironmentSummary(ResourceContract):
    id: UUID
    project_id: UUID
    name: str
    display_name: str
    created_at: datetime


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
    "EnvironmentSummary",
    "ResourceBindingSummary",
    "ResourceObservationPage",
    "ResourceObservationSummary",
    "ResourcePage",
]
