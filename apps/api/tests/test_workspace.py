from .conftest import declare_two_profiles, run_test_worker
from .providers.conftest import answered


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


def test_list_conversations_via_workspace(client, auth_token, monkeypatch):
    """GET /api/workspace/conversations returns user's conversations."""
    headers = {"Authorization": f"Bearer {auth_token}"}

    # Phase 2: must create conversation explicitly (no auto-creation)
    conv_resp = client.post("/api/conversations", json={"title": "New Conversation"}, headers=headers)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    declare_two_profiles(monkeypatch)
    answered(monkeypatch, "Test response")
    worker = run_test_worker()
    try:
        resp = client.post(
            f"/api/chat/{conv_id}/messages",
            json={"content": "Test message"},
            headers=headers,
        )
    finally:
        worker.join(timeout=30)

    assert resp.status_code == 200

    # Now list conversations via workspace endpoint
    response = client.get("/api/workspace/conversations", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    listed = {row["id"]: row for row in data}
    assert conv_id in listed, "the conversation the turn belongs to is listed"
    assert listed[conv_id]["message_count"] >= 1, "the turn's own message is counted"


def test_list_conversations_via_workspace_empty(client, auth_token):
    """GET /api/workspace/conversations returns empty list if no conversations."""
    response = client.get("/api/workspace/conversations", headers={"Authorization": f"Bearer {auth_token}"})
    assert response.status_code == 200
    assert response.json() == []


def test_list_conversations_via_workspace_requires_auth(client):
    """GET /api/workspace/conversations requires authentication."""
    response = client.get("/api/workspace/conversations")
    assert response.status_code == 401
