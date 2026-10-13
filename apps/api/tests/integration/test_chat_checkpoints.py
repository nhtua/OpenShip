"""Integration tests for checkpointed chat graph.

These tests verify the checkpointing behavior using LangGraph's InMemorySaver,
which implements the same interface as PostgresSaver. The saver interface
is what matters for graph checkpointing correctness.

The graph is driven by the configuration a run carries, so a case supplies one
instead of today's settings: what a resumed thread runs is what its run pinned.
"""

import os
import uuid

import pytest

# Set strict msgpack before importing graph modules
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

from src.openship.providers.contracts import RunLimits, RunModelConfig
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import _adapter_factories
from src.openship.runs.graph import ModelResult, build_chat_graph, execute_model_turn

#: Imported so the real adapter is registered before any case replaces one.
import src.openship.providers.openai  # noqa: F401

#: A value that must never appear in a checkpoint. Not a real credential.
SENTINEL_CREDENTIAL = "sentinel-credential-value"  # rayloc:ignore

from tests.providers.conftest import (
    CREDENTIAL_REF,
    StreamTransport,
    error_fixture,
    invocation,
    make_adapter,
    qualified_profile,
    sdk_chunk,
)


@pytest.fixture
def in_memory_saver():
    """Provide an in-memory checkpointer for testing."""
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def _snapshot() -> str:
    """A model configuration a run would carry, as the graph reads it."""
    config = RunModelConfig(
        schema_version=1,
        primary=qualified_profile(),
        limits=RunLimits(),
        binding_origin="submission",
    )
    return config.model_dump_json()


def _state(text: str) -> dict:
    """A graph state carrying the configuration this run pinned."""
    return {
        "messages": [{"role": "user", "content": text}],
        "run_id": "test-run-checkpoint",
        "conversation_id": "test-conv-checkpoint",
        "model_configuration": _snapshot(),
    }


def _serve(monkeypatch, chunks=(), raises=None) -> StreamTransport:
    """The installation's OpenAI adapter answers from a synthetic transport."""
    transport = StreamTransport(list(chunks), raises=raises)
    monkeypatch.setitem(_adapter_factories, "openai", lambda _profile: make_adapter("openai", transport))
    return transport


def test_graph_creates_checkpoint_readable_from_new_connection(in_memory_saver, monkeypatch):
    """A graph run creates a checkpoint readable from a new process/connection."""
    graph = build_chat_graph(in_memory_saver, invocation_factory=invocation())

    _serve(monkeypatch, chunks=[sdk_chunk(content="Test response!", finish_reason="stop")])

    # Run the graph
    thread_id = f"test-thread-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(_state("Hello from test!"), config=config)

    # Read checkpoint back
    state = graph.get_state(config)
    assert state is not None
    assert "messages" in state.values


def test_different_runs_cannot_read_each_others_thread(in_memory_saver, monkeypatch):
    """Different runs cannot read each other's thread by application API."""
    graph = build_chat_graph(in_memory_saver, invocation_factory=invocation())

    thread_id_1 = f"test-thread-1-{uuid.uuid4().hex[:8]}"
    thread_id_2 = f"test-thread-2-{uuid.uuid4().hex[:8]}"

    # Run graph for thread 1
    config1 = {"configurable": {"thread_id": thread_id_1}}
    _serve(monkeypatch, chunks=[sdk_chunk(content="Response for thread 1", finish_reason="stop")])
    graph.invoke(_state("Message for thread 1"), config=config1)

    # Read thread 1 — should find checkpoint
    state1 = graph.get_state(config1)
    assert state1 is not None

    # Read thread 2 — should NOT find thread 1's data
    config2 = {"configurable": {"thread_id": thread_id_2}}
    state2 = graph.get_state(config2)
    # Either None or has no messages (empty state for this thread)
    assert state2 is None or "messages" not in state2.values


def test_provider_timeout_leaves_no_completed_assistant_message(in_memory_saver, monkeypatch):
    """A provider timeout leaves no completed assistant message in the checkpoint."""
    graph = build_chat_graph(in_memory_saver, invocation_factory=invocation())

    thread_id = f"test-thread-timeout-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    _serve(monkeypatch, raises=error_fixture("openai", "unreachable"))
    with pytest.raises(ProviderError) as refused:
        graph.invoke(_state("Message that will timeout"), config=config)
    assert refused.value.code == "provider_unavailable"

    # Check state — should not have completed assistant_response
    state = graph.get_state(config)
    if state is not None:
        values = state.values
        assert "assistant_response" not in values or values.get("assistant_response") == ""


def test_missing_credential_yields_safe_configuration_error(monkeypatch):
    """Missing credential yields a safe configuration error (no 500)."""
    monkeypatch.delenv(CREDENTIAL_REF, raising=False)

    result = execute_model_turn(
        "test-run-config",
        "test-thread-config",
        0,
        _state("Hello"),
        invocation_factory=invocation(),
    )

    assert isinstance(result, ModelResult)
    assert result.error is not None
    assert result.error_code == "configuration_error"
    assert CREDENTIAL_REF in result.error, (
        "the message names the binding to set, never a value"
    )
    # No stack trace or sensitive info
    assert "Traceback" not in result.error


def test_no_model_credential_in_state(in_memory_saver, monkeypatch):
    """Verify no model credential is stored in graph state."""
    monkeypatch.setenv(CREDENTIAL_REF, SENTINEL_CREDENTIAL)

    graph = build_chat_graph(in_memory_saver, invocation_factory=invocation())

    thread_id = f"test-thread-secret-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    _serve(monkeypatch, chunks=[sdk_chunk(content="Response without secret", finish_reason="stop")])
    graph.invoke(_state("Hello"), config=config)

    # Check state — should not contain the API key
    state = graph.get_state(config)
    state_json = str(state.values)
    assert SENTINEL_CREDENTIAL not in state_json, (
        "a credential value never reaches a checkpoint"
    )
    assert CREDENTIAL_REF in state_json, (
        "the state carries the binding name, which is the non-secret form"
    )
    assert "api_key" not in state_json.lower()


def test_absence_of_usage_remains_null(in_memory_saver, monkeypatch):
    """When there is no usage info, it remains null (not 0 or empty dict)."""
    _serve(monkeypatch, chunks=[sdk_chunk(content="Response", finish_reason="stop")])

    result = execute_model_turn(
        "test-run-usage",
        "test-thread-usage",
        0,
        _state("Hello"),
        invocation_factory=invocation(),
    )

    assert isinstance(result, ModelResult)
    assert result.usage is None
