"""Secret-safe public summaries for durable tool execution records."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactSummary
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.contracts import ToolFailure
from src.openship.tools.search.contracts import SearchArguments, SearchToolResult

from .models import ToolAttempt


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
    error: ToolFailure | None = None
    query: str | None = Field(default=None, max_length=512)
    result: dict[str, Any] | None = None
    artifacts: tuple[ArtifactSummary, ...] = ()
    attempts: tuple[ToolAttemptSummary, ...] = ()
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
    """Project only safe operation metadata; args, credentials and raw bodies stay private."""
    query = None
    if operation.capability_id in {"brave_web_search", "tavily_web_search"}:
        try:
            arguments = SearchArguments.model_validate_json(operation.arguments_json)
            query = arguments.query
        except (TypeError, ValueError):
            # Malformed or historical private arguments are not copied into
            # the public read model. The result projection may still have its
            # own independently validated query after a successful run.
            pass
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
        query=query,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        finished_at=operation.finished_at,
    )


def project_operation(db: Session, operation) -> OperationSummary:
    """Project a durable operation and its published, bounded result."""
    try:
        artifact_ids = tuple(UUID(value) for value in json.loads(operation.output_artifact_ids or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        artifact_ids = ()
    artifacts = ()
    if artifact_ids:
        artifacts = tuple(
            ArtifactSummary(
                id=item.id,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                media_type=item.media_type,
                conversation_id=item.conversation_id,
                project_id=item.project_id,
                name=item.display_name,
                source=item.source,
                status=item.status,
                created_at=item.created_at,
                mutability=item.mutability,
            )
            for item in db.query(Artifact)
            .filter(Artifact.id.in_(artifact_ids), Artifact.conversation_id == operation.conversation_id)
            .order_by(Artifact.created_at.asc(), Artifact.id.asc())
            .all()
        )

    error = None
    result = None
    status = operation.status
    error_code = operation.error_code
    if operation.status == "succeeded":
        try:
            result = SearchToolResult.model_validate_json(operation.result_json).model_dump(mode="json")
        except (TypeError, ValueError):
            from src.openship.execution.publication import _ERROR_MESSAGES

            status = "interrupted"
            error_code = "execution_interrupted"
            error = ToolFailure(
                code="execution_interrupted",
                message=_ERROR_MESSAGES["execution_interrupted"],
            )
    elif operation.status in ("failed", "cancelled", "interrupted"):
        from src.openship.execution.publication import _ERROR_MESSAGES

        code = operation.error_code
        if code not in _ERROR_MESSAGES:
            code = "execution_interrupted"
        error = ToolFailure(code=code, message=_ERROR_MESSAGES[code])

    attempts = tuple(
        tool_attempt_summary(attempt)
        for attempt in db.query(ToolAttempt)
        .filter(ToolAttempt.operation_id == operation.id)
        .order_by(ToolAttempt.ordinal.asc())
        .all()
    )
    return OperationSummary(
        **operation_summary(operation).model_dump(
            exclude={"error", "result", "artifacts", "attempts", "status", "error_code"}
        ),
        error=error,
        result=result,
        artifacts=artifacts,
        attempts=attempts,
        status=status,
        error_code=error_code,
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
