"""Tests for the conversation listing endpoint."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_list_conversations(client: TestClient, authorized_client: dict):
    """Test listing conversations returns a list."""
    # Create a conversation explicitly (Phase 2: no auto-creation on send)
    client.post("/api/conversations", json={"title": "Test Conversation"}, headers=authorized_client)

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
