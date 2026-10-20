"""Secret-safe public summaries for durable tool execution records."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OperationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    run_id: UUID
    conversation_id: UUID
    logical_call_id: str = Field(max_length=128)
    capability_id: str = Field(max_length=64)
    capability_version: str = Field(max_length=32)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(max_length=24)
    error_code: str | None = Field(default=None, max_length=64)
    output_artifact_ids: tuple[UUID, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None


class ToolAttemptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    operation_id: UUID
    ordinal: int = Field(ge=1)
    fence: int = Field(ge=1)
    dispatch_started_at: datetime | None = None
    finished_at: datetime | None = None
    outcome: str = Field(max_length=24)
    error_code: str | None = Field(default=None, max_length=64)


def operation_summary(operation) -> OperationSummary:
    try:
        artifact_ids = tuple(UUID(value) for value in json.loads(operation.output_artifact_ids or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        artifact_ids = ()
    return OperationSummary(
        id=operation.id,
        run_id=operation.run_id,
        conversation_id=operation.conversation_id,
        logical_call_id=operation.logical_call_id,
        capability_id=operation.capability_id,
        capability_version=operation.capability_version,
        manifest_digest=operation.manifest_digest,
        status=operation.status,
        error_code=operation.error_code,
        output_artifact_ids=artifact_ids,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        finished_at=operation.finished_at,
    )


def tool_attempt_summary(attempt) -> ToolAttemptSummary:
    return ToolAttemptSummary(
        id=attempt.id,
        operation_id=attempt.operation_id,
        ordinal=attempt.ordinal,
        fence=attempt.fence,
        dispatch_started_at=attempt.dispatch_started_at,
        finished_at=attempt.finished_at,
        outcome=attempt.outcome,
        error_code=attempt.error_code,
    )
