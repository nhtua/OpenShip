import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import Base, User
from src.openship.chat.models import Conversation, Message
import src.openship.database.session as db_session

# Use a temporary file so the in-memory DB survives across connections
_test_dir = tempfile.mkdtemp()
_test_db_path = os.path.join(_test_dir, "test.db")
_TEST_DATABASE_URL = f"sqlite:///{_test_db_path}"

# Ensure fresh database for each test session
if os.path.exists(_test_db_path):
    os.remove(_test_db_path)

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
def setup_db(request):
    """Set up the database schema using Alembic migrations.

    Skipped for migration integration tests that manage their own schema.
    """
    test_path = request.node.fspath.strpath
    if "integration" in test_path and (
        "test_phase2_migration" in test_path
        or "test_run_commands_pg" in test_path
    ):
        yield
        return

    # Run Alembic migrations to set up the full schema
    import subprocess
    import sys

    # Create a temporary alembic.ini for this test run
    script_dir = Path(__file__).parents[1] / "alembic"
    ini_content = f"""[alembic]
script_location = {script_dir}
sqlalchemy.url = {_TEST_DATABASE_URL}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    ini_path = Path(_test_dir) / "alembic_test.ini"
    ini_path.write_text(ini_content)

    env = {
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "ALEMBIC_CONFIG": str(ini_path),
    }

    # Upgrade to head (idempotent - does nothing if already at head)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).parents[1],
        timeout=30,
    )
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

    yield


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    from src.openship.main import app
    return TestClient(app)


@pytest.fixture
def auth_token(client: TestClient, request) -> str:
    """Register a user and return an auth token."""
    # Use unique username per test to avoid conflicts
    import hashlib
    test_name = request.node.name
    short_id = hashlib.md5(test_name.encode()).hexdigest()[:8]
    username = f"tuser_{short_id}"
    email = f"tuser_{short_id}@example.com"
    response = client.post("/api/auth/register", json={
        "username": username,
        "email": email,
        "password": "password123",
    })
    assert response.status_code == 201, f"Failed to register user: {response.text}"
    return response.json()["access_token"]


@pytest.fixture
def authorized_client(client: TestClient, auth_token: str):
    """Return headers with Bearer token for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}