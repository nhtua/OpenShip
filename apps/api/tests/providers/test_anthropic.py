"""Anthropic-specific behaviour: the Messages API's own shapes.

The shared suite proves what every adapter must do. These are the claims only
this API can make or break: it takes the system turn as its own parameter,
names an end differently, reports the input count only in the snapshot it
opens a stream with, and sends blocks that are not answer text - reasoning
and tool calls - between the ones that are.

The breaks these catch: an adapter that puts the system turn in the history,
that reads a reasoning block as the answer, that invents a total this API
never reports, or that guesses the meaning of an end it has not seen.
"""

import os

import pytest

from src.openship.providers.anthropic import AnthropicAdapter
from src.openship.providers.errors import ProviderError

from .conftest import (
    CountingClient,
    adapter_case,
    anthropic_block_start,
    anthropic_block_stop,
    anthropic_finish,
    anthropic_message_start,
    anthropic_ping,
    anthropic_stop,
    anthropic_text,
    anthropic_thinking,
    anthropic_tool_json,
    control,
    conversation,
    drain,
    error_fixture,
    provider_stream,
    qualified_profile,
)

FIXTURE_KEY = "FIXTURE_KEY"  # rayloc:ignore

#: A counter this provider qualifies. Only its name is committed; the count it
#: would return is a fixture's own number, never a measurement.
COUNTING = "anthropic-count-tokens"  # rayloc:ignore


def _counting_profile():
    """The same destination, qualified for the provider's own counter."""
    return qualified_profile(
        adapter="anthropic",
        capabilities={
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
        adapter_options={
            "adapter": "anthropic",
            "server_side_fallback": False,
            "token_counter_id": COUNTING,
        },
    )


@pytest.mark.asyncio
async def test_the_system_turn_is_given_as_the_api_spells_it():
    """The system turn is its own parameter, and never part of the history."""
    case = adapter_case(
        adapter="anthropic",
        chunks=provider_stream("anthropic", text=["ok"]),
    )
    request = conversation(
        ("system", "Be brief."), ("user", "What failed?"), ("assistant", "A deploy.")
    )

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure is None
    assert case.transport.seen_kwargs["system"] == "Be brief."
    assert [turn["role"] for turn in case.transport.seen_kwargs["messages"]] == [
        "user",
        "assistant",
    ]
    assert case.transport.seen_kwargs["max_tokens"] == 128


@pytest.mark.asyncio
async def test_the_adapter_asks_only_for_what_openship_decides():
    """No SDK default is left to decide something OpenShip owns."""
    case = adapter_case(
        adapter="anthropic", chunks=provider_stream("anthropic", text=["ok"])
    )

    await drain(
        case.adapter,
        conversation(("system", "Be brief."), ("user", "hi")),
        case.profile,
        control(),
    )

    assert set(case.transport.seen_kwargs) == {
        "model",
        "messages",
        "system",
        "max_tokens",
        "stream",
        "timeout",
    }


@pytest.mark.asyncio
async def test_only_text_blocks_become_the_answer():
    """Reasoning and tool blocks are neither answer text nor an action."""
    case = adapter_case(
        adapter="anthropic",
        chunks=[
            anthropic_message_start(),
            anthropic_block_start("thinking"),
            anthropic_thinking("checking the deploy log"),
            anthropic_block_stop(),
            anthropic_block_start("tool_use", index=1),
            anthropic_tool_json('{"command": '),
            anthropic_block_stop(index=1),
            anthropic_text("The build failed."),
            anthropic_finish("end_turn"),
            anthropic_stop(),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "what broke?")), case.profile, control()
    )

    assert failure is None
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "The build failed."
    assert events[-1].reason == "stop"


@pytest.mark.asyncio
async def test_a_non_content_event_neither_ends_nor_extends_an_answer():
    """A ping and an event this build has not seen are both ignored."""
    case = adapter_case(
        adapter="anthropic",
        chunks=[
            anthropic_message_start(),
            anthropic_ping(),
            anthropic_text("a"),
            {"type": "audit", "note": "not a content event"},
            anthropic_text("b"),
            anthropic_finish("end_turn"),
            anthropic_stop(),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert "".join(e.text for e in events if e.type == "text_delta") == "ab"


@pytest.mark.asyncio
async def test_the_input_count_comes_from_the_snapshot_that_carries_it():
    """The end never carries an input count, so the first one is kept."""
    case = adapter_case(
        adapter="anthropic",
        chunks=provider_stream("anthropic", text=["hi"], usage=[(10, 8)]),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    updates = [e.usage for e in events if e.type == "usage_update"]
    assert (updates[-1].input_tokens, updates[-1].output_tokens) == (10, 8)


@pytest.mark.asyncio
async def test_a_total_is_never_invented_from_counts_that_werent_reported():
    """This API reports no total, so none is claimed."""
    case = adapter_case(
        adapter="anthropic",
        chunks=provider_stream("anthropic", text=["hi"], usage=[(10, 8)]),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    updates = [e.usage for e in events if e.type == "usage_update"]
    assert updates[-1].total_tokens is None
    assert updates[-1].cached_input_tokens is None


@pytest.mark.asyncio
async def test_a_stream_that_fails_after_answering_is_the_same_failure():
    """An error after HTTP success is normalized, and the stream is closed."""
    case = adapter_case(
        adapter="anthropic",
        chunks=[
            anthropic_message_start(),
            anthropic_text("part of an answer"),
            error_fixture("anthropic"),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "rate_limited"
    assert "".join(e.text for e in events if e.type == "text_delta") == "part of an answer"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_a_rate_limit_keeps_the_repair_hint():
    """The provider's own delay survives; its body does not."""
    case = adapter_case(adapter="anthropic", chunks=[], raises=error_fixture("anthropic"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "rate_limited"
    assert failure.retry_after_seconds == 7.0
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_an_overloaded_provider_is_reported_as_unreachable():
    """A server-side failure is not a rate limit with a made-up delay."""
    case = adapter_case(
        adapter="anthropic", chunks=[], raises=error_fixture("anthropic", "overloaded")
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"
    assert failure.retry_after_seconds is None


@pytest.mark.asyncio
async def test_the_provider_ends_are_mapped_not_passed_through():
    """Each end this API names means one thing in the contract."""
    for provider_end, contract_reason in (
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
    ):
        case = adapter_case(
            adapter="anthropic",
            chunks=[
                anthropic_message_start(),
                anthropic_text("a"),
                anthropic_finish(provider_end),
                anthropic_stop(),
            ],
        )

        events, failure = await drain(
            case.adapter, conversation(("user", "hi")), case.profile, control()
        )

        assert failure is None
        assert events[-1].reason == contract_reason


@pytest.mark.asyncio
async def test_an_end_this_build_has_not_seen_is_not_guessed():
    """A new vocabulary is a failure, not a claim of a complete answer."""
    case = adapter_case(
        adapter="anthropic",
        chunks=[
            anthropic_message_start(),
            anthropic_text("a"),
            anthropic_finish("model_wrapped_up"),
            anthropic_stop(),
        ],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert "model_wrapped_up" in str(failure.cause)


@pytest.mark.asyncio
async def test_a_cancellation_mid_answer_leaves_a_partial():
    """Stopping after some text arrived completes nothing."""
    case = adapter_case(
        adapter="anthropic",
        chunks=provider_stream("anthropic", text=["a", "b", "c"]),
    )
    seen = {"calls": 0}

    def stopped_after_the_first_part():
        seen["calls"] += 1
        return seen["calls"] > 3

    _events, failure = await drain(
        case.adapter,
        conversation(("user", "hi")),
        case.profile,
        control(stop=stopped_after_the_first_part),
    )

    text = "".join(e.text for e in _events if e.type == "text_delta")
    assert failure.code == "cancelled"
    assert text.startswith("a")
    assert text != "abc"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_the_sdk_never_multiplies_our_requests():
    """One failed attempt is one request; retrying is the runner's decision."""
    case = adapter_case(adapter="anthropic", chunks=[], raises=error_fixture("anthropic"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert case.transport.requests == 1
    assert failure.code == "rate_limited"


def test_sdk_retries_are_disabled_and_the_request_is_bounded():
    """OpenShip owns retries and request timeouts, not the SDK."""
    os.environ[FIXTURE_KEY] = "sentinel-credential-value"  # rayloc:ignore
    try:
        profile = qualified_profile(adapter="anthropic")
        client = AnthropicAdapter().build_client(profile)

        assert client.max_retries == 0
        assert client.timeout == profile.limits.request_timeout_seconds
        assert client._client.follow_redirects is False
        assert str(client.base_url).startswith(profile.endpoint)
    finally:
        os.environ.pop(FIXTURE_KEY, None)


def test_a_missing_credential_refuses_before_any_request():
    """An unbound binding is a configuration failure, not a silent default."""
    os.environ.pop(FIXTURE_KEY, None)
    adapter = AnthropicAdapter()

    with pytest.raises(ProviderError) as caught:
        adapter.build_client(qualified_profile(adapter="anthropic"))
    assert caught.value.code == "configuration_error"
    assert "FIXTURE_KEY" in caught.value.safe_message


@pytest.mark.asyncio
async def test_counting_asks_the_providers_own_counter():
    """A qualified counter is called with the same conversation, and its own."""
    client = CountingClient(123)
    adapter = AnthropicAdapter(client_factory=lambda _config: client)
    request = conversation(("system", "Be brief."), ("user", "count this"))

    counted = await adapter.count_input_tokens(
        request, _counting_profile(), control(remaining=5.0)
    )

    assert counted == 123
    assert client.asked[0]["system"] == "Be brief."
    assert [turn["role"] for turn in client.asked[0]["messages"]] == ["user"]
    assert client.asked[0]["timeout"] == 5.0


@pytest.mark.asyncio
async def test_counting_is_refused_when_the_profile_has_no_counter():
    """No qualified counter means no token counting, not an estimate."""
    case = adapter_case(adapter="anthropic", chunks=[])
    request = conversation(("user", "count this"))

    with pytest.raises(ProviderError) as caught:
        await case.adapter.count_input_tokens(request, case.profile, control())

    assert caught.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_a_counter_that_answered_without_a_count_is_not_a_zero():
    """The caller reserves against this number, so a guess is not offered."""
    adapter = AnthropicAdapter(client_factory=lambda _config: CountingClient(None))

    with pytest.raises(ProviderError) as caught:
        await adapter.count_input_tokens(
            conversation(("user", "count this")), _counting_profile(), control()
        )

    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_a_counter_this_adapter_does_not_implement_is_not_substituted():
    """Naming a counter is a request for that one, not for any counter."""
    profile = _counting_profile().model_dump()
    profile["adapter_options"]["token_counter_id"] = "some-other-counter"

    with pytest.raises(ProviderError) as caught:
        await AnthropicAdapter().count_input_tokens(
            conversation(("user", "count this")),
            qualified_profile(**profile),
            control(),
        )

    assert caught.value.code == "unsupported_capability"
    assert "some-other-counter" in caught.value.safe_message


@pytest.mark.asyncio
async def test_native_sdk_redirect_is_not_followed_or_forwarded_with_credentials():
    import httpx2
    from anthropic import AsyncAnthropic
    from anthropic._streaming import AsyncStream
    from anthropic.types import RawMessageStreamEvent

    profile = qualified_profile(adapter="anthropic")
    sdk_client = AsyncAnthropic(api_key="sentinel-credential-value", base_url=profile.endpoint)
    request = httpx2.Request("POST", profile.endpoint + "/messages")
    response = httpx2.Response(
        307,
        headers={"location": "https://unapproved.example/steal"},
        request=request,
    )
    sdk_stream = AsyncStream(cast_to=RawMessageStreamEvent, response=response, client=sdk_client)

    class StreamClient:
        class messages:
            @staticmethod
            async def create(**_kwargs):
                return sdk_stream

    adapter = AnthropicAdapter(client_factory=lambda _profile: StreamClient())
    _events, failure = await drain(
        adapter, conversation(("user", "synthetic private context")), profile, control()
    )
    await sdk_client.close()

    assert failure.code == "configuration_error"


@pytest.mark.asyncio
async def test_constructed_sdk_http_client_does_not_forward_anthropic_redirect(monkeypatch):
    import httpx2

    monkeypatch.setenv(FIXTURE_KEY, "sentinel-credential-value")  # rayloc:ignore
    profile = qualified_profile(adapter="anthropic")
    client = AnthropicAdapter().build_client(profile)
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx2.Response(
            307,
            headers={"location": "https://unapproved.example/steal"},
            request=request,
        )

    client._client._transport = httpx2.MockTransport(respond)
    request = client._client.build_request(
        "POST",
        profile.endpoint + "/messages",
        headers={"x-api-key": "sentinel-credential-value"},
        json={"messages": [{"role": "user", "content": "synthetic private context"}]},
    )
    await client._client.send(request)
    await client.close()
    assert len(seen) == 1
    assert str(seen[0].url) == profile.endpoint.rstrip("/") + "/messages"
    assert b"synthetic private context" in seen[0].content
    assert seen[0].headers["x-api-key"] == "sentinel-credential-value"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
async def test_native_sdk_server_errors_are_provider_unavailable(status):
    import httpx2
    from anthropic import APIStatusError

    from src.openship.providers.anthropic import _sdk_failure

    request = httpx2.Request("POST", "https://fixture.provider.test/v1/messages")
    response = httpx2.Response(status, request=request)
    failure = _sdk_failure(APIStatusError("provider error", response=response, body=None))
    assert failure.code == "provider_unavailable"


@pytest.mark.asyncio
async def test_an_unqualified_model_alias_is_reported():
    """A provider that serves a different model is not silently accepted."""
    case = adapter_case(
        adapter="anthropic",
        chunks=[
            anthropic_message_start(model="other-model"),
            anthropic_text("hi"),
            anthropic_finish("end_turn"),
            anthropic_stop(),
        ],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "unexpected_model"
    assert _events[-1].actual_model == "other-model"
