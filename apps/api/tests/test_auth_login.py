from fastapi.testclient import TestClient

from src.openship.main import app

client = TestClient(app)


def _register_for_login(username: str = "loginuser") -> None:
    """Helper to register a user before testing login."""
    client.post("/api/auth/register", json={
        "username": username,
        "email": f"{username}@example.com",
        "password": "password123",
    })


def test_login_existing_user():
    _register_for_login()
    response = client.post("/api/auth/login", json={
        "username": "loginuser",
        "password": "password123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user"]["username"] == "loginuser"


def test_login_wrong_password():
    _register_for_login()
    response = client.post("/api/auth/login", json={
        "username": "loginuser",
        "password": "wrongpassword",
    })
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "invalid_credentials"


def test_login_user_not_found():
    response = client.post("/api/auth/login", json={
        "username": "nonexistent",
        "password": "password123",
    })
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "invalid_credentials"
