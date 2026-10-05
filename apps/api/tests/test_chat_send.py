"""Tests for the chat send message endpoint (SSE streaming)."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_send_message(client: TestClient, authorized_client: dict):
    """Test sending a message with SSE streaming and verifying response."""
    # Create conversation explicitly first (Phase 2: no auto-creation)
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    # Mock the OpenAI chat response
    mock_chunk1 = MagicMock()
    mock_chunk1.choices = [MagicMock()]
    mock_chunk1.choices[0].delta = MagicMock(content="Hello")

    mock_chunk2 = MagicMock()
    mock_chunk2.choices = [MagicMock()]
    mock_chunk2.choices[0].delta = MagicMock(content=" world!")

    mock_chunk_done = MagicMock()
    mock_chunk_done.choices = []

    mock_stream = [mock_chunk1, mock_chunk2, mock_chunk_done]

    with patch("src.openship.chat.service.chat") as mock_chat:
        mock_chat.return_value = mock_stream

        with patch("src.openship.chat.routes.settings") as mock_settings:
            mock_settings.openai_api_key = "fake-key-for-testing"
            mock_settings.openai_model = "gpt-4o"
            mock_settings.openai_base_url = "https://api.openai.com/v1"

            with patch("src.openship.chat.llm.settings") as mock_llm_settings:
                mock_llm_settings.openai_api_key = "fake-key-for-testing"
                mock_llm_settings.openai_model = "gpt-4o"
                mock_llm_settings.openai_base_url = "https://api.openai.com/v1"

                response = client.post(
                    f"/api/chat/{conv_id}/messages",
                    json={"content": "Hello OpenShip!"},
                    headers=authorized_client,
                )

                assert response.status_code == 200
                assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

                # Parse SSE events
                text = response.text
                events = [
                    line for line in text.strip().split("\n") if line.startswith("data:")
                ]

                # Should have chunk events, a complete event, and [DONE]
                assert any("Hello" in e for e in events)
                assert any("world!" in e for e in events)
                assert any('"type": "complete"' in e for e in events)
                assert any("[DONE]" in e for e in events)

        # Verify chat was called with correct messages
        mock_chat.assert_called_once()
        call_args = mock_chat.call_args
        messages = call_args[0][0]

        # Should have system prompt, then user message
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Hello OpenShip!" in messages[1]["content"]


def test_send_message_to_unknown_conversation_returns_404(client: TestClient, authorized_client: dict):
    """Phase 2: sending to an unknown conversation returns 404 (no auto-creation)."""
    new_conv_id = uuid.uuid4()
    response = client.post(
        f"/api/chat/{new_conv_id}/messages",
        json={"content": "Start a new conversation"},
        headers=authorized_client,
    )
    assert response.status_code == 404


def test_send_message_unauthorized(client: TestClient):
    """Test that sending a message without auth returns 401."""
    response = client.post(
        "/api/chat/00000000-0000-0000-0000-000000000000/messages",
        json={"content": "Hello"},
    )
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthorized"


def test_send_message_missing_api_key(client: TestClient, authorized_client: dict):
    """Test that missing API key returns 503 with user-friendly message."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    with patch("src.openship.chat.llm.chat") as mock_chat:
        mock_chat.return_value = []

        # Patch settings to have no API key
        with patch("src.openship.chat.routes.settings") as mock_settings:
            mock_settings.openai_api_key = ""
            mock_settings.openai_model = "gpt-4o"
            mock_settings.openai_base_url = "https://api.openai.com/v1"

            response = client.post(
                f"/api/chat/{conv_id}/messages",
                json={"content": "Hello"},
                headers=authorized_client,
            )

            assert response.status_code == 503
            data = response.json()
            assert data["error"]["code"] == "api_key_not_configured"
            assert "OPENAI_API_KEY" in data["error"]["message"]
