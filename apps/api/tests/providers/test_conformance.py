"""The conformance suite every adapter must pass, written once.

Each case drives a real adapter against a synthetic transport at the SDK's
outer edge. The break these catch: an adapter that loses conversation order,
invents usage, reports a partial answer as complete, leaks the provider's own
error text, or leaves a stream open when the turn is cancelled.
"""

import pytest

from src.openship.providers.contracts import ChatMessage, ModelRequest

from .conftest import (
    adapter_case,
    control,
    conversation,
    drain,
    error_fixture,
    sdk_chunk,
)

CONVERSATION = (
    ("system", "Be brief."),
    ("user", "What failed?"),
    ("assistant", "A deploy."),
    ("user", "Which check first?"),
)


@pytest.mark.asyncio
async def test_system_and_history_reach_the_provider_in_order():
    """The provider is asked for the conversation exactly as it is stored."""
    case = adapter_case(chunks=[sdk_chunk(content="ok", finish_reason="stop")])
    request = conversation(*CONVERSATION)

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure is None
    assert case.transport.seen_kwargs["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "What failed?"},
        {"role": "assistant", "content": "A deploy."},
        {"role": "user", "content": "Which check first?"},
    ]


@pytest.mark.asyncio
async def test_unicode_deltas_keep_their_order():
    """Multi-byte text is reassembled exactly, in stream order."""
    case = adapter_case(
        chunks=[
            sdk_chunk(content="é"),
            sdk_chunk(content="日本語"),
            sdk_chunk(content=" — done", finish_reason="stop"),
        ]
    )

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    text = "".join(event.text for event in events if event.type == "text_delta")
    assert text == "é日本語 — done"


@pytest.mark.asyncio
async def test_a_stream_without_a_finish_marker_is_not_an_answer():
    """A provider that stops mid-stream fails; it never completes silently."""
    case = adapter_case(chunks=[sdk_chunk(content="half")])

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure.code == "invalid_response"
    assert "".join(e.text for e in events if e.type == "text_delta") == "half"


@pytest.mark.asyncio
async def test_an_empty_stream_is_not_an_answer():
    """Zero output is a failure, and the transport is still closed."""
    case = adapter_case(chunks=[])

    _events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert _events == []
    assert failure.code == "invalid_response"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_an_answer():
    """A clean stop that produced nothing is still not publishable."""
    case = adapter_case(chunks=[sdk_chunk(content="", finish_reason="stop")])

    _events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure.code == "invalid_response"


@pytest.mark.asyncio
async def test_usage_the_provider_omits_stays_unknown():
    """No counts means no claims, not zeros."""
    case = adapter_case(chunks=[sdk_chunk(content="hi", finish_reason="stop")])

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    assert [e for e in events if e.type == "usage_update"] == []


@pytest.mark.asyncio
async def test_cumulative_usage_is_replaced_not_added():
    """Provider snapshots are cumulative; summing them double counts."""
    case = adapter_case(
        chunks=[
            sdk_chunk(content="hi"),
            sdk_chunk(usage=(10, 5, 15)),
            sdk_chunk(content=" there", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(10, 8, 18)),
        ]
    )

    events, failure = await drain(case.adapter, conversation(("user", "hi")), case.profile, control())

    assert failure is None
    assert events[-1].type == "stream_finished"
    updates = [e.usage for e in events if e.type == "usage_update"]
    assert [(u.input_tokens, u.output_tokens, u.total_tokens) for u in updates] == [
        (10, 5, 15),
        (10, 8, 18),
    ]
    assert updates[-1].source == "provider"


@pytest.mark.asyncio
async def test_a_provider_failure_becomes_safe_actionable_copy():
    """The provider's own error text never reaches the user."""
    case = adapter_case(chunks=[], raises=error_fixture("openai"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is not None
    assert failure.retryable is True
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_a_stopped_turn_closes_the_stream():
    """Cancellation stops further output and closes the local transport."""
    case = adapter_case(
        chunks=[sdk_chunk(content="a"), sdk_chunk(content="b", finish_reason="stop")]
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control(stopped=True)
    )

    assert failure.code == "cancelled"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_a_cancelled_turn_closes_the_stream():
    """Stopping the consumer mid-turn runs the adapter's cleanup."""
    case = adapter_case(
        chunks=[sdk_chunk(content="a"), sdk_chunk(content="b", finish_reason="stop")]
    )
    stream = case.adapter.stream(conversation(("user", "hi")), case.profile, control())

    async for _ in stream:
        break

    await stream.aclose()

    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_an_unsupported_capability_never_reaches_the_network():
    """A request the profile cannot serve is refused before dispatch."""
    case = adapter_case(chunks=[sdk_chunk(content="ok", finish_reason="stop")])
    request = ModelRequest(
        messages=(ChatMessage(role="user", content="use a tool"),),
        max_output_tokens=128,
        required_capabilities=frozenset({"streaming", "tool_calls"}),
    )

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure.code == "unsupported_capability"
    assert case.transport.requests == 0
