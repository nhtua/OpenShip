"""Running one bounded model turn through any adapter, and recording it.

The runner is the only place that owns a turn's admission checks, its shared
deadline, its stop signal, and the attempt record. Adapters stay provider-
neutral, so the graph, the worker, and the browser never see an SDK object.

This first shape makes exactly one generation request per turn. Task 5 extends
it with retries, fallback, and the token/cost budget.
"""

from __future__ import annotations

import asyncio

from src.openship.providers.contracts import (
    ModelRequest,
    ProviderTurnResult,
    RunModelConfig,
    TokenUsage,
    validate_limits,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import build_adapter

#: How often the supervisor checks the stop signal and the deadline. The plan's
#: cancellation budget is 2 seconds in controlled transport tests, so polling
#: well inside it interrupts a blocked read without spinning.
_SUPERVISION_SECONDS = 0.5

#: A turn interrupted before it produced an answer, by cause.
INTERRUPTED = ("cancelled", "budget_exceeded")


class _Observed:
    """What a stream produced so far, so an interrupted attempt can still be recorded."""

    def __init__(self):
        self.parts: list[str] = []
        self.usage: TokenUsage | None = None
        self.actual_model: str | None = None
        self.reason: str | None = None


def _context_bytes(request: ModelRequest) -> int:
    """The UTF-8 size of the conversation as it would actually be sent."""
    return sum(
        len(message.role.encode("utf-8")) + len(message.content.encode("utf-8"))
        for message in request.messages
    )


def _admit(request: ModelRequest, config: RunModelConfig) -> None:
    """Refuse what cannot run, before any request is made or attempt recorded."""
    profile = config.primary
    validate_request(request, profile)
    validate_limits(config.limits, profile)

    requested = _context_bytes(request)
    if requested > config.limits.max_context_bytes:
        raise ProviderError(
            "context_limit_exceeded",
            safe_message=(
                f"the conversation is {requested} UTF-8 bytes, above the "
                f"{config.limits.max_context_bytes} this run admitted"
            ),
        )


async def _drain(stream, observed: _Observed) -> None:
    """Consume one adapter stream into what a turn produced."""
    async for event in stream:
        if event.type == "text_delta":
            observed.parts.append(event.text)
        elif event.type == "usage_update":
            # Cumulative snapshots: the last one is the fullest, never a sum.
            observed.usage = event.usage
        else:
            observed.reason = event.reason
            observed.actual_model = event.actual_model


async def _supervise(turn, control) -> str | None:
    """Watch the stop signal and the deadline, interrupting a blocked read."""
    while not turn.done():
        await asyncio.sleep(_SUPERVISION_SECONDS)
        if turn.done():
            return None
        if control.is_stopped():
            turn.cancel()
            return "cancelled"
        if control.remaining_seconds() <= 0:
            turn.cancel()
            return "budget_exceeded"
    return None


async def run_model_turn(
    request: ModelRequest,
    config: RunModelConfig,
    control,
    recorder,
) -> ProviderTurnResult:
    """Run one bounded model turn through the configuration this run pinned.

    Raises ``ProviderError`` when the turn produced no answer; otherwise
    returns the canonical result, whose ``finish_reason`` says whether the
    answer is publishable.
    """
    profile = config.primary
    _admit(request, config)

    attempt_id = recorder.start(profile, reason="initial")
    adapter = build_adapter(profile)

    observed = _Observed()
    turn = asyncio.ensure_future(_drain(adapter.stream(request, profile, control), observed))
    interrupted = await _supervise(turn, control)

    if interrupted is not None or turn.cancelled():
        # Let the turn unwind first: closing the stream it opened is what
        # makes a cancelled turn leave no transport behind.
        await asyncio.wait((turn,))
        code = interrupted if interrupted in INTERRUPTED else "cancelled"
        recorder.finish(
            attempt_id,
            "cancelled" if code == "cancelled" else "failed",
            usage=observed.usage,
            error_code=code,
        )
        raise ProviderError(code) from None

    failure = turn.exception()
    if isinstance(failure, ProviderError):
        recorder.finish(
            attempt_id,
            "cancelled" if failure.code == "lease_lost" else "failed",
            usage=observed.usage,
            error_code=failure.code,
        )
        raise failure
    if failure is not None:
        recorder.finish(attempt_id, "failed", usage=observed.usage, error_code="invalid_response")
        raise ProviderError("invalid_response", cause=failure) from failure

    recorder.finish(
        attempt_id,
        "succeeded",
        usage=observed.usage,
        actual_model=observed.actual_model,
    )

    text = "".join(observed.parts).strip()
    return ProviderTurnResult(
        text=text,
        usage=observed.usage,
        actual_model=observed.actual_model,
        selected_profile_id=profile.id,
        attempt_count=1,
        finish_reason=observed.reason or "stop",
        partial=observed.reason is not None and observed.reason != "stop",
    )
