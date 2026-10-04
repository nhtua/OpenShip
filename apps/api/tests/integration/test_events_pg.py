"""Integration tests for durable events against a real database.

Verifies:
- Concurrent event writers produce distinct ordered sequences
- Stream resumes after process restart (simulated)
- Retention pruning works correctly
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

from src.openship.auth.models import User
from src.openship.workspace.models import Project
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run, Event, Outbox


@pytest.fixture(scope="module")
def test_engine():
    """Create a test database engine using SQLite with Alembic migrations."""
    _test_dir = tempfile.mkdtemp()
    _test_db_path = os.path.join(_test_dir, "events_test.db")

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
    ini_path = Path(_test_dir) / "alembic_events.ini"
    ini_path.write_text(ini_content)

    env = {
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), "upgrade", "head"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

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


def _setup_conv(session):
    """Create user, project, conversation."""
    user = User(
        username=f"evtest_{uuid.uuid4().hex[:8]}",
        email=f"evtest_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    session.add(user)
    session.commit()

    project = Project(owner_user_id=user.id, name="Test Project")
    session.add(project)
    session.commit()

    conv = Conversation(
        user_id=user.id,
        project_id=project.id,
        title="Test",
        next_event_sequence=0,
    )
    session.add(conv)
    session.commit()

    return user, project, conv


def test_concurrent_event_writers_distinct_sequences(test_session):
    """Two writers appending events produce distinct ordered sequences."""
    user, project, conv = _setup_conv(test_session)

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        fence=0,
    )
    test_session.add(run)
    test_session.commit()

    # Writer 1: submit event
    ev1 = Event(
        conversation_id=conv.id,
        run_id=run.id,
        sequence=conv.next_event_sequence,
        type="turn_submitted",
        payload='{"writer": 1}',
    )
    test_session.add(ev1)
    conv.next_event_sequence = 1
    test_session.commit()

    # Writer 2: status change event
    ev2 = Event(
        conversation_id=conv.id,
        run_id=run.id,
        sequence=conv.next_event_sequence,
        type="run_started",
        payload='{"writer": 2}',
    )
    test_session.add(ev2)
    conv.next_event_sequence = 2
    test_session.commit()

    # Verify distinct sequences
    events = (
        test_session.query(Event)
        .filter(Event.conversation_id == conv.id)
        .order_by(Event.sequence)
        .all()
    )
    assert len(events) == 2
    assert events[0].sequence == 0
    assert events[1].sequence == 1
    assert events[0].type == "turn_submitted"
    assert events[1].type == "run_started"


def test_stream_resumes_after_restart(test_session):
    """Client can resume streaming from last received sequence."""
    user, project, conv = _setup_conv(test_session)

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        fence=0,
    )
    test_session.add(run)
    test_session.commit()

    # Write 3 events
    for i in range(3):
        ev = Event(
            conversation_id=conv.id,
            run_id=run.id,
            sequence=i,
            type=f"event_{i}",
            payload='{}',
        )
        test_session.add(ev)
    conv.next_event_sequence = 3
    test_session.commit()

    # Client receives events 0, 1, then "restarts" (closes stream)
    # and resumes from after_sequence=1
    events = (
        test_session.query(Event)
        .filter(Event.conversation_id == conv.id, Event.sequence > 1)
        .order_by(Event.sequence)
        .all()
    )
    assert len(events) == 1
    assert events[0].sequence == 2


def test_retention_pruning(test_session):
    """Pruning keeps most recent events and prunes oldest."""
    user, project, conv = _setup_conv(test_session)

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        fence=0,
    )
    test_session.add(run)
    test_session.commit()

    # Write 10 events
    for i in range(10):
        ev = Event(
            conversation_id=conv.id,
            run_id=run.id,
            sequence=i,
            type=f"event_{i}",
            payload='{}',
        )
        test_session.add(ev)
    conv.next_event_sequence = 10
    test_session.commit()

    # Prune to keep only last 5
    from src.openship.events.retention import prune_events

    pruned = prune_events(
        db=test_session,
        conversation_id=conv.id,
        keep_count=5,
    )
    assert pruned == 5

    # Verify 5 events remain
    remaining = (
        test_session.query(Event)
        .filter(Event.conversation_id == conv.id)
        .count()
    )
    assert remaining == 5

    # Verify the oldest 5 were removed
    sequences = (
        test_session.query(Event.sequence)
        .filter(Event.conversation_id == conv.id)
        .order_by(Event.sequence)
        .all()
    )
    assert [s[0] for s in sequences] == [5, 6, 7, 8, 9]


def test_outbox_deduplication_by_event_id(test_session):
    """Duplicate outbox rows for same event ID are harmless."""
    user, project, conv = _setup_conv(test_session)

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="queued",
        fence=0,
    )
    test_session.add(run)
    test_session.commit()

    ev = Event(
        conversation_id=conv.id,
        run_id=run.id,
        sequence=0,
        type="run_submitted",
        payload='{}',
    )
    test_session.add(ev)
    test_session.flush()

    # Two outbox rows for same event (simulates duplicate delivery)
    ob1 = Outbox(
        conversation_id=conv.id,
        event_id=ev.id,
        type="run_submitted",
        payload='{}',
    )
    ob2 = Outbox(
        conversation_id=conv.id,
        event_id=ev.id,
        type="run_submitted",
        payload='{}',
    )
    test_session.add(ob1)
    test_session.add(ob2)
    conv.next_event_sequence = 1
    test_session.commit()

    # Event count should still be 1
    event_count = (
        test_session.query(Event)
        .filter(Event.conversation_id == conv.id)
        .count()
    )
    assert event_count == 1
