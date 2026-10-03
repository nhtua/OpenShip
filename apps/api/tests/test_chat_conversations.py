"""Tests for the conversation listing endpoint."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_list_conversations(client: TestClient, authorized_client: dict):
    """Test listing conversations returns a list."""
    # Create a conversation by sending a message
    conv_id = uuid.uuid4()

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Hi")
    mock_done = MagicMock()
    mock_done.choices = []

    with patch("src.openship.chat.service.chat") as mock_chat:
        mock_chat.return_value = [mock_chunk, mock_done]

        with patch("src.openship.chat.routes.settings") as mock_settings:
            mock_settings.openai_api_key = "fake-key-for-testing"
            mock_settings.openai_model = "gpt-4o"
            mock_settings.openai_base_url = "https://api.openai.com/v1"

            with patch("src.openship.chat.llm.settings") as mock_llm_settings:
                mock_llm_settings.openai_api_key = "fake-key-for-testing"
                mock_llm_settings.openai_model = "gpt-4o"
                mock_llm_settings.openai_base_url = "https://api.openai.com/v1"

                client.post(
                    f"/api/chat/{conv_id}/messages",
                    json={"content": "Hello"},
                    headers=authorized_client,
                )

    response = client.get("/api/conversations", headers=authorized_client)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_list_conversations_empty(client: TestClient, authorized_client: dict):
    """Test listing conversations when user has none."""
    response = client.get("/api/conversations", headers=authorized_client)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_list_conversations_unauthorized(client: TestClient):
    """Test listing conversations without auth returns 401."""
    response = client.get("/api/conversations")
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthorized"
