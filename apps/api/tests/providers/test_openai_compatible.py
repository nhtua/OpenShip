"""What a separately configured OpenAI-compatible endpoint does.

The shared suite proves what every adapter must do. These are the claims only
this kind of profile can make or break: the destination is a server the
operator pointed at rather than a vendor's API, so its status codes, its SSE
framing, and its refusal of a parameter are its own; it may hold no
credential at all; and every difference is a declared profile option rather
than something inferred from an exception.

The breaks these catch: an adapter that resends a request the endpoint
rejected, that follows a redirect to a destination the profile never named,
that attaches the installation's OpenAI key to a separately configured
endpoint, that reads a malformed chunk as an empty answer, or that treats an
undeclared capability as a default.
"""

import os

import pytest

from src.openship.providers.errors import ProviderError
from src.openship.providers.openai_compatible import (
    OpenAICompatibleAdapter,
    request_body,
)

from .conftest import (
    CREDENTIAL_REF,
    FIXTURE_ENDPOINT,
    adapter_case,
    compatible_chunk,
    control,
    conversation,
    drain,
    error_fixture,
    provider_stream,
    qualified_profile,
)

#: A value that must never reach a separately configured destination. Only
#: its presence is asserted, never its use.
ENV_KEY = "FIXTURE_ENV_KEY"  # rayloc:ignore

#: The one endpoint implementation this adapter has qualified. Any other is a
#: gap stated before the network, not a guess about a similar-looking API.
QUALIFIED_IMPLEMENTATION = "chat-completions"


def _options(**overrides):
    """The compatibility declarations a profile states, over the fixture's.

    Every one of these is required by the contract, so a case that wanted to
    change one had to restate all of them; this states the defaults once.
    """
    declared = {
        "adapter": "openai_compatible",
        "endpoint_implementation": QUALIFIED_IMPLEMENTATION,
        "authentication": "bearer",
        "stream_usage_supported": True,
        "output_limit_parameter": "max_tokens",
    }
    declared.update(overrides)
    return declared


def _keyless_profile():
    """A local endpoint that declares no credential at all."""
    return qualified_profile(
        adapter="openai_compatible",
        endpoint="http://localhost:11434/v1",
        credential_ref="",
        adapter_options=_options(
            authentication="none", stream_usage_supported=False
        ),
    )


def _refusing_profile():
    """The same destination, declaring an implementation this adapter has not."""
    return qualified_profile(
        adapter="openai_compatible",
        adapter_options=_options(endpoint_implementation="responses"),
    )


def _unbound_bearer():
    """A profile that needs a credential but names no binding for it."""
    return qualified_profile(
        adapter="openai_compatible",
        credential_ref="",
        adapter_options=_options(),
    )


@pytest.mark.asyncio
async def test_the_request_is_the_one_the_first_adapter_asks_for():
    """A compatible endpoint is asked the way Chat Completions is."""
    compatible = adapter_case(adapter="openai_compatible", chunks=[])
    primary = adapter_case(adapter="openai", chunks=[])
    request = conversation(("system", "Be brief."), ("user", "hi"))

    await drain(compatible.adapter, request, compatible.profile, control())
    await drain(primary.adapter, request, primary.profile, control())

    assert compatible.transport.seen_kwargs["json"] == {
        name: value
        for name, value in primary.transport.seen_kwargs.items()
        if name != "timeout"
    }
    # The timeout is not part of the body: the SDK carries it with the call,
    # this adapter carries it on the transport. Everything that is part of
    # the request is the same.
    assert compatible.transport.seen_kwargs["url"] == FIXTURE_ENDPOINT + "/chat/completions"


@pytest.mark.asyncio
async def test_the_output_bound_is_the_one_the_profile_named():
    """The limit parameter is the profile's, not a default."""
    case = adapter_case(
        adapter="openai_compatible",
        profile_kwargs={"adapter_options": _options(
            output_limit_parameter="max_completion_tokens"
        )},
        chunks=[],
    )

    await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    body = case.transport.seen_kwargs["json"]
    assert body["max_completion_tokens"] == 128
    assert "max_tokens" not in body


def test_the_usage_option_is_asked_only_where_the_endpoint_reports_it():
    """A profile that says it will not report counts is not asked to."""
    assert "stream_options" not in request_body(
        conversation(("user", "hi")), _keyless_profile()
    )
    assert request_body(
        conversation(("user", "hi")),
        qualified_profile(adapter="openai_compatible"),
    )["stream_options"] == {"include_usage": True}


def test_the_bearer_credential_is_the_profiles_own():
    """A separately configured endpoint never takes the installation's key."""
    os.environ[CREDENTIAL_REF] = "sentinel-credential-value"  # rayloc:ignore
    os.environ["OPENAI_API_KEY"] = ENV_KEY
    try:
        client = OpenAICompatibleAdapter().build_client(
            qualified_profile(adapter="openai_compatible")
        )

        assert client.headers["Authorization"] == "Bearer sentinel-credential-value"
        assert ENV_KEY not in client.headers["Authorization"]
    finally:
        os.environ.pop(CREDENTIAL_REF, None)
        os.environ.pop("OPENAI_API_KEY", None)


def test_a_keyless_profile_is_servable():
    """Declaring no credential is a shape, not a broken configuration."""
    os.environ["OPENAI_API_KEY"] = ENV_KEY
    try:
        client = OpenAICompatibleAdapter().build_client(_keyless_profile())

        assert "Authorization" not in client.headers
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


@pytest.mark.asyncio
async def test_a_local_destination_is_reached_as_declared():
    """The path is the endpoint's own, not one this build assumes."""
    case = adapter_case(
        adapter="openai_compatible",
        profile_kwargs={
            "endpoint": "http://localhost:11434/v1",
            "credential_ref": "",
            "adapter_options": _options(
                authentication="none", stream_usage_supported=False
            ),
        },
        chunks=provider_stream("openai_compatible", text=["local"], finish="stop"),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert case.transport.seen_kwargs["url"] == (
        "http://localhost:11434/v1/chat/completions"
    )
    assert (
        "".join(e.text for e in events if e.type == "text_delta") == "local"
    )


@pytest.mark.asyncio
async def test_an_undeclared_implementation_is_refused_before_the_network():
    """An implementation this adapter has not qualified is a gap, not a guess."""
    _events, failure = await drain(
        OpenAICompatibleAdapter(),
        conversation(("user", "hi")),
        _refusing_profile(),
        control(),
    )

    assert failure.code == "unsupported_capability"
    assert "responses" in failure.safe_message


@pytest.mark.asyncio
async def test_an_unusable_declaration_never_reaches_the_network():
    """A profile that needs a bearer credential but names none is a gap."""
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleAdapter().build_client(_unbound_bearer())

    assert caught.value.code == "configuration_error"
    assert "credential" in caught.value.safe_message


@pytest.mark.asyncio
async def test_a_rejected_request_is_reported_not_resent():
    """The operator configures the option; the adapter never guesses it."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[],
        raises=error_fixture("openai_compatible", "rejected"),
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert case.transport.requests == 1, "a rejected request is never resent"
    assert failure.code == "invalid_request"
    assert failure.retryable is False


@pytest.mark.asyncio
async def test_a_rate_limited_endpoint_keeps_the_repair_hint():
    """The provider's own delay survives; its body does not."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[],
        raises=error_fixture("openai_compatible"),
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "rate_limited"
    assert failure.retry_after_seconds == 7.0
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_is_the_same_failure_as_any_other():
    """A server that answers nothing is not a different kind of answer."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[],
        raises=error_fixture("openai_compatible", "unreachable"),
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"
    assert failure.retryable is True
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_the_status_a_server_answers_is_read_not_assumed():
    """A 500 on the normal path is a failure, not a stream to parse."""
    case = adapter_case(adapter="openai_compatible", chunks=[], status=500)

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"
    assert case.transport.requests == 1


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed():
    """A destination that redirects is not the destination the profile named."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[],
        status=302,
        headers={"location": "https://elsewhere.test/v1/chat/completions"},
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "configuration_error"
    assert case.transport.requests == 1
    assert case.transport.seen_kwargs["follow_redirects"] is False


@pytest.mark.asyncio
async def test_a_malformed_chunk_is_a_failure_not_an_empty_answer():
    """A chunk this build cannot read is not a claim that nothing was said."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[
            compatible_chunk(content="part of an answer"),
            "data: {not json",
            "",
            "data: [DONE]",
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert (
        "".join(e.text for e in events if e.type == "text_delta")
        == "part of an answer"
    )
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_a_stream_that_never_ends_is_not_an_answer():
    """The terminal marker is the end of the stream; without it, nothing is."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[
            compatible_chunk(content="half an answer"),
            compatible_chunk(finish_reason="stop"),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert (
        "".join(e.text for e in events if e.type == "text_delta")
        == "half an answer"
    )
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_framing_that_carries_no_event_is_not_answer_text():
    """A blank line and a comment are part of the protocol, not the answer."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[
            "",
            ": keep-alive",
            compatible_chunk(content="the answer"),
            "",
            compatible_chunk(finish_reason="stop"),
            "",
            "data: [DONE]",
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert (
        "".join(e.text for e in events if e.type == "text_delta") == "the answer"
    )
    assert events[-1].reason == "stop"


@pytest.mark.asyncio
async def test_an_end_that_names_a_tool_call_is_not_a_malformed_answer():
    """This build does not run tools, but the end it was told is still its own."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[
            compatible_chunk(content="let me look up"),
            compatible_chunk(finish_reason="tool_calls"),
            "data: [DONE]",
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert events[-1].reason == "tool_calls"


@pytest.mark.asyncio
async def test_an_unqualified_model_alias_is_reported():
    """A server that serves another model is not silently accepted."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=[
            compatible_chunk(content="hi", model="other-model"),
            compatible_chunk(finish_reason="stop", model="other-model"),
            "data: [DONE]",
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "unexpected_model"
    assert events[-1].actual_model == "other-model"


@pytest.mark.asyncio
async def test_a_stalled_read_is_stopped_and_closed():
    """Stopping part-way through a stream closes the local read."""
    case = adapter_case(
        adapter="openai_compatible",
        chunks=provider_stream("openai_compatible", text=["a", "b", "c"]),
    )
    seen = {"calls": 0}

    def stopped_after_the_first_part():
        seen["calls"] += 1
        return seen["calls"] > 2

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
async def test_counting_is_refused_until_an_endpoint_qualifies_a_counter():
    """No tokenizer is assumed for a server this build has not measured."""
    case = adapter_case(adapter="openai_compatible", chunks=[])

    with pytest.raises(ProviderError) as caught:
        await case.adapter.count_input_tokens(
            conversation(("user", "count this")), case.profile, control()
        )

    assert caught.value.code == "unsupported_capability"
