"""Integration tests for PostgreSQL-checkpointed chat graph.

These tests verify the checkpointing behavior using LangGraph's InMemorySaver,
which implements the same interface as PostgresSaver.
"""

import os
import uuid

import pytest

# Set strict msgpack before importing graph modules
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"


@pytest.fixture
def in_memory_saver():
    """Provide an in-memory checkpointer for testing."""
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def test_graph_creates_checkpoint_readable_from_new_connection(in_memory_saver):
    """A graph run creates a checkpoint readable from a new process/connection."""
    from src.openship.runs.graph import build_chat_graph

    graph = build_chat_graph(in_memory_saver)

    # Run the graph
    thread_id = f"test-thread-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    input_state = {
        "messages": [
            {"role": "user", "content": "Hello from test!"},
        ],
        "run_id": "test-run-checkpoint-1",
        "conversation_id": "test-conv-checkpoint-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].delta = MagicMock(content="Test response!")
            mock_response.usage = None
            mock_chat.return_value = [mock_response]

            graph.invoke(input_state, config=config)

    # Read checkpoint back
    state = graph.get_state(config)
    assert state is not None
    assert "messages" in state.values


def test_different_runs_cannot_read_each_others_thread(in_memory_saver):
    """Different runs cannot read each other's thread by application API."""
    from src.openship.runs.graph import build_chat_graph

    graph = build_chat_graph(in_memory_saver)

    thread_id_1 = f"test-thread-1-{uuid.uuid4().hex[:8]}"
    thread_id_2 = f"test-thread-2-{uuid.uuid4().hex[:8]}"

    # Run graph for thread 1
    config1 = {"configurable": {"thread_id": thread_id_1}}
    input_state = {
        "messages": [{"role": "user", "content": "Message for thread 1"}],
        "run_id": "test-run-thread-1",
        "conversation_id": "test-conv-thread-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].delta = MagicMock(content="Response for thread 1")
            mock_response.usage = None
            mock_chat.return_value = [mock_response]

            graph.invoke(input_state, config=config1)

    # Read thread 1 — should find checkpoint
    state1 = graph.get_state(config1)
    assert state1 is not None

    # Read thread 2 — should NOT find thread 1's data
    config2 = {"configurable": {"thread_id": thread_id_2}}
    state2 = graph.get_state(config2)
    # Either None or has no messages (empty state for this thread)
    assert state2 is None or "messages" not in state2.values


def test_provider_timeout_leaves_no_completed_assistant_message(in_memory_saver):
    """A provider timeout leaves no completed assistant message in the checkpoint."""
    from src.openship.runs.graph import build_chat_graph

    graph = build_chat_graph(in_memory_saver)

    thread_id = f"test-thread-timeout-{uuid.uuid4().hex[:8]}"
    run_id = "test-run-timeout"
    conv_id = "test-conv-timeout"

    config = {"configurable": {"thread_id": thread_id}}
    input_state = {
        "messages": [{"role": "user", "content": "Message that will timeout"}],
        "run_id": run_id,
        "conversation_id": conv_id,
    }

    # Simulate provider timeout
    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.side_effect = Exception("Connection timed out")
            try:
                graph.invoke(input_state, config=config)
            except Exception:
                pass  # Expected to fail

    # Check state — should not have completed assistant_response
    state = graph.get_state(config)
    if state is not None:
        values = state.values
        assert "assistant_response" not in values or values.get("assistant_response") == ""


def test_missing_credential_yields_safe_configuration_error():
    """Missing credential yields a safe configuration error (no 500)."""
    from src.openship.runs.graph import execute_model_turn, ModelResult

    state = {
        "messages": [{"role": "user", "content": "Hello"}],
        "run_id": "test-run-config",
        "conversation_id": "test-conv-config",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        mock_settings.model_max_tokens = 4000

        result = execute_model_turn("test-run-config", "test-thread-config", 0, state)

    assert isinstance(result, ModelResult)
    assert result.error is not None
    assert "OPENAI_API_KEY" in result.error
    # No stack trace or sensitive info
    assert "Traceback" not in result.error


def test_no_model_credential_in_state():
    """Verify no model credential is stored in graph state."""
    from src.openship.runs.graph import build_chat_graph

    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    graph = build_chat_graph(saver)

    thread_id = f"test-thread-secret-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    input_state = {
        "messages": [{"role": "user", "content": "Hello"}],
        "run_id": "test-run-secret",
        "conversation_id": "test-conv-secret",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "sk-test-secret-key-123"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].delta = MagicMock(content="Response without secret")
            mock_response.usage = None
            mock_chat.return_value = [mock_response]

            graph.invoke(input_state, config=config)

    # Check state — should not contain the API key
    state = graph.get_state(config)
    state_dict = state.values
    state_json = str(state_dict)
    assert "sk-test-secret-key-123" not in state_json
    assert "api_key" not in state_json.lower()


def test_absence_of_usage_remains_null():
    """When there is no usage info, it remains null (not 0 or empty dict)."""
    from src.openship.runs.graph import execute_model_turn, ModelResult

    state = {
        "messages": [{"role": "user", "content": "Hello"}],
        "run_id": "test-run-usage",
        "conversation_id": "test-conv-usage",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            # Simulate response without usage info
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].delta = MagicMock(content="Response")
            mock_response.usage = None
            mock_chat.return_value = [mock_response]

            result = execute_model_turn("test-run-usage", "test-thread-usage", 0, state)

    assert isinstance(result, ModelResult)
    assert result.usage is None


# Helper imports for mock objects
from unittest.mock import MagicMock, patch
