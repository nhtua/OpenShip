"""Durable active-execution windows for one v5 task run.

A task budget has to answer two questions honestly: how much work it has
consumed, and how long it has actually spent executing. Only the second one
owns a clock, and that clock must stop while a durable human wait holds the
task without erasing what the task already consumed. These helpers own the
interval columns on the task record (`active_started_at`, `active_deadline_at`
and the accumulated `active_ms`); the run's own wall deadline stays where the
legacy machinery put it.

Callers own the transaction and the commit. Opening and closing revalidate the
live claim, so a worker that no longer owns the run can neither open nor charge
a window. Charging after a crash is the one transition that deliberately does
not require a live claim - the lease is gone by then - and it charges the
unfinished interval up to its deadline instead of crediting or resetting it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ...capabilities.contracts import LoopLimits
from ...execution.admission import require_live_claim
from ...providers.contracts import RunLimits
from ..models import Run
from ..queue import Claim
from .contracts import ExecutionConfigV5, TaskLimits, TaskProgress
from .models import TaskRecord
from .state import TaskStateError

_DEFAULT_DEADLINE_SECONDS = LoopLimits().deadline_seconds


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def effective_loop_limits(
    execution,
    *,
    profile_limits: RunLimits | None = None,
    max_generation_attempts: int | None = None,
) -> TaskLimits:
    """The task ceilings a v5 budget charges against, lowering only.

    Planning turns, repairs, diagnosis and ordinary tool turns share these
    ceilings, so no leg of the task can be counted separately. A declared
    profile cap or a deployment ceiling narrows a limit; it never widens one,
    and a limit nobody declared keeps the task ceiling.
    """
    ceilings = execution.task_limits
    attempts = ceilings.max_generation_attempts
    per_request = ceilings.max_output_tokens_per_request
    if max_generation_attempts is not None:
        if type(max_generation_attempts) is not int or max_generation_attempts < 1:
            raise ValueError("the deployment attempt ceiling must be positive")
        attempts = min(attempts, max_generation_attempts)
    if profile_limits is not None:
        attempts = min(attempts, profile_limits.max_attempts)
        per_request = min(per_request, profile_limits.max_output_tokens)
    return TaskLimits(
        schema_version=ceilings.schema_version,
        max_steps=ceilings.max_steps,
        max_tool_calls=ceilings.max_tool_calls,
        max_model_turns=ceilings.max_model_turns,
        max_generation_attempts=attempts,
        max_output_tokens_per_request=per_request,
        max_total_output_tokens=ceilings.max_total_output_tokens,
        max_repairs_per_step=ceilings.max_repairs_per_step,
        decision_expiry_hours=ceilings.decision_expiry_hours,
    )


def attempt_ceiling_allows(
    progress: TaskProgress,
    step_id: str,
    *,
    limits: TaskLimits | None = None,
) -> bool:
    """Whether one more attempt on `step_id` fits that step's attempt ceiling.

    A step gets one initial attempt plus its repair allowance, so an explicit
    lower repair ceiling also bounds how often a user may repeat a step. This
    reads only the durable progress already recorded; it grants nothing.
    """
    ceilings = limits if limits is not None else TaskLimits()
    used = sum(1 for attempt in progress.attempts if attempt.step_id == step_id)
    return used < 1 + ceilings.max_repairs_per_step


def _deadline_seconds(db: Session, run_id) -> int:
    """The pinned wall deadline one window may run for, never a fresh setting."""
    run = db.get(Run, run_id)
    raw = run.execution_configuration if run is not None else None
    if not raw:
        return _DEFAULT_DEADLINE_SECONDS
    try:
        execution = ExecutionConfigV5.model_validate_json(raw)
    except ValueError as invalid:
        raise TaskStateError(
            "state_corrupt", "the pinned task execution configuration is invalid"
        ) from invalid
    return execution.loop_limits.deadline_seconds


def _task_row(db: Session, run_id):
    return db.query(TaskRecord).filter_by(run_id=run_id).one_or_none()


def _charge_elapsed_ms(task: TaskRecord, now: datetime) -> int:
    """Milliseconds the open window consumed, capped at its own deadline.

    Charging is capped so an interrupted interval can never be credited with
    time the task did not own, and so a stale clock can never go negative.
    """
    started = _utc(task.active_started_at)
    deadline = _utc(task.active_deadline_at)
    charged = min(_utc(now), deadline)
    return max(0, int((charged - started).total_seconds() * 1000))


def _close_active_window(task: TaskRecord, now: datetime) -> None:
    """Charge the open interval and leave no window standing afterwards."""
    task.active_ms = int(task.active_ms or 0) + _charge_elapsed_ms(task, now)
    task.active_started_at = None
    task.active_deadline_at = None
    task.updated_at = _utc(now)


def open_task_window(db: Session, claim: Claim, *, now: datetime) -> datetime:
    """Open the active window under the live claim and return its deadline.

    Re-opening an already open window is idempotent: it never moves a deadline
    and never re-charges elapsed time. A resumed window keeps only the
    remaining active time, so a wait that consumed some of it cannot buy back
    what it spent.
    """
    run = require_live_claim(db, claim, lock=True)
    task = _task_row(db, run.id)
    if task is None:
        raise TaskStateError("task_missing", "this run has no durable task record")
    if task.active_started_at is not None and task.active_deadline_at is not None:
        return _utc(task.active_deadline_at)
    deadline_seconds = _deadline_seconds(db, run.id)
    spent = int(task.active_ms or 0) // 1000
    started = _utc(now)
    deadline_at = started + timedelta(seconds=max(0, deadline_seconds - spent))
    task.active_started_at = started
    task.active_deadline_at = deadline_at
    task.updated_at = started
    db.flush()
    return deadline_at


def close_task_window(db: Session, claim: Claim, *, now: datetime) -> None:
    """Charge and clear the open window before a durable wait or a stop."""
    run = require_live_claim(db, claim, lock=True)
    task = _task_row(db, run.id)
    if task is None:
        raise TaskStateError("task_missing", "this run has no durable task record")
    if task.active_started_at is None or task.active_deadline_at is None:
        return
    _close_active_window(task, now)
    db.flush()


def charge_interrupted_window(db: Session, claim: Claim, *, now: datetime) -> None:
    """Charge a window whose worker died, without needing its lease.

    The lease is gone when this runs, so the claim cannot be revalidated; the
    interval is charged up to its deadline and cleared, which leaves resuming
    with the remaining active time rather than a reset clock.
    """
    task = _task_row(db, claim.run_id)
    if task is None or task.active_started_at is None or task.active_deadline_at is None:
        return
    _close_active_window(task, now)
    db.flush()


def close_active_window(task: TaskRecord, now: datetime) -> None:
    """Charge a window on an already-locked task row (caller holds the lock)."""
    if task.active_started_at is None or task.active_deadline_at is None:
        return
    _close_active_window(task, now)
