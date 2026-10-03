from fastapi.testclient import TestClient

from src.openship.main import app

client = TestClient(app)


def test_register_new_user():
    response = client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "new@example.com",
        "password": "password123",
    })
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["user"]["username"] == "newuser"


def test_register_duplicate_username():
    client.post("/api/auth/register", json={
        "username": "dupuser",
        "email": "dup1@example.com",
        "password": "password123",
    })
    response = client.post("/api/auth/register", json={
        "username": "dupuser",
        "email": "dup2@example.com",
        "password": "password123",
    })
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "username_taken"


def test_register_duplicate_email():
    client.post("/api/auth/register", json={
        "username": "unique1",
        "email": "shared@example.com",
        "password": "password123",
    })
    response = client.post("/api/auth/register", json={
        "username": "unique2",
        "email": "shared@example.com",
        "password": "password123",
    })
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "email_taken"


def test_register_password_too_short():
    response = client.post("/api/auth/register", json={
        "username": "shortpass",
        "email": "short@example.com",
        "password": "abc",
    })
    assert response.status_code == 422
