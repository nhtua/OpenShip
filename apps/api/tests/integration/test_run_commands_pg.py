"""Integration tests for run commands against a real database.

These tests verify the database-level constraints that enforce durable run semantics:
- One active run per conversation (partial unique index)
- Event sequence uniqueness
- No orphan records on failed transactions
"""

import uuid
import tempfile
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import Base, User
from src.openship.workspace.models import Project
from src.openship.chat.models import Conversation, Message
from src.openship.runs.models import Run, RunJob, Event, Outbox


@pytest.fixture(scope="module")
def test_engine():
    """Create a test database engine using SQLite with Alembic migrations."""
    _test_dir = tempfile.mkdtemp()
    _test_db_path = os.path.join(_test_dir, "run_commands_test.db")

    # Run Alembic migrations to set up the schema
    script_dir = Path(__file__).parents[2] / "alembic"
    ini_content = f"""[alembic]
script_location = {script_dir}
sqlalchemy.url = sqlite:///{_test_db_path}

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
    ini_path = Path(_test_dir) / "alembic_test_pg.ini"
    ini_path.write_text(ini_content)

    env = {
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), "upgrade", "head"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

    # Enable foreign keys for SQLite
    import sqlite3
    def connect():
        conn = sqlite3.connect(_test_db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    engine = create_engine(f"sqlite:///{_test_db_path}", creator=connect, echo=False)
    yield engine
    engine.dispose()


@pytest.fixture
def test_session(test_engine):
    """Provide a test database session."""
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


def test_one_active_run_per_conversation(test_session):
    """Only one active (non-terminal) run can exist per conversation."""
    user = User(username=f"run_test_{uuid.uuid4().hex[:8]}", email=f"run_test_{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Test Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Test")
    test_session.add(conv)
    test_session.commit()

    # First active run should succeed
    run1 = Run(conversation_id=conv.id, project_id=project.id, status="queued")
    test_session.add(run1)
    test_session.commit()

    # Second active run on same conversation should violate unique index
    run2 = Run(conversation_id=conv.id, project_id=project.id, status="running")
    test_session.add(run2)

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()

    # Delete the first run to test the completed+queued case
    test_session.delete(run1)
    test_session.commit()

    # A terminal status run is allowed alongside a new active run
    run_completed = Run(conversation_id=conv.id, project_id=project.id, status="completed")
    test_session.add(run_completed)
    test_session.commit()

    run_queued = Run(conversation_id=conv.id, project_id=project.id, status="queued")
    test_session.add(run_queued)
    test_session.commit()  # Should not raise


def test_event_sequence_uniqueness(test_session):
    """Event sequences must be unique per conversation."""
    user = User(username=f"event_test_{uuid.uuid4().hex[:8]}", email=f"event_test_{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Test Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Test")
    test_session.add(conv)
    test_session.commit()

    run = Run(conversation_id=conv.id, project_id=project.id, status="queued")
    test_session.add(run)
    test_session.commit()

    # First event at sequence 0
    event1 = Event(conversation_id=conv.id, run_id=run.id, sequence=0, type="turn_submitted", payload="{}")
    test_session.add(event1)
    test_session.commit()

    # Duplicate sequence should fail
    event2 = Event(conversation_id=conv.id, run_id=run.id, sequence=0, type="turn_submitted", payload="{}")
    test_session.add(event2)

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        test_session.commit()


def test_failed_transaction_leaves_no_orphans(test_session):
    """If any part of submit_turn fails, no orphan records should remain."""
    user = User(username=f"orphan_test_{uuid.uuid4().hex[:8]}", email=f"orphan_test_{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Test Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Test")
    test_session.add(conv)
    test_session.commit()

    # Attempt to create a run with invalid data that violates FK
    bad_run = Run(conversation_id=conv.id, project_id=uuid.uuid4(), status="queued")
    test_session.add(bad_run)

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        test_session.commit()

    test_session.rollback()

    # Verify no orphan run was created
    run_count = test_session.query(Run).filter(Run.conversation_id == conv.id).count()
    assert run_count == 0


def test_concurrent_sends_one_succeeds_one_conflicts(test_session):
    """Two submit_turn calls for the same conversation: one succeeds, one conflicts.

    Tests the database-enforced one-active-run-per-conversation constraint. When two
    requests arrive concurrently, one will create the run (202) and the other will
    hit the unique index violation (409). Both use different client_request_ids so
    neither is an idempotent replay of the other.
    """
    user = User(username=f"concurrent_test_{uuid.uuid4().hex[:8]}", email=f"concurrent_test_{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Test Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Test")
    test_session.add(conv)
    test_session.commit()

    # First submit_turn — should succeed
    req1_id = str(uuid.uuid4())
    run1 = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        client_request_id=req1_id,
    )
    test_session.add(run1)
    test_session.commit()

    # Second submit_turn (concurrent scenario) — should hit unique index violation
    req2_id = str(uuid.uuid4())
    run2 = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        client_request_id=req2_id,
    )
    test_session.add(run2)

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError) as exc_info:
        test_session.commit()
    test_session.rollback()
    # SQLite reports "UNIQUE constraint failed" without index name
    assert "UNIQUE constraint failed" in str(exc_info.value), (
        f"Expected unique index violation, got: {exc_info.value}"
    )
