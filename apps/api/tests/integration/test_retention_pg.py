"""Integration tests for retention with PostgreSQL.

Tests retention policy behavior with actual database constraints:
- Terminal run cleanup
- Checkpoint deletion
- Event pruning
- Retention metrics accuracy
"""

import os
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import Base, User
from src.openship.chat.models import Conversation
from src.openship.workspace.models import Project
from src.openship.runs.models import Run, RunJob, Event, Outbox
from src.openship.events.retention import dry_run_retention, run_retention


def get_pg_url():
    """Get PostgreSQL URL for integration tests."""
    return os.environ.get(
        "PG_INTEGRATION_URL",
        "postgresql+psycopg2://postgres:change_me@localhost:5432/openship_test",
    )


@pytest.fixture(scope="module")
def pg_engine():
    """Create PostgreSQL engine for integration tests."""
    url = get_pg_url()
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def pg_session(pg_engine):
    """Create PostgreSQL session with fresh schema."""
    Session = sessionmaker(bind=pg_engine)
    session = Session()

    # Drop and recreate schema
    session.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
    session.execute(text("CREATE SCHEMA public"))
    session.commit()

    # Create all tables
    Base.metadata.create_all(pg_engine)

    yield session
    session.close()


def test_pg_retention_dry_run_empty(pg_session):
    """Dry-run returns zero eligible when no data exists."""
    result = dry_run_retention(pg_session, dry_run=True)
    assert result["eligible_events"] == 0
    assert result["eligible_checkpoints"] == 0
    assert result["eligible_runs"] == 0


def test_pg_retention_counts_eligible(pg_session):
    """Dry-run correctly counts eligible terminal runs."""
    # Create user, project, conversation
    user = User(username="test5", email="test5@example.com", password_hash="test-hash")
    pg_session.add(user)
    pg_session.flush()

    project = Project(name="Test Project 5", owner_user_id=user.id)
    pg_session.add(project)
    pg_session.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    pg_session.add(conversation)
    pg_session.flush()

    # Create old completed run
    old_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="completed",
        fence=1,
        ended_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(old_run)
    pg_session.flush()

    # Create old event
    old_event = Event(
        conversation_id=conversation.id,
        run_id=old_run.id,
        sequence=1,
        type="run_completed",
        payload='{}',
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(old_event)
    pg_session.commit()

    # Dry run should find eligible items
    result = dry_run_retention(pg_session, dry_run=True)
    assert result["eligible_events"] >= 1
    assert result["eligible_checkpoints"] >= 1
    assert result["eligible_runs"] >= 1


def test_pg_retention_prunes_terminal_data(pg_session):
    """Retention prunes eligible terminal data."""
    # Create user, project, conversation
    user = User(username="test6", email="test6@example.com", password_hash="test-hash")
    pg_session.add(user)
    pg_session.flush()

    project = Project(name="Test Project 6", owner_user_id=user.id)
    pg_session.add(project)
    pg_session.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    pg_session.add(conversation)
    pg_session.flush()

    # Create old completed run
    old_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="completed",
        fence=1,
        ended_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(old_run)
    pg_session.flush()

    # Create old event
    old_event = Event(
        conversation_id=conversation.id,
        run_id=old_run.id,
        sequence=1,
        type="run_completed",
        payload='{}',
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(old_event)

    # Create recent event (should not be pruned)
    recent_event = Event(
        conversation_id=conversation.id,
        run_id=old_run.id,
        sequence=2,
        type="run_completed",
        payload='{}',
        created_at=datetime.now(timezone.utc),
    )
    pg_session.add(recent_event)
    pg_session.commit()

    # Run retention
    result = run_retention(pg_session, event_retention_days=30, checkpoint_retention_days=30, dry_run=False)
    assert result["pruned_events"] >= 1

    # Old event should be gone
    old = pg_session.query(Event).filter(Event.id == old_event.id).first()
    assert old is None

    # Recent event should still exist
    recent = pg_session.query(Event).filter(Event.id == recent_event.id).first()
    assert recent is not None


def test_pg_retention_keeps_active_runs(pg_session):
    """Retention keeps active and queued runs regardless of age."""
    # Create user, project, conversation
    user = User(username="test7", email="test7@example.com", password_hash="test-hash")
    pg_session.add(user)
    pg_session.flush()

    project = Project(name="Test Project 7", owner_user_id=user.id)
    pg_session.add(project)
    pg_session.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    pg_session.add(conversation)
    pg_session.flush()

    # Create old queued run
    queued_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="queued",
        fence=0,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(queued_run)

    # Create old running run
    running_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        fence=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(running_run)
    pg_session.commit()

    # Run retention
    run_retention(pg_session, event_retention_days=30, checkpoint_retention_days=30, dry_run=False)

    # Queued and running runs should still exist
    queued = pg_session.query(Run).filter(Run.id == queued_run.id).first()
    running = pg_session.query(Run).filter(Run.id == running_run.id).first()
    assert queued is not None
    assert running is not None


def test_pg_retention_preserves_messages(pg_session):
    """Retention preserves messages and run summaries."""
    from src.openship.chat.models import Message

    # Create user, project, conversation
    user = User(username="test8", email="test8@example.com", password_hash="test-hash")
    pg_session.add(user)
    pg_session.flush()

    project = Project(name="Test Project 8", owner_user_id=user.id)
    pg_session.add(project)
    pg_session.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    pg_session.add(conversation)
    pg_session.flush()

    # Create old completed run
    old_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="completed",
        fence=1,
        ended_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    pg_session.add(old_run)
    pg_session.flush()

    # Create old messages
    old_msg = Message(
        conversation_id=conversation.id,
        run_id=old_run.id,
        role="user",
        content="Test message",
    )
    pg_session.add(old_msg)
    pg_session.commit()

    # Run retention
    run_retention(pg_session, event_retention_days=30, checkpoint_retention_days=30, dry_run=False)

    # Message should still exist
    msg = pg_session.query(Message).filter(Message.id == old_msg.id).first()
    assert msg is not None

    # Run should still exist
    run = pg_session.query(Run).filter(Run.id == old_run.id).first()
    assert run is not None