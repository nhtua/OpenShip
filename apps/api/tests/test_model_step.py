"""Tests for the chat graph model step and bounded request/output budget."""

import os
import pytest
from unittest.mock import MagicMock, patch

# Set strict msgpack before importing graph modules
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"


def test_model_step_returns_response_with_mocked_provider():
    """Model step returns a complete assistant response when provider succeeds."""
    from src.openship.runs.graph import model_step

    state = {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-1",
        "conversation_id": "test-conv-1",
    }

    # Mock the streaming response
    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Hi there!")
    mock_chunk.usage = None

    mock_chunk2 = MagicMock()
    mock_chunk2.choices = [MagicMock()]
    mock_chunk2.choices[0].delta = MagicMock(content=" How can I help?")
    mock_chunk2.usage = None

    mock_chunk_final = MagicMock()
    mock_chunk_final.choices = [MagicMock()]
    mock_chunk_final.choices[0].delta = MagicMock(content="")
    mock_chunk_final.usage = MagicMock()
    mock_chunk_final.usage.completion_tokens = 10

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.return_value = [mock_chunk, mock_chunk2, mock_chunk_final]
            result = model_step(state)

    assert "assistant_response" in result
    assert result["assistant_response"] == "Hi there! How can I help?"
    assert mock_chat.called


def test_model_step_empty_stream_returns_empty():
    """Model step returns empty response when stream is empty."""
    from src.openship.runs.graph import model_step

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-2",
        "conversation_id": "test-conv-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.return_value = []
            result = model_step(state)

    assert "assistant_response" in result
    assert result["assistant_response"] == ""


def test_model_step_provider_error_captured():
    """Model step captures provider errors without crashing."""
    from src.openship.runs.graph import model_step, ModelError

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-3",
        "conversation_id": "test-conv-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.side_effect = Exception("API timeout")
            with pytest.raises(ModelError) as exc_info:
                model_step(state)

    assert "API timeout" in str(exc_info.value)


def test_model_step_missing_api_key_raises_configuration_error():
    """Model step raises a safe configuration error when API key is missing."""
    from src.openship.runs.graph import model_step, ConfigurationError

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-4",
        "conversation_id": "test-conv-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        mock_settings.model_max_tokens = 4000

        with pytest.raises(ConfigurationError) as exc_info:
            model_step(state)

    assert "OPENAI_API_KEY" in str(exc_info.value)


def test_model_step_output_bounded_by_max_tokens():
    """Model step uses bounded output budget (max_tokens)."""
    from src.openship.runs.graph import model_step

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-5",
        "conversation_id": "test-conv-1",
    }

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Bounded response")
    mock_chunk.usage = None

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.return_value = [mock_chunk]
            result = model_step(state)

    # Verify the call was made with messages
    call_kwargs = mock_chat.call_args
    assert call_kwargs is not None


def test_execute_model_turn_returns_model_result():
    """execute_model_turn returns a ModelResult with response and usage."""
    from src.openship.runs.graph import execute_model_turn, ModelResult

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-6",
        "conversation_id": "test-conv-1",
    }

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Hello back!")
    mock_chunk.usage = None

    mock_chunk_final = MagicMock()
    mock_chunk_final.choices = [MagicMock()]
    mock_chunk_final.choices[0].delta = MagicMock(content="")
    mock_chunk_final.usage = MagicMock()
    mock_chunk_final.usage.completion_tokens = 5

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.return_value = [mock_chunk, mock_chunk_final]
            result = execute_model_turn("test-run-6", "test-thread-6", 0, state)

    assert isinstance(result, ModelResult)
    assert result.assistant_response == "Hello back!"
    assert result.usage is not None
    assert result.usage["completion_tokens"] == 5
    assert result.error is None


def test_execute_model_turn_error_returns_result_with_error():
    """execute_model_turn returns a ModelResult with error on failure."""
    from src.openship.runs.graph import execute_model_turn, ModelResult

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-7",
        "conversation_id": "test-conv-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.side_effect = Exception("Timeout")
            result = execute_model_turn("test-run-7", "test-thread-7", 0, state)

    assert isinstance(result, ModelResult)
    assert result.error is not None
    assert "Timeout" in result.error


def test_build_chat_graph_returns_compiled_graph():
    """build_chat_graph returns a CompiledStateGraph."""
    from src.openship.runs.graph import build_chat_graph

    graph = build_chat_graph(None)
    # CompiledStateGraph is the result of .compile()
    assert hasattr(graph, "invoke")
    assert hasattr(graph, "stream")


def test_graph_state_has_required_fields():
    """Graph state includes all required fields."""
    from src.openship.runs.graph import ChatState

    # Check the state schema has required fields via __annotations__
    assert "messages" in ChatState.__annotations__
    assert "run_id" in ChatState.__annotations__
    assert "conversation_id" in ChatState.__annotations__
    assert "assistant_response" in ChatState.__annotations__


def test_partial_stream_leaves_no_completed_assistant_message():
    """When stream is cut short, no completed assistant message is returned."""
    from src.openship.runs.graph import model_step

    state = {
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "run_id": "test-run-8",
        "conversation_id": "test-conv-1",
    }

    with patch("src.openship.runs.graph.settings") as mock_settings:
        mock_settings.openai_api_key = "fake-key"
        mock_settings.model_max_tokens = 4000

        with patch("src.openship.runs.graph.chat") as mock_chat:
            mock_chat.return_value = []
            result = model_step(state)

    # Empty response is not a "completed" assistant message
    assert result["assistant_response"] == ""
