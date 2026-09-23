import pytest
from fastapi.testclient import TestClient
from openship.app import create_app

app = create_app()
client = TestClient(app)


def test_full_workflow_streaming():
    """Test that the full workflow streams events correctly."""
    response = client.post(
        "/api/workflows",
        json={"requirements": "Simple web app with load balancer"},
        headers={"Accept": "text/event-stream"}
    )
    assert response.status_code == 200
    assert "stage_start" in response.text
    assert "stage_complete" in response.text
    assert "complete" in response.text