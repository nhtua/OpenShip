"""The conformance suite every adapter must pass, written once.

Each case drives a real adapter against a synthetic transport at the SDK's
outer edge. The break these catch: an adapter that loses conversation order,
invents usage, reports a partial answer as complete, leaks the provider's own
error text, or leaves a stream open when the turn is cancelled.

What a case looks like on the wire belongs to the provider, so the fixtures
come from ``provider_stream`` and the request is read back through
``asked_turns`` - the only places either API's spelling is written. The claim
under each name is the same for every adapter, which is the point: an
adapter that passes its own cases but not these has been described, not
qualified.

One case is single-API and says why: a stream that carries no counts at all
is a shape only Chat Completions produces, so the suite cannot ask it of an
API that reports counts on every stream.
"""

import pytest

from src.openship.providers.contracts import ChatMessage, ModelRequest

from .conftest import (
    adapter_case,
    asked_turns,
    control,
    conversation,
    drain,
    error_fixture,
    provider_stream,
)

CONVERSATION = (
    ("system", "Be brief."),
    ("user", "What failed?"),
    ("assistant", "A deploy."),
    ("user", "Which check first?"),
)

#: Every adapter this suite must pass. Adding one here is what makes the
#: suite shared rather than a second file that happens to look alike.
ADAPTERS = ("openai", "anthropic")


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_system_and_history_reach_the_provider_in_order(adapter):
    """The provider is asked for the conversation exactly as it is stored."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=["ok"]))
    request = conversation(*CONVERSATION)

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure is None
    assert asked_turns(adapter, case.transport.seen_kwargs) == [
        ("system", "Be brief."),
        ("user", "What failed?"),
        ("assistant", "A deploy."),
        ("user", "Which check first?"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_unicode_deltas_keep_their_order(adapter):
    """Multi-byte text is reassembled exactly, in stream order."""
    case = adapter_case(
        adapter=adapter,
        chunks=provider_stream(adapter, text=["é", "日本語", " — done"]),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    text = "".join(event.text for event in events if event.type == "text_delta")
    assert text == "é日本語 — done"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_stream_without_a_finish_marker_is_not_an_answer(adapter):
    """A provider that stops mid-stream fails; it never completes silently."""
    case = adapter_case(
        adapter=adapter, chunks=provider_stream(adapter, text=["half"], finish=None)
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert "".join(e.text for e in events if e.type == "text_delta") == "half"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_an_empty_stream_is_not_an_answer(adapter):
    """Zero output is a failure, and the transport is still closed."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=[], finish=None))

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert [e for e in events if e.type in ("text_delta", "stream_finished")] == []
    assert failure.code == "invalid_response"
    assert case.transport.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_an_empty_answer_is_not_an_answer(adapter):
    """A clean stop that produced nothing is still not publishable."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=[""]))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ("openai",))
async def test_usage_the_provider_omits_stays_unknown(adapter):
    """No counts means no claims, not zeros."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=["hi"]))

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert [e for e in events if e.type == "usage_update"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_cumulative_usage_is_replaced_not_added(adapter):
    """Provider snapshots are cumulative; summing them double counts."""
    case = adapter_case(
        adapter=adapter,
        chunks=provider_stream(adapter, text=["hi"], usage=[(10, 5), (10, 8)]),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert events[-1].type == "stream_finished"
    updates = [e.usage for e in events if e.type == "usage_update"]
    assert [(u.input_tokens, u.output_tokens) for u in updates][-1] == (10, 8)
    assert updates[-1].source == "provider"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_provider_failure_becomes_safe_actionable_copy(adapter):
    """The provider's own error text never reaches the user."""
    case = adapter_case(adapter=adapter, chunks=[], raises=error_fixture(adapter))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is not None
    assert failure.retryable is True
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_stopped_turn_closes_the_stream(adapter):
    """Cancellation stops further output and closes the local transport."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=["a", "b"]))

    _events, failure = await drain(
        case.adapter,
        conversation(("user", "hi")),
        case.profile,
        control(stopped=True),
    )

    assert failure.code == "cancelled"
    assert case.transport.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_cancelled_turn_closes_the_stream(adapter):
    """Stopping the consumer mid-turn runs the adapter's cleanup."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=["a", "b"]))
    stream = case.adapter.stream(conversation(("user", "hi")), case.profile, control())

    async for _ in stream:
        break

    await stream.aclose()

    assert case.transport.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_an_unsupported_capability_never_reaches_the_network(adapter):
    """A request the profile cannot serve is refused before dispatch."""
    case = adapter_case(adapter=adapter, chunks=provider_stream(adapter, text=["ok"]))
    request = ModelRequest(
        messages=(ChatMessage(role="user", content="use a tool"),),
        max_output_tokens=128,
        required_capabilities=frozenset({"streaming", "tool_calls"}),
    )

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure.code == "unsupported_capability"
    assert case.transport.requests == 0
