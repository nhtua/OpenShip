"""Durable worker queue with fenced lease ownership and recovery.

Provides claim, renew, finalize, and reconcile_expired operations.
Uses FOR UPDATE SKIP LOCKED for race-free job claiming and fence-based
optimistic concurrency control for all status updates.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from ..config import settings
from .models import Run, RunJob


class ReleaseReason(Enum):
    """Reasons for releasing a lease."""
    EXPIRED_NOT_STARTED = "expired_not_started"
    EXPIRED_DURING_PROVIDER = "expired_during_provider"


@dataclass
class Claim:
    """A claim on a queued job."""
    run_id: uuid.UUID
    fence: int
    lease_until: datetime
    worker_id: str
    user_id: uuid.UUID = None

    @property
    def checkpoint_ns(self) -> str:
        """LangGraph checkpoint namespace for this claim."""
        return f"attempt-{self.fence}"


def _db_now():
    """Get database server time expression for comparisons in WHERE clauses."""
    return func.now()


def _py_now():
    """Get current UTC time for setting values."""
    return datetime.now(timezone.utc)


def claim_next(db: Session, worker_id: str, lease_seconds: int = None) -> Claim | None:
    """Claim the next available job from the queue.

    Uses FOR UPDATE SKIP LOCKED to safely claim jobs under concurrent
    worker access. Increments the fence on each claim for optimistic
    concurrency control.

    Args:
        db: Database session.
        worker_id: Unique identifier for the claiming worker.
        lease_seconds: Lease duration in seconds. Defaults to settings value.

    Returns:
        Claim object if a job was claimed, None if queue is empty.
    """
    if lease_seconds is None:
        lease_seconds = getattr(settings, "worker_lease_seconds", 30)

    # Find the oldest available job that isn't claimed, or has an expired lease
    # Run status is checked in the WHERE clause for atomicity
    # Use database time for expiration comparison
    job = (
        db.query(RunJob)
        .join(Run)
        .filter(and_(
            Run.status == "queued",
            or_(
                RunJob.lease_until.is_(None),
                RunJob.lease_until < _db_now(),
            ),
        ))
        .order_by(RunJob.available_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if not job:
        return None

    # Claim the job — transition run to running, increment fence
    run = db.query(Run).filter(Run.id == job.run_id).first()
    if not run:
        return None

    # Get the conversation to retrieve user_id
    from ..chat.models import Conversation
    conv = db.query(Conversation).filter(Conversation.id == run.conversation_id).first()
    user_id = conv.user_id if conv else None

    fence = job.fence + 1
    job.owner_id = worker_id
    job.fence = fence
    # Use Python time for setting values
    job.lease_until = _py_now() + timedelta(seconds=lease_seconds)
    run.status = "running"
    run.fence = fence
    db.commit()

    return Claim(
        run_id=job.run_id,
        fence=fence,
        lease_until=job.lease_until,
        worker_id=worker_id,
        user_id=user_id,
    )


def renew(db: Session, run_id: uuid.UUID, fence: int, lease_seconds: int = None) -> bool:
    """Renew a job's lease atomically.

    Uses UPDATE ... WHERE fence = :fence with returning to atomically check
    and update the lease, avoiding the read-check-write race.

    Args:
        db: Database session.
        run_id: ID of the run to renew.
        fence: Current fence value (must match).
        lease_seconds: New lease duration. Defaults to settings value.

    Returns:
        True if renewal succeeded, False if fence mismatch or job not found.
    """
    if lease_seconds is None:
        lease_seconds = getattr(settings, "worker_lease_seconds", 30)

    result = db.execute(
        update(RunJob)
        .where(and_(
            RunJob.run_id == run_id,
            RunJob.fence == fence,
        ))
        .values(lease_until=_py_now() + timedelta(seconds=lease_seconds))
        .returning(RunJob.id)
    )
    row = result.fetchone()
    db.commit()
    return row is not None


def finalize(db: Session, run_id: uuid.UUID, fence: int, result: dict) -> bool:
    """Finalize a job atomically.

    Uses UPDATE ... WHERE fence = :fence AND status = 'running' with returning
    to atomically check and update the run status.

    Args:
        db: Database session.
        run_id: ID of the run to finalize.
        fence: Current fence value (must match).
        result: Result data including status and output.

    Returns:
        True if finalization succeeded, False if fence mismatch or job not found.
    """
    status = result.get("status", "success")
    new_status = "completed" if status == "success" else "failed"

    values = {
        "status": new_status,
        "ended_at": _py_now(),
        "fence": fence,
    }
    if status != "success":
        values["error_code"] = result.get("error_code", "unknown_error")

    result_obj = db.execute(
        update(Run)
        .where(and_(
            Run.id == run_id,
            Run.fence == fence,
            Run.status == "running",
        ))
        .values(**values)
        .returning(Run.id)
    )
    row = result_obj.fetchone()
    db.commit()
    return row is not None


def reconcile_expired(db: Session) -> int:
    """Reconcile expired leases with a single commit.

    Jobs that expired without starting a provider call are requeued.
    Jobs that expired during a provider call are marked as failed
    with error code 'model_interrupted'.

    Uses database time (NOW()) for expiration comparison and commits
    all changes in a single transaction.

    Returns:
        Number of leases reclaimed.
    """
    # Find expired leases
    # Use database time for expiration comparison
    expired_jobs = (
        db.query(RunJob)
        .join(Run)
        .filter(and_(
            RunJob.lease_until.isnot(None),
            RunJob.lease_until < _db_now(),
            Run.status == "running",
        ))
        .with_for_update()
        .all()
    )

    reclaimed = 0
    for job in expired_jobs:
        run = db.query(Run).filter(Run.id == job.run_id).first()
        if not run:
            continue

        if run.provider_started_at is None:
            # Was not yet calling provider — requeue
            run.status = "queued"
            run.fence = job.fence
            job.owner_id = None
            job.lease_until = None
        else:
            # Was in-flight during provider call — mark as failed
            run.status = "failed"
            run.error_code = "model_interrupted"
            # Use Python time for setting values
            run.ended_at = _py_now()
            job.lease_until = None
            job.owner_id = None

        reclaimed += 1

    # Single commit for all changes
    if reclaimed > 0:
        db.commit()

    return reclaimed


def fenced_update_run(
    db: Session,
    run_id: uuid.UUID,
    fence: int,
    status: str = None,
    error_code: str = None,
    ended_at=None,
    provider_started_at=None,
    usage: str = None,
) -> bool:
    """Update a run with atomic fence-based optimistic concurrency control.

    Uses UPDATE ... WHERE fence = :fence with returning to atomically check
    and update, avoiding the read-check-write race.

    Args:
        db: Database session.
        run_id: ID of the run to update.
        fence: Expected fence value (must match).
        status: New status value.
        error_code: New error code.
        ended_at: New ended_at timestamp (use _db_now() for current time).
        provider_started_at: New provider_started_at timestamp (use _db_now() for current time).
        usage: New usage string.

    Returns:
        True if update succeeded, False if fence mismatch or run not found.
    """
    values = {}
    if status is not None:
        values["status"] = status
    if error_code is not None:
        values["error_code"] = error_code
    if ended_at is not None:
        values["ended_at"] = ended_at
    if provider_started_at is not None:
        values["provider_started_at"] = provider_started_at
    if usage is not None:
        values["usage"] = usage

    if not values:
        return True

    result = db.execute(
        update(Run)
        .where(and_(
            Run.id == run_id,
            Run.fence == fence,
        ))
        .values(**values)
        .returning(Run.id)
    )
    row = result.fetchone()
    db.commit()
    return row is not None


def mark_provider_started(db: Session, run_id: uuid.UUID, fence: int) -> bool:
    """Mark that a provider call has started.

    This is the boundary that determines recovery behavior:
    - Before this point, lease expiry requeues the job
    - After this point, lease expiry marks the job as failed

    Args:
        db: Database session.
        run_id: ID of the run.
        fence: Expected fence value (must match).

    Returns:
        True if successful, False on fence mismatch or not found.
    """
    return fenced_update_run(
        db=db,
        run_id=run_id,
        fence=fence,
        provider_started_at=_py_now(),
    )
