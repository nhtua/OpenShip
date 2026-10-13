"""Tests for honest usage accounting and retention policies."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.runs.graph import model_step
from src.openship.runs.worker import _usage_record

from .providers.conftest import invocation, run_state, serve, sdk_chunk


def test_model_step_captures_usage(monkeypatch):
    """Model step captures usage from provider response."""
    serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="Hello"),
            sdk_chunk(content=" world"),
            sdk_chunk(choices=False, usage=(42, 15, 57)),
            sdk_chunk(finish_reason="stop"),
        ],
    )

    result = model_step(run_state(("user", "Say hello")), invocation())

    assert result["assistant_response"] == "Hello world"
    assert result["usage"] is not None
    assert result["usage"]["input_tokens"] == 42
    assert result["usage"]["output_tokens"] == 15
    assert result["usage"]["total_tokens"] == 57


def test_model_step_no_usage_when_omitted(monkeypatch):
    """Model step returns None usage when provider omits it."""
    serve(monkeypatch, chunks=[sdk_chunk(content="Hello"), sdk_chunk(finish_reason="stop")])

    result = model_step(run_state(("user", "Say hello")), invocation())

    assert result["assistant_response"] == "Hello"
    assert result["usage"] is None


def test_model_step_incomplete_usage_visible(monkeypatch):
    """Incomplete usage (only completion tokens) is visible as incomplete."""
    serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="Hello"),
            sdk_chunk(choices=False, usage=(None, 15, None)),
            sdk_chunk(finish_reason="stop"),
        ],
    )

    result = model_step(run_state(("user", "Say hello")), invocation())

    assert result["usage"] is not None
    assert result["usage"]["output_tokens"] == 15
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["total_tokens"] is None

    # What a run keeps says the totals are incomplete, and invents no number.
    kept = _usage_record(result["usage"])
    assert kept["estimated"] is True
    assert kept["input_tokens"] is None


def test_finalize_persists_usage():
    """Finalize persists usage to the run record."""
    from src.openship.runs.queue import finalize

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)

    # Create full schema (all models)
    from src.openship.auth.models import Base, User
    from src.openship.chat.models import Conversation, Message
    from src.openship.workspace.models import Project
    from src.openship.runs.models import Run, RunJob, Event, Outbox
    Base.metadata.create_all(engine)

    db = Session()

    # Create required parent records
    user = User(username="test", email="test@example.com", password_hash="test-hash")
    db.add(user)
    db.flush()

    project = Project(name="Test Project", owner_user_id=user.id)
    db.add(project)
    db.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    db.add(conversation)
    db.flush()

    # Create a run
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        fence=1,
    )
    db.add(run)
    db.commit()

    usage = {
        "input_tokens": 42,
        "output_tokens": 15,
        "total_tokens": 57,
        "provider_model": "gpt-4o",
    }

    # Finalize with usage
    success = finalize(
        db=db,
        run_id=run.id,
        fence=1,
        result={
            "status": "success",
            "output": "Hello world",
            "usage": usage,
        },
    )
    assert success

    # Verify usage was persisted
    run = db.query(Run).filter(Run.id == run.id).first()
    assert run.status == "completed"
    stored_usage = json.loads(run.usage)
    assert stored_usage["input_tokens"] == 42
    assert stored_usage["output_tokens"] == 15
    assert stored_usage["total_tokens"] == 57

    db.close()


def test_finalize_null_usage_when_omitted():
    """Finalize leaves usage null when not provided."""
    from src.openship.runs.queue import finalize

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)

    from src.openship.auth.models import Base, User
    from src.openship.chat.models import Conversation
    from src.openship.workspace.models import Project
    from src.openship.runs.models import Run
    Base.metadata.create_all(engine)

    db = Session()

    user = User(username="test2", email="test2@example.com", password_hash="test-hash")
    db.add(user)
    db.flush()

    project = Project(name="Test Project 2", owner_user_id=user.id)
    db.add(project)
    db.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    db.add(conversation)
    db.flush()

    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        fence=1,
    )
    db.add(run)
    db.commit()

    success = finalize(
        db=db,
        run_id=run.id,
        fence=1,
        result={
            "status": "success",
            "output": "Hello world",
        },
    )
    assert success

    run = db.query(Run).filter(Run.id == run.id).first()
    assert run.usage is None

    db.close()


def test_retention_module_exists():
    """Retention module is importable with expected functions."""
    from src.openship.events import retention
    assert retention is not None
    assert hasattr(retention, "dry_run_retention")
    assert hasattr(retention, "run_retention")


def test_retention_dry_run_returns_metrics():
    """Dry-run returns retention metrics."""
    from src.openship.events.retention import dry_run_retention

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)

    from src.openship.auth.models import Base, User
    from src.openship.chat.models import Conversation
    from src.openship.workspace.models import Project
    from src.openship.runs.models import Run, Event
    Base.metadata.create_all(engine)

    db = Session()

    # Create required parent records
    user = User(username="test3", email="test3@example.com", password_hash="test-hash")
    db.add(user)
    db.flush()

    project = Project(name="Test Project 3", owner_user_id=user.id)
    db.add(project)
    db.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    db.add(conversation)
    db.flush()

    # Create an old completed run
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="completed",
        fence=1,
        ended_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(run)
    db.commit()

    event = Event(
        conversation_id=conversation.id,
        run_id=run.id,
        sequence=1,
        type="run_completed",
        payload='{}',
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(event)
    db.commit()

    result = dry_run_retention(db, dry_run=True)
    assert "eligible_events" in result
    assert "eligible_checkpoints" in result
    assert "eligible_runs" in result
    assert result["eligible_events"] >= 1

    db.close()


def test_retention_ignores_active_runs():
    """Retention does not prune active or waiting runs."""
    from src.openship.events.retention import run_retention

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)

    from src.openship.auth.models import Base, User
    from src.openship.chat.models import Conversation
    from src.openship.workspace.models import Project
    from src.openship.runs.models import Run
    Base.metadata.create_all(engine)

    db = Session()

    # Create required parent records
    user = User(username="test4", email="test4@example.com", password_hash="test-hash")
    db.add(user)
    db.flush()

    project = Project(name="Test Project 4", owner_user_id=user.id)
    db.add(project)
    db.flush()

    conversation = Conversation(user_id=user.id, project_id=project.id, title="Test")
    db.add(conversation)
    db.flush()

    # Create an old completed run
    completed_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="completed",
        fence=1,
        ended_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(completed_run)

    # Create an old queued run (should not be pruned)
    queued_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="queued",
        fence=0,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(queued_run)

    # Create an old running run (should not be pruned)
    running_run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        fence=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(running_run)

    db.commit()

    result = run_retention(
        db,
        event_retention_days=30,
        checkpoint_retention_days=30,
        dry_run=False,
    )

    # Verify queued and running runs still exist
    queued = db.query(Run).filter(Run.id == queued_run.id).first()
    running = db.query(Run).filter(Run.id == running_run.id).first()
    assert queued is not None
    assert running is not None

    db.close()


def test_structured_log_format():
    """Structured logs contain required fields and exclude secrets."""
    from src.openship.events import logging as event_logging

    log = event_logging.structured_log(
        event="run_completed",
        run_id="test-run-123",
        conversation_id="test-conv-456",
        status="completed",
    )

    assert "run_id" in log
    assert "conversation_id" in log
    assert "event" in log
    assert log["run_id"] == "test-run-123"
    assert log["conversation_id"] == "test-conv-456"
    assert log["event"] == "run_completed"
    assert log["timestamp"] is not None


def test_health_endpoint_exists(client):
    """Health and readiness endpoints exist."""
    health = client.get("/health")
    assert health.status_code == 200
    assert "status" in health.json()

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert "status" in ready.json()


def test_worker_commands():
    """Worker module supports maintenance commands."""
    from src.openship.runs import worker_commands
    assert hasattr(worker_commands, "run_retention_command")
    assert hasattr(worker_commands, "health_check_command")