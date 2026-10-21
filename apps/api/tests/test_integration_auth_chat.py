"""Integration tests for the full auth + chat flow.

These tests verify the complete user journey:
  register → login → send messages → list conversations

All API interactions use the real FastAPI app with the test database.
The chat turns are durable, so each one needs a worker to run it and a
synthetic provider to answer it — the journey itself is unchanged.
"""

import uuid

from fastapi.testclient import TestClient

from .conftest import declare_two_profiles, execute_test_run
from .providers.conftest import answered


def _durable_turn(client, headers, conversation_id, content, monkeypatch, *answer):
    """Submit a turn, execute it in the worker, and read its durable outcome."""
    answered(monkeypatch, *answer)
    response = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={"content": content, "client_request_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    execute_test_run(run_id)
    result = client.get(f"/api/runs/{run_id}", headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == "completed", result.text
    return response


def test_full_auth_then_chat_flow(client: TestClient, monkeypatch):
    """End-to-end: register, login, send message, list conversations."""

    # --- Step 1: Register ---
    reg = client.post("/api/auth/register", json={
        "username": "integration_user",
        "email": "integration@example.com",
        "password": "securePass123",
    })
    assert reg.status_code == 201
    reg_data = reg.json()
    assert "access_token" in reg_data
    assert reg_data["user"]["username"] == "integration_user"
    token = reg_data["access_token"]

    # --- Step 2: Login (verify we get a new token) ---
    login = client.post("/api/auth/login", json={
        "username": "integration_user",
        "password": "securePass123",
    })
    assert login.status_code == 200
    login_data = login.json()
    assert "access_token" in login_data
    assert login_data["user"]["username"] == "integration_user"

    headers = {"Authorization": f"Bearer {token}"}
    declare_two_profiles(monkeypatch)

    # --- Step 3: Send a chat message ---
    # Phase 2: must create conversation explicitly (no auto-creation)
    conv_resp = client.post("/api/conversations", json={"title": "First"}, headers=headers)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    resp = _durable_turn(
        client, headers, conv_id, "Hello from integration test", monkeypatch,
        "Integration test response",
    )

    assert resp.status_code == 202
    # --- Step 4: List conversations (should include the one we just created) ---
    convs = client.get("/api/conversations", headers=headers)
    assert convs.status_code == 200
    data = convs.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # --- Step 5: Send another message in a new conversation ---
    # Phase 2: must create conversation explicitly (no auto-creation)
    conv_id_2_resp = client.post("/api/conversations", json={"title": "Second"}, headers=headers)
    assert conv_id_2_resp.status_code == 201
    conv_id_2 = conv_id_2_resp.json()["id"]

    resp2 = _durable_turn(
        client, headers, conv_id_2, "Second conversation", monkeypatch,
        "Second message",
    )

    assert resp2.status_code == 202

    # --- Step 6: List conversations again (should now have 2) ---
    convs2 = client.get("/api/conversations", headers=headers)
    assert convs2.status_code == 200
    data2 = convs2.json()
    assert len(data2) == 2


def test_auth_then_multiple_chat_turns(client: TestClient, monkeypatch):
    """Simulate 10 conversation turns in a single conversation."""

    # Register and login
    client.post("/api/auth/register", json={
        "username": "turn_taker",
        "email": "turns@example.com",
        "password": "turnPass123",
    })
    login = client.post("/api/auth/login", json={
        "username": "turn_taker",
        "password": "turnPass123",
    })
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Phase 2: must create conversation explicitly (no auto-creation)
    conv_resp = client.post("/api/conversations", json={"title": "Turn test"}, headers=headers)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    declare_two_profiles(monkeypatch)
    for i in range(10):
        response = _durable_turn(
            client, headers, conv_id, f"Turn number {i}", monkeypatch, "Turn"
        )
        assert response.status_code == 202

    history = client.get(f"/api/conversations/{conv_id}/messages", headers=headers)
    assert history.status_code == 200
    assert len(history.json()) == 20

    # Verify all 10 turns are in conversations list
    convs = client.get("/api/conversations", headers=headers)
    assert convs.status_code == 200
    data = convs.json()
    assert len(data) >= 1
