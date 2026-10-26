"""Durable worker queue with fenced lease ownership and recovery.

Provides claim, renew, finalize, and reconcile_expired operations.
Uses FOR UPDATE SKIP LOCKED for race-free job claiming and fence-based
optimistic concurrency control for all status updates.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..config import settings
from .models import Outbox, Run, RunJob


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


@dataclass(frozen=True)
class ClaimDecision:
    """One exact command's durable acquisition decision."""

    kind: str
    claim: Claim | None = None
    delay: float | None = None
    code: str | None = None


def claim_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    generation: int,
    epoch: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
) -> ClaimDecision:
    """Acquire only the specified run's current command generation."""
    from ..messaging.models import MessageTransportState

    state = db.get(MessageTransportState, 1)
    if state is None:
        return ClaimDecision("disposed", code="transport_missing")
    message_id = uuid.uuid5(
        state.installation_id, f"run.execute:{run_id}:{generation}:{epoch}"
    )
    decision = _claim_run_exact(
        db,
        run_id=run_id,
        generation=generation,
        epoch=epoch,
        message_id=message_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    db.commit()
    return decision


def _claim_run_exact(
    db: Session,
    *,
    run_id: uuid.UUID,
    generation: int,
    epoch: uuid.UUID,
    message_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
    conversation_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    payload_digest: str | None = None,
) -> ClaimDecision:
    """Validate persisted identity and acquire under one transaction."""
    from ..messaging.models import MessageTransportState

    if (
        not isinstance(generation, int)
        or generation < 1
        or lease_seconds < 1
        or not worker_id
        or len(worker_id) > 50
    ):
        return ClaimDecision("disposed", code="invalid_command")
    observed_state = db.get(MessageTransportState, 1)
    if observed_state is None or observed_state.transport_epoch != epoch:
        return ClaimDecision("disposed", code="obsolete_epoch")
    installation_id = observed_state.installation_id
    expected_subject = f"openship.{installation_id.hex}.v1.command.run.execute"
    expected_message_id = uuid.uuid5(
        installation_id, f"run.execute:{run_id}:{generation}:{epoch}"
    )
    row = (
        db.query(Outbox)
        .filter(Outbox.run_id == run_id, Outbox.dispatch_generation == generation)
        .one_or_none()
    )
    if (
        row is None
        or row.destination != "command"
        or row.type != "run.execute"
        or row.schema_version != 1
        or row.message_id != message_id
        or row.message_id != expected_message_id
        or row.subject != expected_subject
        or row.transport_epoch != epoch
    ):
        return ClaimDecision("disposed", code="identity_mismatch")

    identity = db.query(Run.conversation_id).filter(Run.id == run_id).first()
    if identity is None:
        return ClaimDecision("disposed", code="run_missing")
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == identity.conversation_id)
        .with_for_update()
        .first()
    )
    run = (
        db.query(Run)
        .filter(Run.id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if (
        conversation is None
        or run is None
        or job is None
        or row.conversation_id != conversation.id
    ):
        return ClaimDecision("disposed", code="reference_mismatch")
    if (conversation_id is not None and run.conversation_id != conversation_id) or (
        project_id is not None and run.project_id != project_id
    ):
        return ClaimDecision("disposed", code="reference_mismatch")
    # Admission and worker acquisition both lock the conversation before the
    # installation-wide messaging state. Preserve that order to avoid a
    # submission holding the conversation while a claim holds this gate.
    state = (
        db.query(MessageTransportState)
        .filter_by(id=1)
        .with_for_update(read=True)
        .populate_existing()
        .one_or_none()
    )
    if (
        state is None
        or state.installation_id != installation_id
        or state.transport_epoch != epoch
    ):
        return ClaimDecision("disposed", code="obsolete_epoch")
    if payload_digest is not None:
        from ..messaging.models import MessageDisposition

        previous = (
            db.query(MessageDisposition)
            .filter(MessageDisposition.message_id == message_id)
            .one_or_none()
        )
        if previous is not None and previous.payload_digest != payload_digest:
            return ClaimDecision("disposed", code="digest_mismatch")
    if job.dispatch_generation != generation:
        return ClaimDecision("disposed", code="obsolete_generation")
    if run.status in {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "stale",
        "waiting_connector",
    }:
        return ClaimDecision("disposed", code="run_not_executable")
    now = _py_now()
    lease_until = _as_utc(job.lease_until) if job.lease_until is not None else None
    if job.owner_id is not None:
        if lease_until is None or lease_until <= now:
            return ClaimDecision("reconcile_required")
        return ClaimDecision(
            "defer", delay=max(1.0, (lease_until - now).total_seconds())
        )
    if lease_until is not None:
        if lease_until <= now:
            return ClaimDecision("reconcile_required")
        return ClaimDecision(
            "defer", delay=max(1.0, (lease_until - now).total_seconds())
        )
    if run.status != "queued":
        return ClaimDecision("reconcile_required")
    if not state.execution_open:
        return ClaimDecision("defer", delay=30.0)
    available_at = _as_utc(job.available_at)
    if available_at > now:
        return ClaimDecision(
            "defer", delay=max(1.0, (available_at - now).total_seconds())
        )
    fence = max(run.fence, job.fence) + 1
    job.fence = fence
    job.owner_id = worker_id
    job.lease_until = now + timedelta(seconds=lease_seconds)
    run.fence = fence
    run.status = "running"
    db.flush()
    return ClaimDecision(
        "claimed",
        claim=Claim(run_id, fence, job.lease_until, worker_id, conversation.user_id),
    )


def _db_now():
    """Get database server time expression for comparisons in WHERE clauses."""
    return func.now()


def _py_now():
    """Get current UTC time for setting values."""
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def claim_next(db: Session, worker_id: str, lease_seconds: int | None = None) -> Claim | None:
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


def renew(db: Session, run_id: uuid.UUID, fence: int, lease_seconds: int | None = None) -> bool:
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

    now = _py_now()
    run = db.query(Run).filter(
        Run.id == run_id,
        Run.fence == fence,
        Run.status == "running",
    ).first()
    job = db.query(RunJob).filter(
        RunJob.run_id == run_id,
        RunJob.fence == fence,
        RunJob.owner_id.isnot(None),
        RunJob.lease_until > now,
    ).first()
    if run is None or job is None:
        db.rollback()
        return False
    result = db.execute(
        update(RunJob)
        .where(and_(
            RunJob.run_id == run_id,
            RunJob.fence == fence,
            RunJob.owner_id == job.owner_id,
            RunJob.lease_until > now,
        ))
        .values(lease_until=now + timedelta(seconds=lease_seconds))
        .returning(RunJob.id)
        .execution_options(synchronize_session=False)
    )
    row = result.fetchone()
    db.commit()
    return row is not None


#: A requested status that is terminal as-named; anything else is a failure.
TERMINAL_STATUSES = {"success": "completed", "cancelled": "cancelled", "timed_out": "timed_out"}


def finalize(db: Session, run_id: uuid.UUID, fence: int, result: dict, commit: bool = True) -> bool:
    """Finalize a job atomically.

    Uses UPDATE ... WHERE fence = :fence AND status = 'running' with returning
    to atomically check and update the run status.

    Args:
        db: Database session.
        run_id: ID of the run to finalize.
        fence: Current fence value (must match).
        result: Result data including status and output. Usage is persisted
            only on success and only if provided.
        commit: When False, the update stays in the caller's transaction so a
            worker can finalize a run, its assistant message, and its terminal
            event as one command.

    Returns:
        True if finalization succeeded, False if fence mismatch or job not found.
    """
    status = result.get("status", "success")
    new_status = TERMINAL_STATUSES.get(status, "failed")

    values = {
        "status": new_status,
        "ended_at": _py_now(),
        "fence": fence,
    }
    if status != "success":
        values["error_code"] = result.get("error_code", "unknown_error")

    # Persist usage only on success, only if provider returned it
    if status == "success" and result.get("usage") is not None:
        values["usage"] = json.dumps(result["usage"])

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
    if commit:
        db.commit()
    return row is not None


def reconcile_expired(db: Session) -> int:
    """Reconcile expired claims by dispatch state in one caller transaction.

    Tool intents which have crossed the committed dispatch boundary fail as
    ``execution_interrupted``. Undispatched commands may be claimed again.
    Provider-only chat runs retain their historical ``model_interrupted``
    behavior, while chat-v2 tool dispatches are fenced like manual tools.
    Locks follow conversation → run → job → operation.
    """
    expired_ids = (
        db.query(RunJob.run_id)
        .join(Run, Run.id == RunJob.run_id)
        .filter(
            RunJob.lease_until.isnot(None),
            RunJob.lease_until < _db_now(),
            Run.status == "running",
        )
        .all()
    )
    from ..execution.recovery import (
        append_run_terminal_event,
        has_uncertain_tool_dispatch,
        mark_expired_tool_interrupted,
    )

    reclaimed = 0
    now = _py_now()
    for (run_id,) in expired_ids:
        identity = db.query(Run.conversation_id).filter(Run.id == run_id).first()
        if identity is None:
            continue
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == identity.conversation_id)
            .with_for_update()
            .first()
        )
        run = (
            db.query(Run)
            .filter(Run.id == run_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        job = (
            db.query(RunJob)
            .filter(RunJob.run_id == run_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if (
            conversation is None
            or run is None
            or job is None
            or run.status != "running"
            or job.lease_until is None
            or _as_utc(job.lease_until) >= now
        ):
            continue

        old_fence = job.fence
        has_tool_dispatch = has_uncertain_tool_dispatch(db, run.id)
        from_tool_dispatch = has_tool_dispatch or run.provider_started_at is not None
        if not from_tool_dispatch:
            run.status = "queued"
            run.error_code = None
            run.ended_at = None
            run.fence = job.fence
            job.owner_id = None
            job.lease_until = None
            job.dispatch_generation += 1
            from ..messaging.models import MessageTransportState
            from ..messaging.outbox import enqueue_run

            transport = db.query(MessageTransportState).filter_by(id=1).one()
            enqueue_run(
                db,
                run.id,
                job.dispatch_generation,
                epoch=transport.transport_epoch,
            )
            reclaimed += 1
            continue

        error_code = "execution_interrupted" if has_tool_dispatch or run.kind == "tool" else "model_interrupted"
        if has_tool_dispatch or run.kind == "tool":
            mark_expired_tool_interrupted(
                db,
                run_id=run.id,
                conversation_id=conversation.id,
                old_fence=old_fence,
            )
        run.status = "failed"
        run.error_code = error_code
        run.ended_at = now
        run.fence = max(run.fence, job.fence) + 1
        job.fence = run.fence
        job.owner_id = None
        job.lease_until = None
        append_run_terminal_event(db, run, conversation.id, error_code=error_code)
        reclaimed += 1

    if reclaimed:
        db.commit()
    elif expired_ids:
        db.rollback()
    return reclaimed


def fenced_update_run(
    db: Session,
    run_id: uuid.UUID,
    fence: int,
    status: str | None = None,
    error_code: str | None = None,
    ended_at=None,
    provider_started_at=None,
    usage: str | None = None,
    model_configuration: str | None = None,
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
        model_configuration: A provider snapshot to bind under this fence.

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
    if model_configuration is not None:
        values["model_configuration"] = model_configuration

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
    identity = db.query(Run.conversation_id).filter(Run.id == run_id).first()
    if identity is None:
        return False
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == identity.conversation_id)
        .with_for_update()
        .first()
    )
    run = (
        db.query(Run)
        .filter(Run.id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    now = _py_now()
    if (
        conversation is None
        or run is None
        or job is None
        or run.status != "running"
        or run.fence != fence
        or job.fence != fence
        or job.owner_id is None
        or job.lease_until is None
        or _as_utc(job.lease_until) <= now
    ):
        db.rollback()
        return False
    run.provider_started_at = now
    db.commit()
    return True


def begin_execution_window(
    db: Session,
    claim: Claim,
    *,
    deadline_seconds: int,
    graph_thread_id: str,
) -> tuple[datetime, datetime] | None:
    """Persist one deadline per run and its current fenced graph thread."""
    if deadline_seconds < 1 or deadline_seconds > 3600:
        raise ValueError("execution deadline is outside the supported range")
    identity = db.query(Run.conversation_id).filter(Run.id == claim.run_id).first()
    if identity is None:
        return None
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == identity.conversation_id)
        .with_for_update()
        .first()
    )
    run = (
        db.query(Run)
        .filter(Run.id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    now = _py_now()
    if (
        conversation is None
        or run is None
        or job is None
        or not (
            run.kind == "tool"
            or (run.kind == "chat" and run.graph_version in (2, 3, 4))
        )
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
        or job.lease_until is None
        or _as_utc(job.lease_until) <= now
    ):
        db.rollback()
        return None
    if run.execution_started_at is None and run.execution_deadline_at is None:
        run.execution_started_at = now
        run.execution_deadline_at = now + timedelta(seconds=deadline_seconds)
    elif run.execution_started_at is None or run.execution_deadline_at is None:
        db.rollback()
        return None
    run.graph_thread_id = graph_thread_id
    started = _as_utc(run.execution_started_at)
    deadline = _as_utc(run.execution_deadline_at)
    db.commit()
    return started, deadline
