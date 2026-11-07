"""Responses adapter translates complete bounded model turns."""

from types import SimpleNamespace

import pytest

from .conftest import StreamTransport, control, conversation, qualified_profile


def response_profile():
    return qualified_profile(
        api_version="responses",
        adapter_options={"adapter": "openai", "output_limit_parameter": "max_output_tokens",
                         "reasoning_effort": "low"},
    )


@pytest.mark.asyncio
async def test_response_text_stream_is_mapped_and_closes():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "phase": "final_answer", "content": [{"type": "output_text", "text": "hello", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([
        SimpleNamespace(type="response.output_text.delta", delta="hello"),
        SimpleNamespace(type="response.completed", response=response),
    ])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    adapter = OpenAIResponsesAdapter(client_factory=lambda _profile: client)
    events = [event async for event in adapter.stream(conversation(("user", "hi")), response_profile(), control())]
    assert [event.type for event in events] == ["activity", "text_delta", "continuation", "stream_finished"]
    assert events[1].text == "hello"
    assert events[-1].reason == "stop"
    assert transport.seen_kwargs["store"] is False
    assert transport.seen_kwargs["reasoning"] == {"effort": "low"}
    assert transport.closed


@pytest.mark.asyncio
async def test_response_tool_call_is_not_executed_before_terminal_completion():
    from src.openship.providers.contracts import ToolDefinition
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    definition = ToolDefinition(name="lookup", description="Look up a value", parameters={
        "type": "object", "properties": {"key": {"type": "string"}},
        "required": ["key"], "additionalProperties": False,
    })
    profile = response_profile().model_copy(update={"capabilities": response_profile().capabilities.model_copy(update={"tool_calls": True})})
    request = conversation(("user", "look up x")).model_copy(update={"tools": (definition,)})
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup",
            "arguments": '{"key":"x"}', "status": "completed"}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    adapter = OpenAIResponsesAdapter(client_factory=lambda _profile: client)
    events = [event async for event in adapter.stream(request, profile, control())]
    call = next(event.tool_call for event in events if event.type == "tool_call_ready")
    assert call.provider_call_id == "call_1"
    assert call.arguments == {"key": "x"}
    assert events[-1].reason == "tool_calls"
    assert transport.seen_kwargs["tools"][0]["name"] == "lookup"


@pytest.mark.asyncio
async def test_response_replays_private_items_and_tool_result_statelessly():
    from src.openship.providers.contracts import (
        ChatMessage,
        ModelRequest,
        ResponsesContinuation,
        ToolCall,
    )
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    profile = response_profile().model_copy(update={
        "capabilities": response_profile().capabilities.model_copy(update={"tool_calls": True})
    })
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup",
            "arguments": '{"key":"x"}', "status": "completed"}
    state = ResponsesContinuation(version=1, protocol="openai.responses",
                                  profile_revision=profile.profile_revision, model=profile.model,
                                  destination="fixture.provider.test/v1", items=[item])
    request = ModelRequest(messages=(
        ChatMessage(role="user", content="lookup"),
        ChatMessage(role="assistant", content="", tool_calls=(ToolCall(
            name="lookup", provider_call_id="call_1", arguments={"key": "x"}),), continuation=state),
        ChatMessage(role="tool", name="lookup", tool_call_id="call_1", content="found"),
    ), max_output_tokens=128)
    final = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
             "phase": "final_answer", "content": [{"type": "output_text", "text": "found", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[final], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(request, profile, control())]
    assert events[-1].reason == "stop"
    assert transport.seen_kwargs["input"] == [
        {"role": "user", "content": "lookup"}, item,
        {"type": "function_call_output", "call_id": "call_1", "output": "found"},
    ]


@pytest.mark.asyncio
async def test_response_does_not_send_continuation_to_other_profile():
    from src.openship.providers.contracts import (
        ChatMessage,
        ModelRequest,
        ResponsesContinuation,
    )
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    profile = response_profile()
    state = ResponsesContinuation(version=1, protocol="openai.responses",
                                  profile_revision="other", model=profile.model,
                                  destination="fixture.provider.test/v1", items=[])
    request = ModelRequest(messages=(ChatMessage(role="assistant", content="", continuation=state),),
                           max_output_tokens=128)
    transport = StreamTransport([])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError):
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(request, profile, control())]
    assert transport.requests == 0


@pytest.mark.asyncio
async def test_response_commentary_is_not_emitted_as_final_text():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    items = [
        {"type": "message", "id": "m1", "role": "assistant", "phase": "commentary",
         "status": "completed", "content": [{"type": "output_text", "text": "thinking", "annotations": []}]},
        {"type": "message", "id": "m2", "role": "assistant", "phase": "final_answer",
         "status": "completed", "content": [{"type": "output_text", "text": "answer", "annotations": []}]},
    ]
    response = SimpleNamespace(status="completed", output=items, model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.output_text.delta", delta="thinking"),
                                 SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        conversation(("user", "hi")), response_profile(), control())]
    assert "".join(event.text for event in events if event.type == "text_delta") == "answer"


@pytest.mark.asyncio
async def test_response_rejects_completed_empty_answer():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    response = SimpleNamespace(status="completed", output=[], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError):
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control())]


@pytest.mark.asyncio
async def test_function_fragment_marks_attempt_as_started_before_disconnect():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    transport = StreamTransport([SimpleNamespace(type="response.function_call_arguments.delta", delta="{"),
                                 RuntimeError("disconnected")])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = []
    with pytest.raises(RuntimeError):
        async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control()):
            events.append(event)
    assert [event.type for event in events] == ["activity"]
    assert transport.closed


@pytest.mark.asyncio
async def test_response_reports_usage_without_double_counting_reasoning():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30,
                            input_tokens_details=SimpleNamespace(cached_tokens=3),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=12))
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=usage)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        conversation(("user", "hi")), response_profile(), control())]
    reported = next(event.usage for event in events if event.type == "usage_update")
    assert (reported.input_tokens, reported.output_tokens, reported.total_tokens) == (10, 20, 30)
    assert (reported.cached_input_tokens, reported.reasoning_tokens) == (3, 12)


@pytest.mark.asyncio
async def test_response_maps_sdk_rate_limit_without_exposing_body():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    from .conftest import error_fixture

    transport = StreamTransport([], raises=error_fixture("openai", "rate_limited"))
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError) as failure:
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control())]
    assert failure.value.code == "rate_limited"
    assert "secret-body" not in str(failure.value)


@pytest.mark.asyncio
async def test_response_model_mismatch_rejected_before_text_or_continuation():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "untrusted", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[item], model="unexpected", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = []
    with pytest.raises(ProviderError) as failure:
        async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control()):
            events.append(event)
    assert failure.value.code == "unexpected_model"
    assert not any(event.type in {"text_delta", "continuation", "tool_call_ready"} for event in events)


@pytest.mark.asyncio
async def test_response_rejects_excessive_function_argument_fragments():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([
        SimpleNamespace(type="response.function_call_arguments.delta", delta="a" * 65537),
        SimpleNamespace(type="response.completed", response=response),
    ])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError) as failure:
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control())]
    assert failure.value.code == "invalid_response"
    assert transport.closed


@pytest.mark.asyncio
async def test_response_rejects_argument_deltas_disagreeing_with_terminal_call():
    from src.openship.providers.contracts import ToolDefinition
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    definition = ToolDefinition(name="lookup", description="Look up a value", parameters={
        "type": "object", "properties": {"key": {"type": "string"}},
        "required": ["key"], "additionalProperties": False,
    })
    base = response_profile()
    profile = base.model_copy(update={"capabilities": base.capabilities.model_copy(update={"tool_calls": True})})
    request = conversation(("user", "lookup")).model_copy(update={"tools": (definition,)})
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup",
            "arguments": '{"key":"different"}', "status": "completed"}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([
        SimpleNamespace(type="response.function_call_arguments.delta", item_id="fc_1", delta='{"key":"x"}'),
        SimpleNamespace(type="response.completed", response=response),
    ])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = []
    with pytest.raises(ProviderError):
        async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(request, profile, control()):
            events.append(event)
    assert not any(event.type == "tool_call_ready" for event in events)


@pytest.mark.asyncio
async def test_response_output_limit_is_not_invalid_response():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    response = SimpleNamespace(status="incomplete", model="fixture-model", output=[], usage=None,
                               incomplete_details=SimpleNamespace(reason="max_output_tokens"))
    transport = StreamTransport([SimpleNamespace(type="response.incomplete", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        conversation(("user", "hi")), response_profile(), control())]
    assert events[-1].reason == "length"


@pytest.mark.asyncio
async def test_response_rejects_events_after_terminal_marker():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response),
                                 SimpleNamespace(type="response.output_text.delta", delta="extra")])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError):
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control())]


@pytest.mark.asyncio
async def test_response_refusal_is_not_published_as_text():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "refusal", "refusal": "private refusal text"}]}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        conversation(("user", "hi")), response_profile(), control())]
    assert events[-1].reason == "refusal"
    assert not any(event.type in {"text_delta", "continuation", "tool_call_ready"} for event in events)


@pytest.mark.asyncio
async def test_added_function_item_marks_attempt_as_started_before_disconnect():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", name="lookup")
    transport = StreamTransport([SimpleNamespace(type="response.output_item.added", item=item),
                                 RuntimeError("disconnected")])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = []
    with pytest.raises(RuntimeError):
        async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control()):
            events.append(event)
    assert [event.type for event in events] == ["activity"]


@pytest.mark.asyncio
async def test_failed_event_cannot_claim_completed_status():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.failed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError):
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control())]


@pytest.mark.asyncio
async def test_response_keeps_item_and_call_id_distinct():
    from src.openship.providers.contracts import ResponsesContinuation, ToolDefinition
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    definition = ToolDefinition(name="lookup", description="Look up a value", parameters={
        "type": "object", "properties": {"key": {"type": "string"}},
        "required": ["key"], "additionalProperties": False,
    })
    base = response_profile()
    profile = base.model_copy(update={"capabilities": base.capabilities.model_copy(update={"tool_calls": True})})
    request = conversation(("user", "lookup")).model_copy(update={"tools": (definition,)})
    item = {"type": "function_call", "id": "item_7", "call_id": "call_9", "name": "lookup",
            "arguments": '{"key":"x"}', "status": "completed"}
    response = SimpleNamespace(status="completed", output=[item], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        request, profile, control())]
    call = next(event.tool_call for event in events if event.type == "tool_call_ready")
    assert call.provider_call_id == "call_9"
    state = next(event.continuation for event in events if event.type == "continuation")
    assert isinstance(state, ResponsesContinuation)
    assert state.items == (item,)


@pytest.mark.asyncio
async def test_response_sends_nonstrict_wire_schema_only_for_dynamic_maps():
    from src.openship.providers.contracts import ToolDefinition
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    fixed = ToolDefinition(name="lookup", description="Look up a value", parameters={
        "type": "object", "properties": {"key": {"type": "string"}},
        "required": ["key"], "additionalProperties": False,
    })
    dynamic = ToolDefinition(name="python_execute", description="Run python", parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "output_media": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["source"],
        "additionalProperties": False,
    })
    base = response_profile()
    profile = base.model_copy(update={"capabilities": base.capabilities.model_copy(update={"tool_calls": True})})
    request = conversation(("user", "run")).model_copy(update={"tools": (fixed, dynamic)})
    response = SimpleNamespace(status="completed", output=[], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    with pytest.raises(ProviderError):
        _ = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            request, profile, control())]
    tools = transport.seen_kwargs["tools"]
    assert [tool["name"] for tool in tools] == ["lookup", "python_execute"]
    assert tools[0]["strict"] is True
    assert tools[1]["strict"] is False
    assert tools[1]["parameters"]["properties"]["output_media"] == {
        "type": "object", "additionalProperties": {"type": "string"}}


@pytest.mark.asyncio
async def test_response_keeps_ordered_reasoning_out_of_published_text():
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-token", "status": "completed"}
    final = {"type": "message", "id": "m2", "role": "assistant", "phase": "final_answer",
             "status": "completed", "content": [{"type": "output_text", "text": "answer", "annotations": []}]}
    response = SimpleNamespace(status="completed", output=[reasoning, final], model="fixture-model", usage=None)
    transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    events = [event async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
        conversation(("user", "hi")), response_profile(), control())]
    state = next(event.continuation for event in events if event.type == "continuation")
    assert state.items == (reasoning, final)
    assert "".join(event.text for event in events if event.type == "text_delta") == "answer"
    assert "opaque-token" not in repr(events)


@pytest.mark.asyncio
async def test_response_rejects_duplicate_or_unknown_items():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    async def expect_rejection(output):
        response = SimpleNamespace(status="completed", output=output, model="fixture-model", usage=None)
        transport = StreamTransport([SimpleNamespace(type="response.completed", response=response)])
        client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
        with pytest.raises(ProviderError) as failure:
            _ = [event async for event in OpenAIResponsesAdapter(
                client_factory=lambda _profile, _client=client: _client).stream(
                conversation(("user", "hi")), response_profile(), control())]
        assert failure.value.code == "invalid_response"
        assert transport.closed

    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup",
            "arguments": '{"key":"x"}', "status": "completed"}
    await expect_rejection([call, dict(call)])
    await expect_rejection([call, {"type": "tool_search_call", "id": "ts_1"}])


@pytest.mark.asyncio
async def test_response_cancellation_closes_stream_and_emits_no_call():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    transport = StreamTransport([
        SimpleNamespace(type="response.output_item.added", item=SimpleNamespace(
            type="function_call", id="fc_1", call_id="call_1", name="lookup")),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(
            status="completed", output=[{"type": "message", "id": "m1", "role": "assistant",
                                         "phase": "final_answer", "status": "completed",
                                         "content": [{"type": "output_text", "text": "late",
                                                      "annotations": []}]}],
            model="fixture-model", usage=None)),
    ])
    client = SimpleNamespace(responses=SimpleNamespace(create=transport.create))
    emitted = []
    stopped = {"on": False}
    with pytest.raises(ProviderError) as failure:
        async for event in OpenAIResponsesAdapter(client_factory=lambda _: client).stream(
            conversation(("user", "hi")), response_profile(), control(stop=lambda: stopped["on"])):
            emitted.append(event)
            stopped["on"] = True
    assert [event.type for event in emitted] == ["activity"]
    assert failure.value.code == "cancelled"
    assert transport.closed


@pytest.mark.asyncio
async def test_response_count_input_tokens_fails_closed():
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter

    adapter = OpenAIResponsesAdapter(client_factory=lambda _profile: None)
    with pytest.raises(ProviderError) as failure:
        await adapter.count_input_tokens(conversation(("user", "hi")), response_profile(), control())
    assert failure.value.code == "unsupported_capability"
