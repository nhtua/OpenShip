"""The shared runner: bounded retries, authorized fallback, and honest budgets.

Every case drives the same runner against a transport that answers differently
per request, so a case is a sequence of attempts rather than a mock of the
runner's internals. Time is virtual: the control's clock and the runner's
sleeper are injected, so a backoff never makes a case slow.

The breaks these catch: an SDK that retries on its own and multiplies requests,
a fallback that runs without being authorized, a partial answer retried as if
starting over, and a budget reported as if a number were known.
"""

import pytest

from decimal import Decimal

from src.openship.providers.contracts import RunLimits, RunModelConfig
from src.openship.providers.errors import ProviderError
from src.openship.providers.runner import run_model_turn

from .conftest import (
    CountingAdapter,
    FakeRecorder,
    control,
    conversation,
    error_fixture,
    qualified_profile,
    sdk_chunk,
    serve_counted,
    serve_requests,
)

PRIMARY = "primary-fixture"
FALLBACK = "fallback-fixture"
PRIMARY_MODEL = "primary-model"
FALLBACK_MODEL = "fallback-model"

CONVERSATION = (("system", "Be brief."), ("user", "What happened?"))

#: What a qualified counter reports for the fixture conversation, so a case can
#: say what one reserved attempt costs. Not a measurement of any real model.
COUNTED_INPUT_TOKENS = 100


def _primary(**overrides):
    """The destination a run pinned, and the one its operator may fall back to."""
    spec = {"id": PRIMARY, "model": PRIMARY_MODEL, "allowed_fallback_profile_ids": [FALLBACK]}
    spec.update(overrides)
    return qualified_profile(**spec)


def _alternate(**overrides):
    """A second destination, so an unauthorized or unasked one is visible."""
    spec = {"id": FALLBACK, "model": FALLBACK_MODEL}
    spec.update(overrides)
    return qualified_profile(**spec)


def _counting(**overrides):
    """A destination that qualifies a counter and a conservative price."""
    spec = {
        "id": PRIMARY,
        "model": PRIMARY_MODEL,
        "allowed_fallback_profile_ids": [FALLBACK],
        "capabilities": {
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": 32768,
            "max_output_tokens": 4096,
            "token_counting": True,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "adapter_options": {
            "adapter": "openai",
            "output_limit_parameter": "max_tokens",
            "token_counter_id": "fixture-counter",
        },
        "pricing": {
            "revision": "fixture-schedule",
            "currency": "USD",
            "input_per_million": "1000",
            "output_per_million": "1000",
        },
    }
    spec.update(overrides)
    return qualified_profile(**spec)


def _pricier_fallback():
    """The same second destination, priced higher, so a bound is comparable."""
    return _counting(
        id=FALLBACK,
        model=FALLBACK_MODEL,
        allowed_fallback_profile_ids=[],
        pricing={
            "revision": "other-schedule",
            "currency": "USD",
            "input_per_million": "2000",
            "output_per_million": "2000",
        },
    )


def _config(primary=None, fallbacks=(), **limits):
    """The provider configuration a run carries, with the limits a case needs."""
    return RunModelConfig(
        schema_version=1,
        primary=primary or _primary(),
        fallbacks=tuple(fallbacks),
        limits=RunLimits(**limits),
        binding_origin="submission",
    )


async def no_wait(_seconds):
    """A backoff the runner is told about: a case never waits for real time."""
    return None


def no_jitter():
    """Full jitter at its lower bound, so a case is deterministic."""
    return 0.0


async def _turn(
    monkeypatch,
    behaviors,
    *,
    config=None,
    control_=None,
    recorder=None,
    counted=None,
    sleeper=no_wait,
    jitter=no_jitter,
):
    """One turn through the runner against a sequence of transport answers."""
    if counted is None:
        transport = serve_requests(monkeypatch, behaviors)
    else:
        transport = serve_counted(monkeypatch, behaviors, counted)

    result = await run_model_turn(
        conversation(*CONVERSATION),
        config if config is not None else _config(),
        control_ if control_ is not None else control(),
        recorder if recorder is not None else FakeRecorder(),
        sleeper=sleeper,
        jitter=jitter,
    )
    return result, transport


def _asked_for(transport):
    """The models the transport was actually asked for, in order."""
    return [seen["model"] for seen in transport.seen]


@pytest.mark.asyncio
async def test_429_then_success_is_two_attempts(monkeypatch):
    """A rate-limited request is retried once: the answer is real, and so are two attempts."""
    recorder = FakeRecorder()
    result, transport = await _turn(
        monkeypatch,
        [
            {"raises": error_fixture("openai", "rate_limited")},
            {"chunks": [sdk_chunk(model=PRIMARY_MODEL, content="It worked", finish_reason="stop")]},
        ],
        recorder=recorder,
    )

    assert result.text == "It worked"
    assert result.completed is True
    assert result.attempt_count == 2
    assert transport.requests == 2
    assert _asked_for(transport) == [PRIMARY_MODEL, PRIMARY_MODEL]
    assert recorder.started == [(PRIMARY, "initial"), (PRIMARY, "retry")]


@pytest.mark.asyncio
async def test_sdk_retries_do_not_multiply_requests(monkeypatch):
    """The runner owns request counts: a rate-limited turn asks exactly max_attempts times."""
    with pytest.raises(ProviderError) as refused:
        await _turn(
            monkeypatch,
            [{"raises": error_fixture("openai", "rate_limited")}],
            config=_config(max_attempts=2),
        )

    assert refused.value.code == "rate_limited"
    assert refused.value.retry_after_seconds == 7.0, "the provider's own hint is preserved"


@pytest.mark.asyncio
async def test_three_attempt_limit_includes_fallback(monkeypatch):
    """The attempt budget is shared: fallback is a further attempt, never a fresh one."""
    transport = serve_requests(monkeypatch, [{"raises": error_fixture("openai", "rate_limited")}])

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(fallbacks=[_alternate()], max_attempts=3),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "rate_limited"
    assert transport.requests == 3, "three generation requests, across every destination"
    assert _asked_for(transport) == [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        FALLBACK_MODEL,
    ], "an eligible fallback comes before retrying the same destination"


@pytest.mark.asyncio
async def test_retry_after_beyond_deadline_stops(monkeypatch):
    """A provider's own wait that cannot fit the turn stops it, and is not shortened."""
    transport = serve_requests(monkeypatch, [{"raises": error_fixture("openai", "rate_limited")}])

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(),
            control(remaining=2.0),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "budget_exceeded"
    assert refused.value.retry_after_seconds == 7.0, "the hint is carried, not swallowed"
    assert transport.requests == 1, "no request is made past a deadline it cannot fit"


@pytest.mark.asyncio
async def test_cancel_during_backoff_stops_next_request(monkeypatch):
    """A cancellation during the wait between attempts stops the next request."""
    stopped = {"now": False}

    async def during_backoff(_seconds):
        stopped["now"] = True

    transport = serve_requests(monkeypatch, [{"raises": error_fixture("openai", "rate_limited")}])

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(),
            control(stop=lambda: stopped["now"]),
            FakeRecorder(),
            sleeper=during_backoff,
            jitter=lambda: 1.0,
        )

    assert refused.value.code == "cancelled"
    assert transport.requests == 1, "the next request is never made"


@pytest.mark.asyncio
async def test_partial_output_never_retries_or_falls_back(monkeypatch):
    """A stream that already began answering is never started over."""
    transport = serve_requests(
        monkeypatch,
        [
            {
                "chunks": [
                    sdk_chunk(model=PRIMARY_MODEL, content="A beginning"),
                    error_fixture("openai", "unreachable"),
                ]
            }
        ],
    )

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(fallbacks=[_alternate()]),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "provider_unavailable"
    assert transport.requests == 1, "output already produced is not thrown away for a retry"


@pytest.mark.asyncio
async def test_auth_and_refusal_never_fall_back(monkeypatch):
    """Only a transient failure may be retried or answered by another destination."""
    transport = serve_requests(monkeypatch, [{"raises": error_fixture("openai", "unauthorized")}])

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(fallbacks=[_alternate()]),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "authentication_error"
    assert transport.requests == 1

    result, transport = await _turn(
        monkeypatch,
        [{"chunks": [sdk_chunk(model=PRIMARY_MODEL, content="No.", finish_reason="refusal")]}],
        config=_config(fallbacks=[_alternate()]),
    )

    assert result.finish_reason == "refusal"
    assert result.completed is False
    assert transport.requests == 1, "a refusal is the model's answer, not a failure to retry"


@pytest.mark.asyncio
async def test_unauthorized_fallback_makes_no_request(monkeypatch):
    """A destination the profile's allowlist does not name is never asked."""
    transport = serve_requests(monkeypatch, [{"raises": error_fixture("openai", "rate_limited")}])

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(
                primary=_primary(allowed_fallback_profile_ids=[]),
                fallbacks=[_alternate()],
                max_attempts=3,
            ),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "rate_limited"
    assert _asked_for(transport) == [PRIMARY_MODEL] * 3
    assert FALLBACK_MODEL not in _asked_for(transport), (
        "the user's list authorizes a turn; the profile's allowlist permits it"
    )


@pytest.mark.asyncio
async def test_unexpected_actual_model_fails(monkeypatch):
    """A provider that served another model fails, and is not retried."""
    transport = serve_requests(
        monkeypatch,
        [{"chunks": [sdk_chunk(model="other-model", content="ok", finish_reason="stop")]}],
    )

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "unexpected_model"
    assert transport.requests == 1


@pytest.mark.asyncio
async def test_unknown_failed_usage_consumes_reservation(monkeypatch):
    """A failed request with unknown counts is charged its bound, not nothing."""
    recorder = FakeRecorder()
    transport = serve_counted(
        monkeypatch,
        [{"raises": error_fixture("openai", "rate_limited")}],
        COUNTED_INPUT_TOKENS,
    )

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(
                primary=_counting(),
                fallbacks=[_pricier_fallback()],
                max_attempts=3,
                max_cost_usd="0.912",
            ),
            control(),
            recorder,
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "budget_exceeded"
    assert recorder.reservations == [Decimal("0.228")], "the attempt records what it reserved"
    assert transport.requests == 1, (
        "the fallback's own bound no longer fits once the failed request is charged its bound"
    )


@pytest.mark.asyncio
async def test_cost_limit_requires_qualified_pricing(monkeypatch):
    """A cost ceiling the profile cannot price is refused before any request."""
    transport = serve_requests(
        monkeypatch, [{"chunks": [sdk_chunk(model=PRIMARY_MODEL, content="never asked", finish_reason="stop")]}]
    )

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            conversation(*CONVERSATION),
            _config(max_cost_usd="0.01"),
            control(),
            FakeRecorder(),
            sleeper=no_wait,
            jitter=no_jitter,
        )

    assert refused.value.code == "configuration_error"
    assert "pricing" in refused.value.safe_message
    assert transport.requests == 0, "an unpriceable ceiling is never treated as zero cost"
