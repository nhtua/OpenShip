import json
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer

from ..providers.configuration import destination_label
from ..providers.errors import SAFE_MESSAGES
from .models import ProviderAttempt, Run


class TurnSubmitRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    client_request_id: str = Field(..., description="UUID generated once per browser send, for idempotency")
    # A browser names a profile and nothing else: no endpoint, no model, no SDK
    # argument. Omitting the profile means the installation's default.
    provider_profile_id: str | None = Field(default=None, max_length=128)
    # Omitted means "the installation's default"; an explicit empty list means
    # fallback disabled. The two are not the same request.
    fallback_profile_ids: list[str] | None = None


class ModelSummary(BaseModel):
    """What a run was pinned to — never a credential or an SDK setting."""
    profile_id: str
    label: str
    adapter: str
    requested_model: str
    actual_model: str | None = None
    destination_label: str | None = None
    fallback_profile_ids: tuple[str, ...] = ()
    recorded: bool = True


class UsageSummary(BaseModel):
    """Provider-reported counts. Missing stays missing; nothing is estimated."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    source: str | None = None


class ErrorSummary(BaseModel):
    """A normalized failure: a code and fixed copy, never a provider body."""
    code: str
    message: str
    retry_after_seconds: float | None = None


class AttemptSummary(BaseModel):
    """One bounded request a worker made for a run."""
    ordinal: int
    profile_id: str
    adapter: str
    requested_model: str
    actual_model: str | None = None
    destination_label: str | None = None
    outcome: str
    error_code: str | None = None
    usage: UsageSummary | None = None
    started_at: str | None = None
    ended_at: str | None = None


class RunResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    created_at: datetime
    attempt: int = 0
    model: ModelSummary | None = None
    usage: UsageSummary | None = None
    error: ErrorSummary | None = None
    provider_attempts: list[AttemptSummary] = Field(default_factory=list)

    @field_serializer('id')
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer('conversation_id')
    def serialize_conversation_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class RunCancelRequest(BaseModel):
    pass


def _model_projection(snapshot: str | None) -> ModelSummary | None:
    """A run's own pinned configuration, or nothing for a run that predates it."""
    if not snapshot:
        return None
    config = json.loads(snapshot)
    primary = config["primary"]
    return ModelSummary(
        profile_id=primary["id"],
        label=primary["label"],
        adapter=primary["adapter"],
        requested_model=primary["model"],
        destination_label=destination_label(primary["endpoint"]),
        fallback_profile_ids=tuple(profile["id"] for profile in config.get("fallbacks", ())),
    )


def _usage_projection(recorded: str | None) -> UsageSummary | None:
    if not recorded:
        return None
    return UsageSummary(**json.loads(recorded))


def _error_projection(code: str | None) -> ErrorSummary | None:
    if not code:
        return None
    return ErrorSummary(code=code, message=SAFE_MESSAGES.get(code, "The model provider reported a failure."))


def _attempt_projection(attempt: ProviderAttempt) -> AttemptSummary:
    return AttemptSummary(
        ordinal=attempt.ordinal,
        profile_id=attempt.profile_id,
        adapter=attempt.adapter,
        requested_model=attempt.requested_model,
        actual_model=attempt.actual_model,
        destination_label=attempt.destination_label,
        outcome=attempt.outcome,
        error_code=attempt.error_code,
        usage=_usage_projection(attempt.usage),
        started_at=attempt.started_at.isoformat() if attempt.started_at else None,
        ended_at=attempt.ended_at.isoformat() if attempt.ended_at else None,
    )


def project_run_response(run: Run, attempts=()) -> RunResponse:
    """The public shape of a run: only what a browser is allowed to see."""
    return RunResponse(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        created_at=run.created_at,
        attempt=run.attempt,
        model=_model_projection(run.model_configuration),
        usage=_usage_projection(run.usage),
        error=_error_projection(run.error_code),
        provider_attempts=[_attempt_projection(attempt) for attempt in attempts],
    )
