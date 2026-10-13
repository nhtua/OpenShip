"""Running one bounded model turn through any adapter, and recording it.

The runner is the only place that owns a turn's admission checks, its shared
deadline, its stop signal, its request budget, and the attempt record. Adapters
stay provider-neutral, so the graph, the worker, and the browser never see an
SDK object.

The policy here is the one the roadmap states: at most ``max_attempts``
generation requests across every destination, one deadline shared by all of
them, retries only for a transient failure that produced no answer, fallback
only to a destination both the run's list and the profile's allowlist name,
and a conservative reservation against a token or cost ceiling before any
request is made. None of this is an exact provider invoice.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal

from src.openship.providers.contracts import (
    ModelRequest,
    ProviderTurnResult,
    RunLimits,
    RunModelConfig,
    TokenUsage,
    validate_limits,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import build_adapter

#: How often the supervisor checks the stop signal and the deadline. The
#: cancellation budget is 2 seconds in controlled transport tests, so polling
#: well inside it interrupts a blocked read without spinning.
_SUPERVISION_SECONDS = 0.5

#: A turn interrupted before it produced an answer, by cause.
INTERRUPTED = ("cancelled", "budget_exceeded")

#: The upper bound of full jitter's schedule, in seconds.
_BACKOFF_CEILING_SECONDS = 8.0

#: Why an attempt happened, so a run's history reads as a decision.
_INITIAL = "initial"


class _Observed:
    """What a stream produced so far, so an interrupted attempt can still be recorded."""

    def __init__(self):
        self.parts: list[str] = []
        self.usage: TokenUsage | None = None
        self.actual_model: str | None = None
        self.reason: str | None = None

    def produced(self) -> bool:
        """Whether an answer had already begun."""
        return bool(self.parts)


def _context_bytes(request: ModelRequest) -> int:
    """The UTF-8 size of the conversation as it would actually be sent."""
    return sum(
        len(message.role.encode("utf-8")) + len(message.content.encode("utf-8"))
        for message in request.messages
    )


def _admit(request: ModelRequest, limits: RunLimits, profile) -> None:
    """Refuse what cannot run, before any request is made or attempt recorded."""
    validate_request(request, profile)
    validate_limits(limits, profile)

    requested = _context_bytes(request)
    if requested > limits.max_context_bytes:
        raise ProviderError(
            "context_limit_exceeded",
            safe_message=(
                f"the conversation is {requested} UTF-8 bytes, above the "
                f"{limits.max_context_bytes} this run admitted"
            ),
        )


def _reserved_cost(profile, tokens: int) -> Decimal | None:
    """A conservative cost bound for a qualified schedule, or nothing."""
    if profile.pricing is None:
        return None
    rate = max(profile.pricing.input_per_million, profile.pricing.output_per_million)
    return Decimal(tokens) * rate / Decimal(1_000_000)


class _Spent:
    """What the turn has already reserved against the run's ceilings."""

    def __init__(self):
        self.tokens = 0
        self.cost = Decimal(0)

    def consume(self, bound: int, cost: Decimal | None) -> None:
        """Charge the turn for an attempt that did not answer.

        Unknown usage costs the reserved bound, never zero: the alternative
        would let a retry loop spend a ceiling the operator set.
        """
        self.tokens += bound
        if cost is not None:
            self.cost += cost


async def _reserve(
    adapter,
    request: ModelRequest,
    profile,
    limits: RunLimits,
    attempts_left: int,
    spent: _Spent,
    control,
) -> tuple[int, Decimal | None] | None:
    """Reserve input plus maximum output for each attempt that could still run.

    Returns one attempt's bound, which is what a failed attempt then consumes.
    Counting obeys the same deadline and stop signal as generation does.
    """
    if limits.max_total_tokens is None and limits.max_cost_usd is None:
        return None

    counted = await adapter.count_input_tokens(request, profile, control)
    bound = counted + request.max_output_tokens

    if limits.max_total_tokens is not None and spent.tokens + bound * attempts_left > (
        limits.max_total_tokens
    ):
        raise ProviderError(
            "budget_exceeded",
            safe_message=(
                f"{attempts_left} attempts of at most {bound} tokens do not fit the "
                f"{limits.max_total_tokens} this run admitted"
            ),
        )

    cost = _reserved_cost(profile, bound)
    if limits.max_cost_usd is not None:
        if cost is None:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"profile {profile.id!r} has no qualified pricing schedule, so a "
                    "cost ceiling cannot be enforced"
                ),
            )
        if spent.cost + cost * attempts_left > limits.max_cost_usd:
            raise ProviderError(
                "budget_exceeded",
                safe_message=(
                    f"{attempts_left} reserved attempts cost more than the "
                    f"{limits.max_cost_usd} this run admitted"
                ),
            )
    return bound, cost


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


async def _attempt(adapter, request, profile, control, observed: _Observed) -> tuple[str, object]:
    """One generation request through the control. Never raises: the caller decides.

    A cancelled task can complete normally, because closing the stream it
    opened swallows the cancellation — so the supervisor's decision is what
    ended the turn, and the task is awaited to completion before it is read.
    """
    turn = asyncio.ensure_future(_drain(adapter.stream(request, profile, control), observed))
    interrupted = await _supervise(turn, control)

    if interrupted is not None or turn.cancelled():
        await asyncio.wait((turn,))
        return "interrupted", interrupted if interrupted in INTERRUPTED else "cancelled"

    failure = turn.exception()
    if failure is None:
        return "finished", None
    if isinstance(failure, ProviderError):
        return "error", failure
    return "crash", failure


def _delay(failure: ProviderError, attempt_number: int, control, jitter) -> float:
    """The wait before the next request: the provider's own, or full jitter.

    A wait that does not fit the remaining deadline is not shortened: the turn
    stops, because pretending it would fit would promise an answer that cannot
    arrive.
    """
    remaining = control.remaining_seconds()
    if failure.retry_after_seconds is not None:
        delay = failure.retry_after_seconds
        if delay > remaining:
            raise ProviderError(
                "budget_exceeded",
                retry_after_seconds=delay,
                safe_message=(
                    f"the provider asked to wait {delay} seconds, which does not fit "
                    f"the {remaining} left in this turn"
                ),
            )
        return delay

    ceiling = min(_BACKOFF_CEILING_SECONDS, 2.0 ** (attempt_number - 1))
    delay = ceiling * jitter()
    if delay > remaining:
        raise ProviderError(
            "budget_exceeded",
            safe_message=(
                f"the next attempt cannot start inside the {remaining} left in this turn"
            ),
        )
    return delay


def _reason(index: int, made: int) -> str:
    """Why this attempt happened, so a run's history reads as a decision."""
    if made == 0:
        return _INITIAL
    return "fallback" if index else "retry"


def _retryable(failure: ProviderError, observed: _Observed) -> bool:
    """Only a transient failure before any answer content may be tried again."""
    return failure.retryable and not observed.produced()


async def run_model_turn(
    request: ModelRequest,
    config: RunModelConfig,
    control,
    recorder,
    *,
    sleeper=None,
    jitter=None,
) -> ProviderTurnResult:
    """Run one bounded model turn through the configuration this run pinned.

    Raises ``ProviderError`` when the turn produced no answer; otherwise
    returns the canonical result, whose ``finish_reason`` says whether the
    answer is publishable.

    ``sleeper`` and ``jitter`` are injectable so a test can own the turn's
    clock: a case about policy is never a case about wall time.
    """
    sleeper = sleeper or asyncio.sleep
    jitter = jitter or random.random

    limits = config.limits
    destinations = [config.primary, *config.fallbacks]
    _admit(request, limits, destinations[0])

    spent = _Spent()
    index = 0
    made = 0

    while True:
        profile = destinations[index]
        if index:
            # A fallback is admitted on its own merits, before it is asked.
            _admit(request, limits, profile)

        adapter = build_adapter(profile)
        reservation = await _reserve(
            adapter, request, profile, limits, limits.max_attempts - made, spent, control
        )

        control.raise_if_stopped()

        attempt_id = recorder.start(
            profile,
            reason=_reason(index, made),
            reserved_cost_usd=reservation[1] if reservation is not None else None,
        )
        observed = _Observed()
        kind, detail = await _attempt(adapter, request, profile, control, observed)
        made += 1

        if kind == "interrupted":
            code = detail
            recorder.finish(
                attempt_id,
                "cancelled" if code == "cancelled" else "failed",
                usage=observed.usage,
                error_code=code,
            )
            raise ProviderError(code) from None

        if kind == "crash":
            recorder.finish(attempt_id, "failed", usage=observed.usage, error_code="invalid_response")
            raise ProviderError("invalid_response", cause=detail) from detail

        if kind == "error":
            failure = detail
            recorder.finish(
                attempt_id,
                "cancelled" if failure.code == "lease_lost" else "failed",
                usage=observed.usage,
                error_code=failure.code,
            )
            # An attempt that did not answer still used its reserved bound.
            if reservation is not None:
                spent.consume(*reservation)
            if not _retryable(failure, observed):
                raise failure
            if made >= limits.max_attempts:
                raise failure

            next_index = index + 1
            if next_index < len(destinations) and destinations[next_index].id in profile.allowed_fallback_profile_ids:
                index = next_index
                continue

            delay = _delay(failure, made, control, jitter)
            await sleeper(delay)
            # The stop signal and the fence are checked again after the wait,
            # so a cancelled turn never starts another request.
            control.raise_if_stopped()
            continue

        # The stream finished. A provider that served another model is not
        # this profile's answer, and is never retried.
        if (
            observed.actual_model
            and observed.actual_model != profile.model
            and observed.actual_model not in profile.qualified_model_aliases
        ):
            recorder.finish(
                attempt_id,
                "failed",
                usage=observed.usage,
                error_code="unexpected_model",
                actual_model=observed.actual_model,
            )
            raise ProviderError(
                "unexpected_model",
                safe_message=(
                    f"the provider served {observed.actual_model} for profile {profile.id!r}, "
                    "which does not qualify that model"
                ),
            ) from None

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
            attempt_count=made,
            finish_reason=observed.reason or "stop",
            partial=observed.reason is not None and observed.reason != "stop",
        )