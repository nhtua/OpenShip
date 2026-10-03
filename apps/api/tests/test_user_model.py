from src.openship.auth.models import User
import pytest


def test_user_model_fields():
    user = User(username="testuser", email="test@example.com", password_hash="hash")
    assert user.username == "testuser"
    assert user.email == "test@example.com"
