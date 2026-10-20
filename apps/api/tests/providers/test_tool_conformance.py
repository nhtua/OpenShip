"""The first tool-capable provider preserves the registered tool contract."""

import json

import pytest
from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    RunLimits,
    RunModelConfig,
    ToolDefinition,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.runner import run_model_turn

from .conftest import (
    FakeRecorder,
    adapter_case,
    control,
    drain,
    error_fixture,
    qualified_profile,
    sdk_chunk,
    sdk_tool_chunk,
    serve_requests,
)


def _definition(name="brave_web_search"):
    manifest = next(
        manifest for manifest in list_capability_manifests() if manifest.id == name
    )
    return ToolDefinition(
        name=name,
        description="Search the web with Brave.",
        parameters=manifest.input_schema,
    )


def _request(*, definition=None):
    return ModelRequest(
        messages=(ChatMessage(role="user", content="Find the docs for café. "),),
        tools=(definition or _definition(),),
        max_output_tokens=128,
    )


def _profile(**overrides):
    capabilities = {
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
    return qualified_profile(capabilities=capabilities, **overrides)


@pytest.mark.asyncio
async def test_openai_preserves_exact_names_ids_and_unicode_arguments():
    """Strict wire adaptation does not rename IDs or lose optional defaults."""
    definition = _definition()
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-",
                        "name": "brave_",
                        "arguments": '{"query":"café",',
                    }
                ]
            ),
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "1",
                        "name": "web_search",
                        "arguments": '"max_results":null}',
                    }
                ],
                finish_reason="tool_calls",
            ),
        ],
    )

    events, failure = await drain(
        case.adapter, _request(definition=definition), case.profile, control()
    )

    assert failure is None
    fragments = [event.tool_call_delta for event in events if event.type == "tool_call_delta"]
    assert "".join(part.name_delta for part in fragments) == "brave_web_search"
    assert "".join(part.id_delta for part in fragments) == "provider-call-1"
    assert "".join(part.arguments_delta for part in fragments) == '{"query":"café","max_results":null}'
    ready = [event.tool_call for event in events if event.type == "tool_call_ready"]
    assert len(ready) == 1
    assert ready[0].name == "brave_web_search"
    assert ready[0].provider_call_id == "provider-call-1"
    assert ready[0].arguments == {"query": "café", "max_results": 5}
    assert events[-1].type == "stream_finished"
    assert events[-1].reason == "tool_calls"

    sent_tool = case.transport.seen_kwargs["tools"][0]
    assert sent_tool["function"]["name"] == "brave_web_search"
    assert sent_tool["function"]["strict"] is True
    wire_schema = sent_tool["function"]["parameters"]
    assert wire_schema["required"] == ["query", "max_results"]
    assert wire_schema["additionalProperties"] is False
    assert wire_schema["properties"]["max_results"] == {
        "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}]
    }
    assert "default" not in json.dumps(wire_schema)
    assert definition.parameters["required"] == ["query"]
    assert definition.parameters["properties"]["max_results"]["default"] == 5
    assert case.transport.seen_kwargs["parallel_tool_calls"] is False


@pytest.mark.asyncio
async def test_tool_call_can_finish_without_text():
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-1",
                        "name": "brave_web_search",
                        "arguments": '{"query":"café"}',
                    }
                ],
                finish_reason="tool_calls",
            )
        ],
    )

    events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure is None
    assert not [event for event in events if event.type == "text_delta"]
    assert [event.type for event in events] == [
        "tool_call_delta",
        "tool_call_ready",
        "stream_finished",
    ]


@pytest.mark.asyncio
async def test_provider_returned_name_must_match_exact_request_allowlist():
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-1",
                        "name": "tavily_web_search",
                        "arguments": '{"query":"café"}',
                    }
                ],
                finish_reason="tool_calls",
            )
        ],
    )

    _events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure.code == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fragments",
    [
        [
            {"index": 0, "id": "same", "name": "brave_web_search", "arguments": '{"query":"a"}'},
            {"index": 1, "id": "same", "name": "brave_web_search", "arguments": '{"query":"b"}'},
        ],
        [
            {
                "index": 0,
                "id": "provider-call-1",
                "name": "brave_web_search",
                "arguments": " " * 65537,
            }
        ],
        [
            {"index": 0, "id": "call-1", "name": "brave_web_search", "arguments": '{"query":"a"}'},
            {"index": 1, "id": "call-2", "name": "brave_web_search", "arguments": '{"query":"b"}'},
        ],
    ],
    ids=("duplicate-provider-id", "oversize-arguments", "parallel-calls"),
)
async def test_duplicate_oversize_and_parallel_calls_are_rejected(fragments):
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[sdk_tool_chunk(fragments=fragments, finish_reason="tool_calls")],
    )

    _events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure.code == "invalid_response"


@pytest.mark.asyncio
async def test_tool_calls_require_finish_marker():
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-1",
                        "name": "brave_web_search",
                        "arguments": '{"query":"café"}',
                    }
                ]
            )
        ],
    )

    _events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure.code == "invalid_response"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_tool_call_from_unqualified_model_is_rejected_and_stream_closed():
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-1",
                        "name": "brave_web_search",
                        "arguments": '{"query":"café"}',
                    }
                ],
                finish_reason="tool_calls",
                model="unqualified-model",
            )
        ],
    )

    events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure.code == "unexpected_model"
    assert events[-1].actual_model == "unqualified-model"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_cancelling_after_a_tool_fragment_closes_the_stream():
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": "provider-call-1",
                        "name": "brave_web_search",
                        "arguments": '{"query":',
                    }
                ]
            )
        ],
    )
    stream = case.adapter.stream(_request(), case.profile, control())

    event = await anext(stream)
    await stream.aclose()

    assert event.type == "tool_call_delta"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_tool_history_keeps_provider_ids_on_the_wire():
    prior_call = {
        "provider_call_id": "provider-id-with-underscores",
        "name": "brave_web_search",
        "arguments": {"query": "café", "max_results": 5},
    }
    request = ModelRequest(
        messages=(
            ChatMessage(role="user", content="Search for café."),
            ChatMessage(role="assistant", content="", tool_calls=(prior_call,)),
            ChatMessage(
                role="tool",
                name="brave_web_search",
                tool_call_id="provider-id-with-underscores",
                content='{"items":[]}',
            ),
        ),
        tools=(_definition(),),
        max_output_tokens=128,
    )
    case = adapter_case(
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
        chunks=[sdk_chunk(content="Found it.", finish_reason="stop")],
    )
    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure is None
    messages = case.transport.seen_kwargs["messages"]
    assert messages[1]["tool_calls"][0]["id"] == "provider-id-with-underscores"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "brave_web_search"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == (
        '{"query":"café","max_results":5}'
    )
    assert messages[2]["tool_call_id"] == "provider-id-with-underscores"
    assert messages[2]["name"] == "brave_web_search"


@pytest.mark.asyncio
async def test_tool_fragment_failure_does_not_retry_or_fall_back(monkeypatch):
    """A partial call cannot be replayed as though nothing reached the model."""
    primary = _profile(
        id="primary-tool-profile",
        model="primary-model",
        allowed_fallback_profile_ids=["fallback-tool-profile"],
    )
    fallback = _profile(id="fallback-tool-profile", model="fallback-model")
    transport = serve_requests(
        monkeypatch,
        [
            {
                "chunks": [
                    sdk_tool_chunk(
                        fragments=[
                            {
                                "index": 0,
                                "id": "provider-call-1",
                                "name": "brave_web_search",
                                "arguments": '{"query":',
                            }
                        ]
                    ),
                    error_fixture("openai", "unreachable"),
                ]
            }
        ],
    )
    config = RunModelConfig(
        schema_version=1,
        primary=primary,
        fallbacks=(fallback,),
        limits=RunLimits(max_attempts=3),
        binding_origin="submission",
    )

    async def no_wait(_delay):
        return None

    with pytest.raises(ProviderError) as refused:
        await run_model_turn(
            _request(), config, control(), FakeRecorder(), sleeper=no_wait
        )

    assert refused.value.code == "provider_unavailable"
    assert transport.requests == 1
    assert [request["model"] for request in transport.seen] == ["primary-model"]


@pytest.mark.asyncio
async def test_runner_returns_ready_tool_calls_without_marking_them_as_final_text(monkeypatch):
    transport = serve_requests(
        monkeypatch,
        [
            {
                "chunks": [
                    sdk_tool_chunk(
                        fragments=[
                            {
                                "index": 0,
                                "id": "provider-call-1",
                                "name": "brave_web_search",
                                "arguments": '{"query":"café","max_results":null}',
                            }
                        ],
                        finish_reason="tool_calls",
                        model="primary-model",
                    )
                ]
            }
        ],
    )
    config = RunModelConfig(
        schema_version=1,
        primary=_profile(id="primary-tool-profile", model="primary-model"),
        limits=RunLimits(max_attempts=1),
        binding_origin="submission",
    )

    result = await run_model_turn(
        _request(), config, control(), FakeRecorder()
    )

    assert transport.requests == 1
    assert result.text == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "brave_web_search"
    assert result.tool_calls[0].provider_call_id == "provider-call-1"
    assert result.tool_calls[0].arguments == {"query": "café", "max_results": 5}
    assert result.completed is False


def test_duplicate_tool_definitions_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        ModelRequest(
            messages=(ChatMessage(role="user", content="search"),),
            tools=(_definition(), _definition()),
            max_output_tokens=128,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ("anthropic", "google", "openai_compatible"))
async def test_unqualified_adapters_reject_tool_requests_before_dispatch(adapter):
    case = adapter_case(
        adapter=adapter,
        chunks=[],
        profile_kwargs={"capabilities": _profile().capabilities.model_dump()},
    )

    _events, failure = await drain(case.adapter, _request(), case.profile, control())

    assert failure.code == "unsupported_capability"
    assert case.transport.requests == 0
