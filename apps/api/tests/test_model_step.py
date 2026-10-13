"""Tests for the chat graph model step: the run's own snapshot, bounded output, and safe failures.

The breaks these catch: a step that follows today's settings instead of the
configuration the run carries, a partial answer published as if it were
complete, and a provider failure that reaches a user as a stack trace.
"""

import os

import pytest

# Set strict msgpack before importing graph modules
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

from src.openship.providers.contracts import RunLimits
from src.openship.providers.errors import ProviderError
from src.openship.runs.graph import (
    ChatState,
    ModelResult,
    build_chat_graph,
    execute_model_turn,
    model_step,
)

from .conftest import FIXTURE_MODEL
from .providers.conftest import (
    CREDENTIAL_REF,
    error_fixture,
    invocation,
    run_state,
    sdk_chunk,
    serve,
)


def test_model_step_returns_response_with_mocked_provider(monkeypatch):
    """Model step returns a complete assistant response when provider succeeds."""
    serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="Hi there!"),
            sdk_chunk(content=" How can I help?"),
            sdk_chunk(finish_reason="stop"),
            sdk_chunk(choices=False, usage=(11, 10, 21)),
        ],
    )

    result = model_step(run_state(("system", "You are helpful."), ("user", "Hello!")), invocation())

    assert result["assistant_response"] == "Hi there! How can I help?"
    assert result["finish_reason"] == "stop"


def test_model_step_empty_stream_refuses_an_answer(monkeypatch):
    """Model step refuses an answer when the stream produced nothing."""
    serve(monkeypatch, chunks=[])

    with pytest.raises(ProviderError) as refused:
        model_step(run_state(("user", "Hello!")), invocation())

    assert refused.value.code == "invalid_response"


def test_model_step_provider_error_captured(monkeypatch):
    """Model step reports a provider failure in the normalized vocabulary."""
    serve(monkeypatch, raises=error_fixture("openai", "unreachable"))

    with pytest.raises(ProviderError) as refused:
        model_step(run_state(("user", "Hello!")), invocation())

    assert refused.value.code == "provider_unavailable"
    assert "unreachable" not in refused.value.safe_message


def test_model_step_missing_credential_raises_configuration_error(monkeypatch):
    """Model step raises a safe configuration error when a binding is missing."""
    monkeypatch.delenv(CREDENTIAL_REF, raising=False)

    with pytest.raises(ProviderError) as refused:
        model_step(run_state(("user", "Hello!")), invocation())

    assert refused.value.code == "configuration_error"
    assert CREDENTIAL_REF in refused.value.safe_message, (
        "the message names the binding the operator has to set, never its value"
    )


def test_model_step_output_bounded_by_max_tokens(monkeypatch):
    """Model step uses bounded output budget (the run's own limit)."""
    transport = serve(
        monkeypatch,
        chunks=[sdk_chunk(content="Bounded response", finish_reason="stop")],
    )

    model_step(run_state(("user", "Hello!")), invocation())

    assert transport.seen_kwargs["max_tokens"] == RunLimits().max_output_tokens
    assert transport.seen_kwargs["model"] == FIXTURE_MODEL, (
        "the step served the model the run carries, not today's setting"
    )


def test_execute_model_turn_returns_model_result(monkeypatch):
    """execute_model_turn returns a ModelResult with response and usage."""
    serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="Hello back!"),
            sdk_chunk(finish_reason="stop"),
            sdk_chunk(choices=False, usage=(None, 5, None)),
        ],
    )

    result = execute_model_turn(
        "test-run-6",
        "test-thread-6",
        0,
        run_state(("user", "Hello!")),
        invocation_factory=invocation(),
    )

    assert isinstance(result, ModelResult)
    assert result.assistant_response == "Hello back!"
    assert result.error is None
    assert result.usage["output_tokens"] == 5
    assert result.usage["input_tokens"] is None, "absence stays absence, never a zero"


def test_execute_model_turn_error_returns_result_with_error(monkeypatch):
    """execute_model_turn returns a ModelResult with a safe error on failure."""
    serve(monkeypatch, raises=error_fixture("openai", "unauthorized"))

    result = execute_model_turn(
        "test-run-7",
        "test-thread-7",
        0,
        run_state(("user", "Hello!")),
        invocation_factory=invocation(),
    )

    assert isinstance(result, ModelResult)
    assert result.error is not None
    assert result.error_code == "authentication_error"
    assert "Traceback" not in result.error


def test_build_chat_graph_returns_compiled_graph():
    """build_chat_graph returns a CompiledStateGraph."""
    graph = build_chat_graph(None)
    # CompiledStateGraph is the result of .compile()
    assert hasattr(graph, "invoke")
    assert hasattr(graph, "stream")


def test_graph_state_has_required_fields():
    """Graph state includes the fields a resumed thread needs, and no runtime ones."""

    assert "messages" in ChatState.__annotations__
    assert "run_id" in ChatState.__annotations__
    assert "conversation_id" in ChatState.__annotations__
    assert "assistant_response" in ChatState.__annotations__
    assert "model_configuration" in ChatState.__annotations__, (
        "a resumed thread must run the model its run pinned"
    )
    assert "control" not in ChatState.__annotations__, (
        "the runtime control is injected per invocation, never serialized"
    )
    assert "recorder" not in ChatState.__annotations__


def test_partial_stream_leaves_no_completed_assistant_message(monkeypatch):
    """When the stream is cut short, no completed assistant message is returned."""
    serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="A beginning that was cut off"),
            sdk_chunk(content=" and never finished", finish_reason="length"),
        ],
    )

    values = model_step(run_state(("user", "Hello!")), invocation())
    assert values["finish_reason"] == "length"

    result = execute_model_turn(
        "test-run-8",
        "test-thread-8",
        0,
        run_state(("user", "Hello!")),
        invocation_factory=invocation(),
    )

    assert result.assistant_response == "", "partial text is never an answer"
    assert result.error_code == "output_limit_exceeded"
