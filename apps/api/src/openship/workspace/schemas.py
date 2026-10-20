"""Strict bounded contracts for persistent conversation workspaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class WorkspaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkspaceQuotaSummary(WorkspaceContract):
    committed_bytes: int = Field(ge=0)
    committed_count: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    reserved_count: int = Field(ge=0)
    max_bytes: int = Field(ge=1)
    max_count: int = Field(ge=1)


class WorkspaceSummary(WorkspaceContract):
    id: UUID
    conversation_id: UUID
    project_id: UUID
    schema_version: Literal[1] = 1
    relative_root: str = Field(min_length=1, max_length=255)
    storage_id: UUID
    status: Literal["available", "unavailable", "deleted"]
    created_at: datetime
    quota: WorkspaceQuotaSummary


class WorkspaceListArguments(WorkspaceContract):
    limit: StrictInt = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=256)


class WorkspaceReadArguments(WorkspaceContract):
    artifact_id: UUID
    offset: StrictInt = Field(default=0, ge=0)
    max_bytes: StrictInt = Field(default=8 * 1024, ge=4, le=16 * 1024)

    @field_validator("artifact_id", mode="before")
    @classmethod
    def _parse_artifact_id(cls, value):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("artifact_id must be a UUID") from exc
        return value


class WorkspaceFileSummary(WorkspaceContract):
    id: UUID
    version_id: UUID
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str = Field(min_length=1, max_length=32)
    created_at: datetime | None = None
    originating_run_id: UUID | None = None
    originating_operation_id: UUID | None = None


class WorkspaceListResult(WorkspaceContract):
    workspace_id: UUID
    items: tuple[WorkspaceFileSummary, ...]
    next_cursor: str | None = None


class WorkspaceReadResult(WorkspaceContract):
    workspace_id: UUID
    artifact_id: UUID
    version_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    offset: int = Field(ge=0)
    content: str
    next_offset: int | None = Field(default=None, ge=0)
