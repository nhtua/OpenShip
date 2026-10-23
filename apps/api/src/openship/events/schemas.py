"""Pydantic schemas for event API."""

from typing import Any

from pydantic import BaseModel, Field

from ..artifacts.contracts import ArtifactSummary
from ..execution.projections import OperationSummary
from ..runs.schemas import (
    AttemptSummary,
    ConnectorBlockerSummary,
    ErrorSummary,
    ModelSummary,
    UsageSummary,
)


class EventInput(BaseModel):
    """Input for appending an event."""
    type: str = Field(..., min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None


class EventRead(BaseModel):
    """Event as returned to client."""
    id: str
    conversation_id: str
    run_id: str | None
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: str


class MessageSummary(BaseModel):
    """Summary of a chat message."""
    id: str
    role: str
    content: str
    run_id: str | None
    created_at: str


class RunSummary(BaseModel):
    """Summary of a run. Only the public projection: no credential, no SDK setting."""
    id: str
    status: str
    attempt: int
    started_at: str | None
    ended_at: str | None
    error_code: str | None
    kind: str = "chat"
    graph_version: int = 1
    model: ModelSummary | None = None
    usage: UsageSummary | None = None
    error: ErrorSummary | None = None
    provider_attempts: list[AttemptSummary] = Field(default_factory=list)
    operations: list[OperationSummary] = Field(default_factory=list)
    operations_next_cursor: str | None = None
    connector_blocker: ConnectorBlockerSummary | None = None


class SnapshotResponse(BaseModel):
    """Complete state snapshot for a conversation."""
    conversation_id: str
    project_id: str
    archived_at: str | None
    project_archived_at: str | None
    sequence: int  # Max committed sequence; -1 means empty
    events: list[EventRead]
    messages: list[MessageSummary]
    runs: list[RunSummary]
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    artifacts_next_cursor: str | None = None


class StreamResponse(BaseModel):
    """Stream of events after a sequence cursor."""
    conversation_id: str
    after_sequence: int
    events: list[EventRead]
