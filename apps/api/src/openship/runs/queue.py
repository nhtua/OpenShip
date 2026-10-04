"""Durable worker queue with fenced lease ownership and recovery.

Provides claim, renew, finalize, and reconcile_expired operations.
Uses FOR UPDATE SKIP LOCKED for race-free job claiming and fence-based
optimistic concurrency control for all status updates.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum

from sqlalchemy import and_, func
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


def _now() -> datetime:
    """Get current UTC time."""
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

    now = _now()

    # Find the oldest available job that isn't claimed
    job = (
        db.query(RunJob)
        .join(Run)
        .filter(and_(
            Run.status == "queued",
            RunJob.lease_until.is_(None),
        ))
        .order_by(RunJob.available_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if not job:
        # Try expired leases
        job = (
            db.query(RunJob)
            .join(Run)
            .filter(and_(
                Run.status == "queued",
                RunJob.lease_until.isnot(None),
                RunJob.lease_until < now,
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
    job.lease_until = now + timedelta(seconds=lease_seconds)
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
    """Renew a job's lease.

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

    job = (
        db.query(RunJob)
        .filter(and_(
            RunJob.run_id == run_id,
            RunJob.fence == fence,
        ))
        .first()
    )

    if not job:
        return False

    job.lease_until = _now() + timedelta(seconds=lease_seconds)
    db.commit()
    return True


def finalize(db: Session, run_id: uuid.UUID, fence: int, result: dict) -> bool:
    """Finalize a job, marking it as completed or failed.

    Args:
        db: Database session.
        run_id: ID of the run to finalize.
        fence: Current fence value (must match).
        result: Result data including status and output.

    Returns:
        True if finalization succeeded, False if fence mismatch or job not found.
    """
    job = (
        db.query(RunJob)
        .filter(and_(
            RunJob.run_id == run_id,
            RunJob.fence == fence,
        ))
        .first()
    )

    if not job:
        return False

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        return False

    status = result.get("status", "success")
    run.status = "completed" if status == "success" else "failed"
    run.ended_at = _now()
    run.fence = fence

    if status != "success":
        run.error_code = result.get("error_code", "unknown_error")

    db.commit()
    return True


def reconcile_expired(db: Session) -> int:
    """Reconcile expired leases.

    Jobs that expired without starting a provider call are requeued.
    Jobs that expired during a provider call are marked as failed
    with error code 'model_interrupted'.

    Returns:
        Number of leases reclaimed.
    """
    now = _now()

    # Find expired leases
    expired_jobs = (
        db.query(RunJob)
        .join(Run)
        .filter(and_(
            RunJob.lease_until.isnot(None),
            RunJob.lease_until < now,
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
            db.commit()
        else:
            # Was in-flight during provider call — mark as failed
            run.status = "failed"
            run.error_code = "model_interrupted"
            run.ended_at = now
            job.lease_until = None
            job.owner_id = None
            db.commit()

        reclaimed += 1

    return reclaimed


def fenced_update_run(
    db: Session,
    run_id: uuid.UUID,
    fence: int,
    status: str = None,
    error_code: str = None,
    ended_at: datetime = None,
    provider_started_at: datetime = None,
    usage: str = None,
) -> bool:
    """Update a run with fence-based optimistic concurrency control.

    Args:
        db: Database session.
        run_id: ID of the run to update.
        fence: Expected fence value (must match).
        status: New status value.
        error_code: New error code.
        ended_at: New ended_at timestamp.
        provider_started_at: New provider_started_at timestamp.
        usage: New usage string.

    Returns:
        True if update succeeded, False if fence mismatch or run not found.
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        return False

    if run.fence != fence:
        return False

    if status is not None:
        run.status = status
    if error_code is not None:
        run.error_code = error_code
    if ended_at is not None:
        run.ended_at = ended_at
    if provider_started_at is not None:
        run.provider_started_at = provider_started_at
    if usage is not None:
        run.usage = usage

    db.commit()
    return True


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
        provider_started_at=_now(),
    )
