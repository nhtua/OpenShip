"""Pydantic schemas for event API."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts.contracts import ArtifactSummary
from ..capabilities.contracts import SHA256_PATTERN
from ..execution.projections import OperationSummary
from ..runs.schemas import (
    AnalysisResumeSummary,
    AttemptSummary,
    ConnectorBlockerSummary,
    ErrorSummary,
    ModelSummary,
    UsageSummary,
)
from ..runs.tasks.projections import (
    TaskArtifactLink,
    TaskPlanStepSummary,
    TaskRecoverySummary,
    TaskStepSummary,
    TaskSummary,
)


class TaskDecisionRequestedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: UUID
    revision: int = Field(ge=0)
    request_id: UUID
    kind: Literal["plan_confirmation", "clarification", "configuration", "recovery"]
    expected_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt: str = Field(min_length=1, max_length=10000)
    expires_at: str
    # A recovery names the diagnosis behind its three choices, so a
    # reconnect offers the reader the same grounds they chose from. The
    # diagnosis's own digest is what a recovery response must echo.
    diagnosis: TaskRecoverySummary | None = None


class TaskDecisionResolvedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: UUID
    revision: int = Field(ge=0)
    request_id: UUID
    state: Literal["confirmed", "rejected", "answered", "expired", "cancelled"]


class TaskPlanProposedEvent(BaseModel):
    """A reviewed plan revision was published for this run.

    The projection is the plan the browser is owed: steps, their order and
    the review digest. A new revision never replaces an older one in the
    stream - the stream keeps every revision the person may still look at.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: UUID
    revision: int = Field(ge=1)
    mode: Literal["auto", "copilot"]
    review_digest: str = Field(pattern=SHA256_PATTERN)
    steps: list[TaskPlanStepSummary] = Field(min_length=1, max_length=8)


class TaskProgressEvent(BaseModel):
    """What each step has durably done, as of this point in the stream."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: UUID
    mode: Literal["auto", "copilot"]
    revision: int = Field(ge=0)
    status: Literal[
        "unknown",
        "planned",
        "running",
        "waiting",
        "verification_pending",
        "completed",
        "blocked",
        "failed",
        "cancelled",
    ]
    steps: list[TaskStepSummary] = Field(default_factory=list, max_length=8)
    skipped_steps: list[str] = Field(default_factory=list, max_length=8)
    verified_outputs: list[TaskArtifactLink] = Field(default_factory=list, max_length=64)
    last_disposition: Literal[
        "none",
        "known_failure",
        "material_revision",
        "uncertain",
        "denied",
        "policy_revoked",
        "budget_exhausted",
    ]


class TaskDispositionEvent(BaseModel):
    """The outcome class the last step verdict put on record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: UUID
    revision: int = Field(ge=0)
    step_id: str | None = None
    disposition: Literal[
        "none",
        "known_failure",
        "material_revision",
        "uncertain",
        "denied",
        "policy_revoked",
        "budget_exhausted",
    ]


class MessageAdmittedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    message_id: UUID
    disposition: Literal["clarification", "queued"]
    status: Literal["attached", "queued", "promoted", "failed"]
    run_id: UUID | None = None
    request_id: UUID | None = None


class MessageStatusChangedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    message_id: UUID
    status: Literal["promoted", "failed"]
    run_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=100)


TASK_EVENT_SCHEMAS = {
    "message.admitted": MessageAdmittedEvent,
    "message.status_changed": MessageStatusChangedEvent,
    "task.decision_requested": TaskDecisionRequestedEvent,
    "task.decision_resolved": TaskDecisionResolvedEvent,
    "task.plan_proposed": TaskPlanProposedEvent,
    "task.progress": TaskProgressEvent,
    "task.disposition": TaskDispositionEvent,
}


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
    analysis: AnalysisResumeSummary | None = None


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
    # A reload shows the same plan and progress the events already showed.
    tasks: list[TaskSummary] = Field(default_factory=list)
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    artifacts_next_cursor: str | None = None


class StreamResponse(BaseModel):
    """Stream of events after a sequence cursor."""

    conversation_id: str
    after_sequence: int
    events: list[EventRead]
