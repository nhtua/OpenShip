import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import Base, User
from src.openship.chat.models import Conversation, Message
import src.openship.database.session as db_session

# Use a temporary file so the in-memory DB survives across connections
# (each :memory: connection creates a separate DB).
_test_dir = tempfile.mkdtemp()
_test_db_path = os.path.join(_test_dir, "test.db")
_TEST_DATABASE_URL = f"sqlite:///{_test_db_path}"

# Recreate engine and session factory for the test database.
test_engine = create_engine(
    _TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=test_engine
)

# Patch the module so that get_db() uses our test engine.
db_session.engine = test_engine
db_session.SessionLocal = TestSessionLocal


@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test and drop them afterwards."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    from fastapi.testclient import TestClient
    from src.openship.main import app
    return TestClient(app)


@pytest.fixture
def auth_token(client: TestClient) -> str:
    """Register a user and return an auth token."""
    response = client.post("/api/auth/register", json={
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "password123",
    })
    assert response.status_code == 201
    return response.json()["access_token"]


@pytest.fixture
def authorized_client(client: TestClient, auth_token: str):
    """Return headers with Bearer token for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}
