import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
        or "test_retention_pg" in test_path
        or "test_provider_pinning" in test_path
        or "test_phase4_execution_migration" in test_path
        or "test_tool_admission_postgres" in test_path
        or "test_tool_transcript_migration" in test_path
        or "test_workspace_migration_postgres" in test_path
        or "test_workspace_retention_postgres" in test_path
        or "test_tool_transcript_postgres" in test_path
        or "test_agent_tool_loop_postgres" in test_path
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
        check=False,
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

# --- Model profile configuration helpers (non-secret; credentials stay in bindings) ---

FIXTURE_ENDPOINT = "https://fixture.provider.test/v1"  # rayloc:ignore
FIXTURE_MODEL = "fixture-model"  # rayloc:ignore
CREDENTIAL_BINDING = "TEST_PROVIDER_CREDENTIAL"  # rayloc:ignore
SENTINEL_CREDENTIAL = "sentinel-credential-value"  # rayloc:ignore


def profile_spec(
    profile_id: str,
    *,
    label: str | None = None,
    allowed_fallbacks=(),
    endpoint: str | None = None,
    credential_ref: str | None = None,
    aliases=(),
):
    """A minimal text-only profile an operator would declare.

    The destination and the credential binding can be restated because a
    profile that points somewhere else, or needs no credential at all, is a
    shape the installation can declare. So can the aliases: a provider that
    answers under a name the operator qualified is a real shape, and the
    difference between the name a run asked for and the name that answered
    is a claim the installation has to keep.
    """
    return {
        "id": profile_id,
        "label": label or f"Fixture profile {profile_id}",
        "adapter": "openai",
        "model": FIXTURE_MODEL,
        "endpoint": endpoint or FIXTURE_ENDPOINT,
        "api_version": "chat-completions",
        "credential_ref": CREDENTIAL_BINDING if credential_ref is None else credential_ref,
        "capabilities": {
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": 32768,
            "max_output_tokens": 4096,
            "token_counting": False,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "allowed_fallback_profile_ids": list(allowed_fallbacks),
        "qualified_model_aliases": list(aliases),
        "adapter_options": {
            "adapter": "openai",
            "output_limit_parameter": "max_tokens",
        },
    }


def configure_profiles(monkeypatch, specs, default_id: str):
    """Declare the installation's profiles for one test."""
    import json

    monkeypatch.setattr("src.openship.config.settings.model_profiles_json", json.dumps(specs))
    monkeypatch.setattr("src.openship.config.settings.model_default_profile_id", default_id)
    monkeypatch.setenv(CREDENTIAL_BINDING, SENTINEL_CREDENTIAL)


def declare_two_profiles(monkeypatch) -> None:
    """Two profiles where the second is an allowed fallback of the first."""
    configure_profiles(
        monkeypatch,
        [
            profile_spec("primary-fixture", allowed_fallbacks=["secondary-fixture"]),
            profile_spec("secondary-fixture"),
        ],
        "primary-fixture",
    )


@pytest.fixture(autouse=True)
def configured_model_profiles(monkeypatch):
    """Tests declare a synthetic installation instead of relying on host settings."""
    declare_two_profiles(monkeypatch)


def execute_test_run(run_id: str) -> None:
    """Claim and execute one submitted turn with the real worker, without polling."""
    import uuid

    from src.openship.runs.models import RunJob
    from src.openship.runs.queue import claim_next
    from src.openship.runs.worker import Worker

    with TestSessionLocal() as session:
        # Other cases may intentionally leave queued turns after asserting on
        # submission. This helper executes only the turn its caller submitted.
        session.query(RunJob).filter(RunJob.run_id != uuid.UUID(run_id)).delete()
        session.commit()
        claim = claim_next(session, worker_id="test-worker", lease_seconds=30)
    assert claim is not None and str(claim.run_id) == run_id
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=TestSessionLocal,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim), "the worker did not publish a terminal result"
    finally:
        worker.engine.dispose()
