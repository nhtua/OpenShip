"""The legacy chat route: a Phase-1 contract over the durable pipeline.

The route no longer calls a provider in the API process. It queues a turn
like every other turn and translates what a worker made durable, so these
cases need two things a Phase-1 test never had: a worker that actually runs
the turn, and a provider that is synthetic. Everything else — the shapes a
Phase-1 client parses, and what it must never see — is unchanged.
"""

import uuid

from fastapi.testclient import TestClient

from .conftest import (
    CREDENTIAL_BINDING,
    SENTINEL_CREDENTIAL,
    declare_two_profiles,
    run_test_worker,
)
from .providers.conftest import answered


def test_send_message(client: TestClient, authorized_client: dict, monkeypatch):
    """A Phase-1 client still gets its answer inside one response."""
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    declare_two_profiles(monkeypatch)
    transport = answered(monkeypatch, "Hello", " world!")
    worker = run_test_worker()
    try:
        response = client.post(
            f"/api/chat/{conv_id}/messages",
            json={"content": "Hello OpenShip!"},
            headers=authorized_client,
        )
    finally:
        worker.join(timeout=30)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    events = [line for line in response.text.strip().split("\n") if line.startswith("data:")]

    assert any("Hello" in line for line in events)
    assert any("world!" in line for line in events)
    assert any('"type": "complete"' in line for line in events)
    assert any("[DONE]" in line for line in events)

    # The provider was asked once, and it saw the conversation in order.
    assert transport.requests == 1, "one response means one request"
    asked = transport.seen_kwargs["messages"]
    assert asked[0]["role"] == "system"
    assert asked[-1]["role"] == "user"
    assert "Hello OpenShip!" in asked[-1]["content"]


def test_send_message_to_unknown_conversation_returns_404(
    client: TestClient, authorized_client: dict
):
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


def test_send_message_missing_api_key(client: TestClient, authorized_client: dict, monkeypatch):
    """An installation whose credential binding is absent says so, and queues nothing."""
    conv_resp = client.post("/api/conversations", json={"title": "Test"}, headers=authorized_client)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    # The legacy installation names the binding it needs; no value is ever read.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        f"/api/chat/{conv_id}/messages",
        json={"content": "Hello"},
        headers=authorized_client,
    )

    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "api_key_not_configured"
    assert "OPENAI_API_KEY" in data["error"]["message"]
    assert SENTINEL_CREDENTIAL not in data["error"]["message"], "a safe reason names no value"
    assert CREDENTIAL_BINDING not in data["error"]["message"]
