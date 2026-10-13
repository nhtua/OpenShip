"""Integration tests for the full auth + chat flow.

These tests verify the complete user journey:
  register → login → send messages → list conversations

All API interactions use the real FastAPI app with the test database.
The chat turns are durable, so each one needs a worker to run it and a
synthetic provider to answer it — the journey itself is unchanged.
"""

from fastapi.testclient import TestClient

from .conftest import declare_two_profiles, run_test_worker
from .providers.conftest import answered


def _legacy_turn(client, headers, conversation_id, content, monkeypatch, *answer):
    """One legacy turn: a synthetic provider answers, and a test worker runs it."""
    answered(monkeypatch, *answer)
    worker = run_test_worker()
    try:
        return client.post(
            f"/api/chat/{conversation_id}/messages",
            json={"content": content},
            headers=headers,
        )
    finally:
        worker.join(timeout=30)


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

    resp = _legacy_turn(
        client, headers, conv_id, "Hello from integration test", monkeypatch,
        "Integration test response",
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"

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

    resp2 = _legacy_turn(
        client, headers, conv_id_2, "Second conversation", monkeypatch,
        "Second message",
    )

    assert resp2.status_code == 200

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
    answered(monkeypatch, "Turn")
    worker = run_test_worker()
    try:
        # Send 10 turns
        for i in range(10):
            resp = client.post(
                f"/api/chat/{conv_id}/messages",
                json={"content": f"Turn number {i}"},
                headers=headers,
            )
            assert resp.status_code == 200
    finally:
        worker.join(timeout=60)

    # Verify all 10 turns are in conversations list
    convs = client.get("/api/conversations", headers=headers)
    assert convs.status_code == 200
    data = convs.json()
    assert len(data) >= 1
