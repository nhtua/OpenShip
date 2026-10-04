import uuid
from unittest.mock import MagicMock, patch


def test_get_workspace(client, auth_token, request):
    """GET /api/workspace returns user's workspace info."""
    import hashlib
    test_name = request.node.name
    short_id = hashlib.md5(test_name.encode()).hexdigest()[:8]
    username = f"tuser_{short_id}"

    response = client.get("/api/workspace", headers={"Authorization": f"Bearer {auth_token}"})
    assert response.status_code == 200

    data = response.json()
    assert data["user"]["username"] == username
    assert data["workspace"]["name"] == f"{username}'s workspace"


def test_get_workspace_requires_auth(client):
    """GET /api/workspace requires authentication."""
    response = client.get("/api/workspace")
    assert response.status_code == 401


def test_list_conversations_via_workspace(client, auth_token):
    """GET /api/workspace/conversations returns user's conversations."""
    headers = {"Authorization": f"Bearer {auth_token}"}

    # Phase 2: must create conversation explicitly (no auto-creation)
    conv_resp = client.post("/api/conversations", json={"title": "New Conversation"}, headers=headers)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Test response")
    mock_done = MagicMock()
    mock_done.choices = []

    with patch("src.openship.chat.llm.chat") as mock_chat:
        mock_chat.return_value = [mock_chunk, mock_done]

        with patch("src.openship.chat.routes.settings") as mock_settings:
            mock_settings.openai_api_key = "fake-key"
            mock_settings.openai_model = "gpt-4o"
            mock_settings.openai_base_url = "https://api.openai.com/v1"

            with patch("src.openship.chat.llm.settings") as mock_llm_settings:
                mock_llm_settings.openai_api_key = "fake-key"
                mock_llm_settings.openai_model = "gpt-4o"
                mock_llm_settings.openai_base_url = "https://api.openai.com/v1"

                resp = client.post(
                    f"/api/chat/{conv_id}/messages",
                    json={"content": "Test message"},
                    headers=headers,
                )

    assert resp.status_code == 200

    # Now list conversations via workspace endpoint
    response = client.get("/api/workspace/conversations", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["title"] == "New Conversation"
    assert data[0]["message_count"] >= 1


def test_list_conversations_via_workspace_empty(client, auth_token):
    """GET /api/workspace/conversations returns empty list if no conversations."""
    response = client.get("/api/workspace/conversations", headers={"Authorization": f"Bearer {auth_token}"})
    assert response.status_code == 200
    assert response.json() == []


def test_list_conversations_via_workspace_requires_auth(client):
    """GET /api/workspace/conversations requires authentication."""
    response = client.get("/api/workspace/conversations")
    assert response.status_code == 401
