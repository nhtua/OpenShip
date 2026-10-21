import json
import uuid
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
)

from ..execution.projections import OperationSummary
from ..providers.configuration import destination_label
from ..providers.errors import SAFE_MESSAGES
from .models import ProviderAttempt, Run


class TurnSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=10000)
    client_request_id: str = Field(..., description="UUID generated once per browser send, for idempotency")
    # A browser names a profile and nothing else: no endpoint, no model, no SDK
    # argument. Omitting the profile means the installation's default.
    provider_profile_id: str | None = Field(default=None, max_length=128)
    # Omitted means "the installation's default"; an explicit empty list means
    # fallback disabled. The two are not the same request.
    fallback_profile_ids: list[str] | None = None
    enable_tools: StrictBool = False
    artifact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @field_validator("artifact_ids")
    @classmethod
    def _unique_artifact_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("artifact_ids must be distinct")
        return value


class ModelSummary(BaseModel):
    """What a run was pinned to — never a credential or an SDK setting."""
    profile_id: str
    label: str
    adapter: str
    requested_model: str
    serving_context_tokens: int | None = None
    actual_model: str | None = None
    destination_label: str | None = None
    serving_profile_id: str | None = None
    serving_label: str | None = None
    serving_adapter: str | None = None
    serving_destination_label: str | None = None
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
    kind: str = "chat"
    graph_version: int = 1
    model: ModelSummary | None = None
    usage: UsageSummary | None = None
    error: ErrorSummary | None = None
    provider_attempts: list[AttemptSummary] = Field(default_factory=list)
    operations: list[OperationSummary] = Field(default_factory=list)
    operations_next_cursor: str | None = None

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


def _served_model(attempts) -> str | None:
    """What the last successful attempt says it actually answered with.

    Missing stays missing: an attempt that did not record one is not evidence
    of a model, and nothing here falls back to the requested name - that is
    the conflation the plan says must not be made.
    """
    for attempt in reversed(tuple(attempts)):
        if attempt.outcome == "succeeded" and attempt.actual_model:
            return attempt.actual_model
    return None


def _serving_attempt(attempts):
    """The successful request that actually served the run, if recorded."""
    return next(
        (attempt for attempt in reversed(tuple(attempts)) if attempt.outcome == "succeeded"),
        None,
    )


def _model_projection(snapshot: str | None, attempts=()) -> ModelSummary | None:
    """A run's own pinned configuration, or nothing for a run that predates it."""
    if not snapshot:
        return None
    try:
        config = json.loads(snapshot)
        primary = config["primary"]
        profile_id = primary["id"]
        label = primary["label"]
        adapter = primary["adapter"]
        model = primary["model"]
        endpoint = primary["endpoint"]
        fallbacks = config.get("fallbacks", ())
        profiles = (primary, *fallbacks)
        fallback_ids = tuple(profile["id"] for profile in fallbacks)
        labels = {profile["id"]: profile["label"] for profile in (primary, *config.get("fallbacks", ()))}
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        # A snapshot can outlive the schema that wrote it. Its normalized
        # failure remains readable even when its provider metadata does not.
        return None
    serving = _serving_attempt(attempts)
    serving_context_tokens = None
    if serving is not None:
        serving_profile = next(
            (profile for profile in profiles if profile.get("id") == serving.profile_id),
            None,
        )
        capabilities = serving_profile.get("capabilities") if serving_profile else None
        context_tokens = capabilities.get("context_tokens") if isinstance(capabilities, dict) else None
        if isinstance(context_tokens, int) and not isinstance(context_tokens, bool) and context_tokens > 0:
            serving_context_tokens = context_tokens
    return ModelSummary(
        profile_id=profile_id,
        label=label,
        adapter=adapter,
        requested_model=model,
        serving_context_tokens=serving_context_tokens,
        # Requested and actual are separate fields on the contract, and they
        # stay separate here: a provider that answered from an alias it was
        # handed would otherwise be indistinguishable from one that answered
        # from the model the operator named.
        actual_model=serving.actual_model if serving and serving.actual_model else _served_model(attempts),
        destination_label=destination_label(endpoint),
        serving_profile_id=serving.profile_id if serving else None,
        serving_label=labels.get(serving.profile_id, serving.profile_id) if serving else None,
        serving_adapter=serving.adapter if serving else None,
        serving_destination_label=serving.destination_label if serving else None,
        fallback_profile_ids=fallback_ids,
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


def project_run_response(run: Run, attempts=(), operation_page=None) -> RunResponse:
    """The public shape of a run: only what a browser is allowed to see."""
    return RunResponse(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        created_at=run.created_at,
        attempt=run.attempt,
        kind=run.kind or "chat",
        graph_version=run.graph_version or 1,
        model=_model_projection(run.model_configuration, attempts),
        usage=_usage_projection(run.usage),
        error=_error_projection(run.error_code),
        provider_attempts=[_attempt_projection(attempt) for attempt in attempts],
        operations=list(operation_page.items) if operation_page else [],
        operations_next_cursor=operation_page.next_cursor if operation_page else None,
    )
