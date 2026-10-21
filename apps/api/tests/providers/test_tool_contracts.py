"""Provider-neutral tool contracts preserve exact IDs and complete payloads."""

import pytest
from pydantic import ValidationError

from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    ProviderEvent,
    ProviderTurnResult,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
    pin_profile,
    validate_request,
)
from tests.providers.conftest import qualified_profile


def definition(name="brave_web_search"):
    return ToolDefinition(
        name=name,
        description="Search the web with Brave.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def call(**overrides):
    values = {
        "provider_call_id": "provider-call-1",
        "name": "brave_web_search",
        "arguments": {"query": "terraform modules"},
    }
    values.update(overrides)
    return ToolCall(**values)


def test_message_round_trips_complete_tool_calls_and_tool_results():
    user = ChatMessage(role="user", content="Find module docs.")
    assistant = ChatMessage(role="assistant", content="", tool_calls=(call(),))
    tool = ChatMessage(
        role="tool",
        name="brave_web_search",
        tool_call_id="provider-call-1",
        content='{"items": []}',
    )

    assert ChatMessage.model_validate_json(user.model_dump_json()) == user
    assert ChatMessage.model_validate_json(assistant.model_dump_json()) == assistant
    assert ChatMessage.model_validate_json(tool.model_dump_json()) == tool
    assert assistant.tool_calls[0].provider_call_id == tool.tool_call_id


def test_role_validation_requires_a_complete_matching_shape():
    with pytest.raises(ValidationError):
        ChatMessage(role="tool", content="missing call ID")
    with pytest.raises(ValidationError):
        ChatMessage(role="assistant", content="", tool_calls=(
            call(provider_call_id="same"),
            call(provider_call_id="same", name="tavily_web_search"),
        ))
    with pytest.raises(ValidationError):
        call(arguments='{"query":')
    with pytest.raises(ValidationError):
        call(arguments={"query": object()})
    with pytest.raises(ValidationError):
        call(arguments={"query": float("nan")})


def test_partial_fragments_are_distinct_from_ready_calls():
    fragment = ToolCallDelta(
        index=0,
        id_delta="call_",
        name_delta="brave_",
        arguments_delta='{"query":',
    )
    delta_event = ProviderEvent(type="tool_call_delta", tool_call_delta=fragment)
    ready_event = ProviderEvent(type="tool_call_ready", tool_call=call())

    assert delta_event.tool_call_delta.arguments_delta == '{"query":'
    assert ready_event.tool_call == call()
    with pytest.raises(ValidationError):
        ProviderEvent(type="tool_call_ready", tool_call_delta=fragment)


def test_model_request_requires_exact_tool_definitions_and_declared_capability():
    request = ModelRequest(
        messages=(ChatMessage(role="user", content="Find module docs."),),
        tools=(definition(),),
        max_output_tokens=32,
    )
    tool_profile = pin_profile(
        qualified_profile(
            capabilities={
                "streaming": True,
                "usage_reporting": True,
                "context_tokens": 32768,
                "max_output_tokens": 4096,
                "token_counting": False,
                "tool_calls": True,
                "structured_output": False,
                "attachment_types": [],
                "parallel_tool_calls": False,
            }
        )
    )
    validate_request(request, tool_profile)

    text_profile = pin_profile(qualified_profile())
    with pytest.raises(Exception, match="tool_calls"):
        validate_request(request, text_profile)
    with pytest.raises(ValidationError):
        ModelRequest(
            messages=(ChatMessage(role="user", content="hello"),),
            tools=(definition(), definition()),
            max_output_tokens=32,
        )


def test_model_request_rejects_calls_outside_its_exact_tool_allowlist():
    with pytest.raises(ValidationError, match="not present in the request allowlist"):
        ModelRequest(
            messages=(
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=(call(name="brave_web_search_v2"),),
                ),
            ),
            tools=(definition(),),
            max_output_tokens=32,
        )


def test_completed_history_remains_valid_when_a_tool_is_disabled_mid_run():
    request = ModelRequest(
        messages=(
            ChatMessage(role="user", content="Search once."),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(call(name="brave_web_search"),),
            ),
            ChatMessage(
                role="tool",
                name="brave_web_search",
                tool_call_id="provider-call-1",
                content='{"status":"succeeded"}',
            ),
            ChatMessage(role="user", content="Now explain."),
        ),
        tools=(),
        max_output_tokens=32,
    )

    assert request.tools == ()


def test_canonical_tool_schema_keeps_optional_properties_for_provider_adaptation():
    optional = ToolDefinition(
        name="brave_web_search",
        description="Search the web with Brave.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    assert optional.parameters["required"] == ["query"]
    assert "max_results" in optional.parameters["properties"]


def test_empty_tool_turn_is_not_a_completed_final_answer():
    result = ProviderTurnResult(
        text="",
        tool_calls=(call(),),
        selected_profile_id="openai-primary",
        finish_reason="tool_calls",
    )

    assert result.tool_calls[0].name == "brave_web_search"
    assert result.finish_reason == "tool_calls"
    assert not result.completed
