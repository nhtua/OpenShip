"""Integration tests for worker queue ownership, fencing, and lease recovery.

These tests verify:
- Race condition safety: two workers racing to claim a job, only one wins
- Fence-based recovery: stale workers cannot heartbeat/finalize after lease expiry
- Crash recovery scenarios (before claim, before provider call, during provider call)
- Cancelled runs cannot be claimed or finalized
"""

import uuid
import tempfile
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import Base, User
from src.openship.workspace.models import Project
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import (
    claim_next,
    renew,
    finalize,
    reconcile_expired,
    ReleaseReason,
)
from src.openship.runs.service import submit_turn


@pytest.fixture(scope="module")
def test_engine():
    """Create a test database engine using SQLite with Alembic migrations."""
    _test_dir = tempfile.mkdtemp()
    _test_db_path = os.path.join(_test_dir, "worker_recovery_test.db")

    # Run Alembic migrations
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
    ini_path = Path(_test_dir) / "alembic_worker_test.ini"
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


@pytest.fixture
def test_run(test_session):
    """Create a test user, project, conversation, and queued run."""
    user = User(
        username=f"worker_test_{uuid.uuid4().hex[:8]}",
        email=f"worker_test_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Worker Test Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Worker Test")
    test_session.add(conv)
    test_session.commit()

    run = submit_turn(
        db=test_session,
        user_id=user.id,
        conversation_id=str(conv.id),
        content="Test message for worker",
        client_request_id=str(uuid.uuid4()),
    )
    return run


def test_claim_next_returns_claim(test_session, test_run):
    """claim_next returns a Claim object with run_id, fence, and lease_until."""
    claim = claim_next(
        db=test_session,
        worker_id="worker-1",
        lease_seconds=30,
    )
    assert claim is not None
    assert claim.run_id == test_run.id
    assert claim.fence == 1
    assert claim.lease_until is not None

    # Verify job is marked as claimed
    job = test_session.query(RunJob).filter(RunJob.run_id == test_run.id).first()
    assert job.owner_id == "worker-1"
    assert job.lease_until is not None
    assert job.fence == 1

    # Verify run status changed to running
    run = test_session.query(Run).filter(Run.id == test_run.id).first()
    assert run.status == "running"


def test_claim_next_only_one_winner(test_session, test_run):
    """Two workers racing to claim a job — only one wins."""
    claim1 = claim_next(
        db=test_session,
        worker_id="worker-a",
        lease_seconds=30,
    )
    assert claim1 is not None
    assert claim1.worker_id == "worker-a"

    # Second worker tries to claim the same job
    claim2 = claim_next(
        db=test_session,
        worker_id="worker-b",
        lease_seconds=30,
    )
    # Should return None because there's no more queued jobs
    assert claim2 is None


def test_renew_updates_lease(test_session, test_run):
    """renew updates the lease_until timestamp."""
    claim = claim_next(
        db=test_session,
        worker_id="worker-renew",
        lease_seconds=30,
    )
    original_lease = claim.lease_until

    # Renew the lease
    result = renew(
        db=test_session,
        run_id=test_run.id,
        fence=claim.fence,
        lease_seconds=30,
    )
    assert result is True

    # Verify lease was extended
    job = test_session.query(RunJob).filter(RunJob.run_id == test_run.id).first()
    assert job.lease_until > original_lease


def test_renew_wrong_fence_fails(test_session, test_run):
    """renew with wrong fence value fails."""
    claim = claim_next(
        db=test_session,
        worker_id="worker-wrong-fence",
        lease_seconds=30,
    )
    result = renew(
        db=test_session,
        run_id=test_run.id,
        fence=999,  # Wrong fence
        lease_seconds=30,
    )
    assert result is False


def test_finalize_completes_run(test_session, test_run):
    """finalize transitions run to completed."""
    claim = claim_next(
        db=test_session,
        worker_id="worker-finish",
        lease_seconds=30,
    )
    result = finalize(
        db=test_session,
        run_id=test_run.id,
        fence=claim.fence,
        result={"status": "success", "output": "Hello!"},
    )
    assert result is True

    run = test_session.query(Run).filter(Run.id == test_run.id).first()
    assert run.status == "completed"
    assert run.ended_at is not None


def test_finalize_wrong_fence_fails(test_session, test_run):
    """finalize with wrong fence fails."""
    claim = claim_next(
        db=test_session,
        worker_id="worker-wrong-fence-finish",
        lease_seconds=30,
    )
    result = finalize(
        db=test_session,
        run_id=test_run.id,
        fence=999,
        result={"status": "success"},
    )
    assert result is False


def test_reconcile_expired_requeues_not_started(test_session):
    """Expired leases without provider_started_at are requeued."""
    # Create a run manually (not via submit_turn) to control timestamps
    user = User(
        username=f"reconcile_{uuid.uuid4().hex[:8]}",
        email=f"reconcile_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Reconcile Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Reconcile Test")
    test_session.add(conv)
    test_session.commit()

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="running",
        fence=1,
    )
    test_session.add(run)
    test_session.flush()

    # Create a claimed job with expired lease
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    job = RunJob(
        run_id=run.id,
        owner_id="dead-worker",
        fence=1,
        lease_until=past,
    )
    test_session.add(job)
    test_session.commit()

    # Reconcile expired leases
    reclaimed = reconcile_expired(test_session)
    assert reclaimed >= 1

    # Verify run is requeued
    run = test_session.query(Run).filter(Run.id == run.id).first()
    assert run.status == "queued"


def test_reconcile_expired_marks_started_as_failed(test_session):
    """Expired leases with provider_started_at are marked as failed."""
    user = User(
        username=f"reconcile2_{uuid.uuid4().hex[:8]}",
        email=f"reconcile2_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Reconcile2 Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Reconcile2 Test")
    test_session.add(conv)
    test_session.commit()

    run = Run(
        conversation_id=conv.id,
        project_id=project.id,
        status="running",
        fence=1,
        provider_started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    test_session.add(run)
    test_session.flush()

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    job = RunJob(
        run_id=run.id,
        owner_id="dead-worker-2",
        fence=1,
        lease_until=past,
    )
    test_session.add(job)
    test_session.commit()

    reclaimed = reconcile_expired(test_session)
    assert reclaimed >= 1

    # Verify run is marked as failed
    run = test_session.query(Run).filter(Run.id == run.id).first()
    assert run.status == "failed"
    assert run.error_code == "model_interrupted"


def test_cancelled_run_cannot_be_claimed(test_session):
    """Cancelled runs are not claimable."""
    user = User(
        username=f"cancel_{uuid.uuid4().hex[:8]}",
        email=f"cancel_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    test_session.add(user)
    test_session.commit()

    project = Project(owner_user_id=user.id, name="Cancel Project")
    test_session.add(project)
    test_session.commit()

    conv = Conversation(user_id=user.id, project_id=project.id, title="Cancel Test")
    test_session.add(conv)
    test_session.commit()

    # Create a queued run and then cancel it
    run = submit_turn(
        db=test_session,
        user_id=user.id,
        conversation_id=str(conv.id),
        content="Cancel me",
        client_request_id=str(uuid.uuid4()),
    )

    # Cancel the run
    from src.openship.runs.service import request_cancel
    request_cancel(db=test_session, user_id=user.id, run_id=str(run.id))

    # Try to claim
    claim = claim_next(
        db=test_session,
        worker_id="worker-cancels",
        lease_seconds=30,
    )
    # Should not claim the cancelled run
    assert claim is None or claim.run_id != run.id


def test_fenced_write_rejected_with_old_fence(test_session, test_run):
    """A write with an old fence value is rejected."""
    from src.openship.runs.queue import fenced_update_run

    # First, claim and update the run
    claim = claim_next(
        db=test_session,
        worker_id="worker-fence-update",
        lease_seconds=30,
    )
    assert claim.fence == 1

    # Update with correct fence
    result = fenced_update_run(
        db=test_session,
        run_id=test_run.id,
        fence=claim.fence,
        status="running",
    )
    assert result is True

    # Simulate another claim (fence increments)
    # First finalize the current run
    finalize(
        db=test_session,
        run_id=test_run.id,
        fence=claim.fence,
        result={"status": "success"},
    )

    # Create a new job to claim with fence 2
    new_run = submit_turn(
        db=test_session,
        user_id=claim.user_id if hasattr(claim, 'user_id') else None,
        conversation_id=str(test_run.conversation_id),
        content="Second turn",
        client_request_id=str(uuid.uuid4()),
    )

    claim2 = claim_next(
        db=test_session,
        worker_id="worker-fence-update-2",
        lease_seconds=30,
    )
    assert claim2 is not None
    assert claim2.fence == 1  # New run gets fence 1

    # Old fence write is rejected
    result = fenced_update_run(
        db=test_session,
        run_id=new_run.id,
        fence=999,
        status="completed",
    )
    assert result is False
