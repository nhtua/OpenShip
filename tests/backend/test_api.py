import pytest
from fastapi.testclient import TestClient
from openship.app import create_app

app = create_app()
client = TestClient(app)


def test_create_workflow_streams_events():
    response = client.post("/api/workflows", json={"requirements": "test"})
    assert response.status_code == 200
    assert response.headers.get("content-type").startswith("text/event-stream")
    assert "stage_start" in response.text
    assert "stage_complete" in response.text
    assert "complete" in response.text


def test_stream_events_endpoint_exists():
    response = client.get("/api/workflows/test-id/events")
    assert response.status_code == 200
