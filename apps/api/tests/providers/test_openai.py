"""OpenAI-specific behavior: the qualified Chat Completions baseline.

The breaks these catch: an adapter that lets the SDK retry on its own,
assumes every model accepts `max_tokens`, invents usage, or builds a client
without a credential.
"""

import os

import pytest

from src.openship.providers.errors import ProviderError
from src.openship.providers.openai import OpenAIAdapter

from .conftest import (
    adapter_case,
    control,
    conversation,
    drain,
    error_fixture,
    qualified_profile,
    sdk_chunk,
)

FIXTURE_KEY = "FIXTURE_KEY"  # rayloc:ignore


@pytest.mark.asyncio
async def test_usage_is_requested_explicitly():
    """A provider that reports usage only when asked is asked."""
    case = adapter_case(
        chunks=[sdk_chunk(content="hi", finish_reason="stop"), sdk_chunk(choices=False, usage=(3, 2, 5))]
    )

    await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert case.transport.seen_kwargs["stream_options"] == {"include_usage": True}
    assert case.transport.seen_kwargs["stream"] is True


@pytest.mark.asyncio
async def test_the_output_parameter_belongs_to_the_profile():
    """A profile that needs max_completion_tokens is not sent max_tokens."""
    for parameter in ("max_tokens", "max_completion_tokens"):
        other = "max_tokens" if parameter == "max_completion_tokens" else "max_completion_tokens"
        case = adapter_case(
            chunks=[sdk_chunk(content="hi", finish_reason="stop")],
            profile_kwargs={"output_limit_parameter": parameter},
        )

        await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

        assert case.transport.seen_kwargs[parameter] == 128
        assert other not in case.transport.seen_kwargs


@pytest.mark.asyncio
async def test_the_request_never_outruns_the_turn():
    """The per-request timeout is the smaller of the profile's and the turn's."""
    case = adapter_case(chunks=[sdk_chunk(content="hi", finish_reason="stop")])

    await drain(case.adapter, conversation(("user", "hi")), case.profile, control(remaining=5.0))

    assert case.transport.seen_kwargs["timeout"] == 5.0
    assert case.transport.seen_kwargs["timeout"] <= case.profile.limits.request_timeout_seconds
    """The trailing usage chunk is not mistaken for an empty answer."""
    case = adapter_case(
        chunks=[
            sdk_chunk(content="hi", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(3, 2, 5)),
        ]
    )

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    assert "".join(e.text for e in events if e.type == "text_delta") == "hi"
    finished = events[-1]
    assert finished.type == "stream_finished"
    assert finished.reason == "stop"
    assert finished.actual_model == "fixture-model"


@pytest.mark.asyncio
async def test_the_sdk_never_multiplies_our_requests():
    """One failed attempt is one request; retrying is the runner's decision."""
    case = adapter_case(chunks=[], raises=error_fixture("openai"))

    _events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert case.transport.requests == 1
    assert failure.code == "rate_limited"


@pytest.mark.asyncio
async def test_a_rate_limit_keeps_the_repair_hint():
    """A provider's retry delay survives normalization; its body does not."""
    case = adapter_case(chunks=[], raises=error_fixture("openai"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "rate_limited"
    assert failure.retry_after_seconds == 7.0
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_an_unreachable_provider_says_so_without_inventing_a_cause():
    """A connection failure is reported as one, with no provider body."""
    case = adapter_case(chunks=[], raises=error_fixture("openai", "unreachable"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"
    assert failure.retry_after_seconds is None


@pytest.mark.asyncio
async def test_a_refusal_is_carried_as_the_provider_reported_it():
    """The provider's own finish reason is preserved, not renamed here."""
    case = adapter_case(chunks=[sdk_chunk(content="no", finish_reason="refusal")])

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    assert events[-1].type == "stream_finished"
    assert events[-1].reason == "refusal"


@pytest.mark.asyncio
async def test_truncation_is_carried_as_the_provider_reported_it():
    """A length finish reaches the runner unchanged; mapping is the runner's."""
    case = adapter_case(chunks=[sdk_chunk(content="cut", finish_reason="length")])

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    assert events[-1].reason == "length"


def test_sdk_retries_are_disabled_and_the_request_is_bounded():
    """OpenShip owns retries and request timeouts, not the SDK."""
    os.environ[FIXTURE_KEY] = "sentinel-credential-value"  # rayloc:ignore
    try:
        profile = qualified_profile()
        client = OpenAIAdapter().build_client(profile)

        assert client.max_retries == 0
        assert client.timeout == profile.limits.request_timeout_seconds
        assert str(client.base_url).startswith(profile.endpoint)
    finally:
        os.environ.pop(FIXTURE_KEY, None)


def test_a_missing_credential_refuses_before_any_request():
    """An unbound binding is a configuration failure, not a silent default."""
    os.environ.pop(FIXTURE_KEY, None)
    adapter = OpenAIAdapter()

    with pytest.raises(ProviderError) as caught:
        adapter.build_client(qualified_profile())
    assert caught.value.code == "configuration_error"
    assert "FIXTURE_KEY" in caught.value.safe_message


@pytest.mark.asyncio
async def test_counting_is_refused_when_the_profile_has_no_counter():
    """No qualified counter means no token counting, not an estimate."""
    case = adapter_case(chunks=[])
    request = conversation(("user", "count this"))

    with pytest.raises(ProviderError) as caught:
        await case.adapter.count_input_tokens(request, case.profile, control())

    assert caught.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_an_unqualified_model_alias_is_reported():
    """A provider that serves a different model is not silently accepted."""
    case = adapter_case(
        chunks=[sdk_chunk(content="hi", finish_reason="stop", model="other-model")]
    )

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure.code == "unexpected_model"
    assert events[-1].actual_model == "other-model"
