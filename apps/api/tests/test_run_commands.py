"""Tests for durable run commands: submit_turn, require_owned_conversation, request_cancel."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_submit_turn_creates_run_and_message(client: TestClient, authorized_client: dict):
    """Submitting a turn creates a conversation, run, message, job, event, and outbox entry."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    # Now submit a durable turn
    client_request_id = str(uuid.uuid4())
    response = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "My turn", "client_request_id": client_request_id},
        headers=authorized_client,
    )

    assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
    body = response.json()
    assert "id" in body
    assert body["status"] == "queued"


def test_submit_turn_idempotent(client: TestClient, authorized_client: dict):
    """Resubmitting with the same client_request_id returns the same run (idempotent)."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    client_request_id = str(uuid.uuid4())

    # First submission
    resp1 = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "My turn", "client_request_id": client_request_id},
        headers=authorized_client,
    )
    assert resp1.status_code == 202, f"First submit failed: {resp1.text}"
    run_id_1 = resp1.json()["id"]

    # Second submission with same client_request_id should be idempotent (202 again)
    resp2 = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "My turn", "client_request_id": client_request_id},
        headers=authorized_client,
    )
    assert resp2.status_code == 202, f"Idempotent submit failed: {resp2.status_code} {resp2.text}"
    run_id_2 = resp2.json()["id"]

    assert run_id_1 == run_id_2


def test_submit_turn_same_key_different_content_conflict(client: TestClient, authorized_client: dict):
    """Same client_request_id with different content returns 409."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    client_request_id = str(uuid.uuid4())

    # First submission
    resp1 = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "Content A", "client_request_id": client_request_id},
        headers=authorized_client,
    )
    assert resp1.status_code == 202, f"First submit failed: {resp1.text}"

    # Second submission with same key but different content
    resp2 = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "Content B", "client_request_id": client_request_id},
        headers=authorized_client,
    )
    assert resp2.status_code == 409, f"Expected 409, got {resp2.status_code}: {resp2.text}"


def test_submit_turn_unauthorized(client: TestClient):
    """Submitting a turn without auth returns 401."""
    conv_id = uuid.uuid4()
    response = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "My turn", "client_request_id": str(uuid.uuid4())},
    )
    assert response.status_code == 401


def test_submit_turn_unknown_conversation_returns_404(client: TestClient, authorized_client: dict):
    """Submitting a turn to an unknown conversation returns 404 (no auto-creation)."""
    unknown_conv_id = str(uuid.uuid4())
    response = client.post(
        f"/api/runs/{unknown_conv_id}/turns",
        json={"content": "My turn", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"


def test_submit_turn_empty_content_returns_422(client: TestClient, authorized_client: dict):
    """Submitting a turn with empty content returns 422."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    response = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"


def test_submit_turn_oversized_content_returns_422(client: TestClient, authorized_client: dict):
    """Submitting a turn with oversized content returns 422."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    oversized_content = "x" * 10001
    response = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": oversized_content, "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"


def test_request_cancel_unauthorized(client: TestClient):
    """Requesting cancel without auth returns 401."""
    run_id = str(uuid.uuid4())
    response = client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 401


def test_request_cancel_unknown_run_returns_404(client: TestClient, authorized_client: dict):
    """Requesting cancel for an unknown run returns 404."""
    unknown_run_id = str(uuid.uuid4())
    response = client.post(
        f"/api/runs/{unknown_run_id}/cancel",
        headers=authorized_client,
    )
    assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"


def test_cancel_replay_idempotent(client: TestClient, authorized_client: dict):
    """Cancel requests are idempotent (replay returns success)."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    client_request_id = str(uuid.uuid4())
    resp = client.post(
        f"/api/runs/{conv_id}/turns",
        json={"content": "My turn", "client_request_id": client_request_id},
        headers=authorized_client,
    )
    assert resp.status_code == 202
    run_id = resp.json()["id"]

    # First cancel
    cancel1 = client.post(f"/api/runs/{run_id}/cancel", headers=authorized_client)
    assert cancel1.status_code == 200, f"First cancel failed: {cancel1.text}"

    # Replay cancel should be idempotent
    cancel2 = client.post(f"/api/runs/{run_id}/cancel", headers=authorized_client)
    assert cancel2.status_code == 200, f"Replay cancel failed: {cancel2.status_code} {cancel2.text}"


def test_old_chat_stream_adapter(client: TestClient, authorized_client: dict):
    """The old POST /api/chat/{id}/messages stream still works as an adapter."""
    # Create conversation explicitly first
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock(content="Hello")
    mock_done = MagicMock()
    mock_done.choices = []

    with patch("src.openship.chat.service.chat") as mock_chat:
        mock_chat.return_value = [mock_chunk, mock_done]

        with patch("src.openship.chat.routes.settings") as mock_settings:
            mock_settings.openai_api_key = "fake-key"
            mock_settings.openai_model = "gpt-4o"
            mock_settings.openai_base_url = "https://api.openai.com/v1"

            with patch("src.openship.chat.llm.settings") as mock_llm_settings:
                mock_llm_settings.openai_api_key = "fake-key"
                mock_llm_settings.openai_model = "gpt-4o"
                mock_llm_settings.openai_base_url = "https://api.openai.com/v1"

                response = client.post(
                    f"/api/chat/{conv_id}/messages",
                    json={"content": "Old style message"},
                    headers=authorized_client,
                )

                assert response.status_code == 200
                assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

                # Parse SSE events
                text = response.text
                events = [
                    line for line in text.strip().split("\n") if line.startswith("data:")
                ]
                assert any("Hello" in e for e in events)
                assert any("[DONE]" in e for e in events)
