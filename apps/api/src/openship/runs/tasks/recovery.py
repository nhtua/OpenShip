"""Recovery-grade reads of durable task truth.

The module is deliberately side-effect free: it reads committed rows and
reports what they prove. A logical call that was bound before dispatch but has
no settled ``TaskStepAttempt`` is uncertainty, not absence, so the projection
keeps it at the binding's ordinal and names the admitted operation only when
one exists. Nothing here reads a checkpoint, a prompt or a provider response.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ...execution.models import ToolOperation
from .contracts import TaskAttempt, TaskCallBinding, TaskProgress
from .models import TaskRecord, TaskStepAttempt
from .state import (
    TaskStateError,
    _counters,
    _current_plan,
    _json,
    _load_plan,
    _pending_decision,
    _verified_outputs,
    _verified_steps,
)


def _call_bindings(task: TaskRecord) -> tuple[TaskCallBinding, ...]:
    raw = _json(task.call_bindings_json, [])
    if not isinstance(raw, list):
        raise TaskStateError("state_corrupt", "call bindings are not a list")
    try:
        bindings = tuple(TaskCallBinding.model_validate(item) for item in raw)
    except ValueError as exc:
        raise TaskStateError("state_corrupt", "a call binding is unreadable") from exc
    if len({binding.logical_call_id for binding in bindings}) != len(bindings):
        raise TaskStateError("state_corrupt", "call bindings repeat a logical call")
    return bindings


def _committed_attempts(
    db: Session, task: TaskRecord, step_ids: tuple[str, ...]
) -> tuple[tuple[TaskAttempt, ...], tuple[TaskStepAttempt, ...]]:
    """The attempts the ledger has settled, exactly as the rows record them."""
    if not step_ids:
        return (), ()
    rows = (
        db.query(TaskStepAttempt)
        .filter(
            TaskStepAttempt.task_id == task.id,
            TaskStepAttempt.step_id.in_(step_ids),
        )
        .order_by(TaskStepAttempt.step_id.asc(), TaskStepAttempt.ordinal.asc())
        .all()
    )
    attempts = tuple(
        TaskAttempt(
            step_id=row.step_id,
            attempt=row.ordinal,
            status=row.status,
            operation_id=row.operation_id,
            input_digest=row.input_digest,
            result_digest=row.result_digest,
            evidence_refs=tuple(_json(row.evidence_json, [])),
        )
        for row in rows
    )
    return attempts, tuple(rows)


def _operations_by_logical_call(
    db: Session, run_id: UUID, logical_call_ids: set[str]
) -> dict[str, ToolOperation]:
    if not logical_call_ids:
        return {}
    rows = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run_id,
            ToolOperation.logical_call_id.in_(logical_call_ids),
        )
        .all()
    )
    return {row.logical_call_id: row for row in rows}


def reconcile_task_projection(db: Session, *, run_id: UUID) -> TaskProgress:
    """Read one task's truth from committed rows without writing anything.

    Attempts come from ``TaskStepAttempt`` rows. A call binding with no settled
    attempt row is projected as ``uncertain`` at the binding's own ordinal: if
    an operation was admitted for that logical call, its id and arguments
    digest are reported, and otherwise both stay ``None``. No ordinal, step,
    digest or operation identity is ever derived from the plan or the model.
    """
    task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one_or_none()
    if task is None:
        raise TaskStateError(
            "task_missing", "no durable task record exists for this run"
        )

    plan_row = _current_plan(db, task)
    if plan_row is None and task.current_revision > 0:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row) if plan_row is not None else None
    step_ids = tuple(step.id for step in plan.steps) if plan is not None else ()
    plan_steps = set(step_ids)

    bindings = _call_bindings(task)
    if bindings and not plan_steps:
        raise TaskStateError(
            "state_corrupt", "call bindings require a current plan"
        )
    for binding in bindings:
        if binding.step_id not in plan_steps:
            raise TaskStateError(
                "state_corrupt",
                "a call binding references a step outside the current plan",
            )

    committed, rows = _committed_attempts(db, task, step_ids)
    settled_ordinals = {(row.step_id, row.ordinal) for row in rows}
    settled_operations = {row.operation_id for row in rows}

    pending = [
        binding
        for binding in bindings
        if (binding.step_id, binding.attempt) not in settled_ordinals
    ]
    operations = _operations_by_logical_call(
        db, task.run_id, {binding.logical_call_id for binding in pending}
    )

    uncertain: list[TaskAttempt] = []
    for binding in bindings:
        if (binding.step_id, binding.attempt) in settled_ordinals:
            continue
        operation = operations.get(binding.logical_call_id)
        if operation is not None and operation.id in settled_operations:
            continue
        uncertain.append(
            TaskAttempt(
                step_id=binding.step_id,
                attempt=binding.attempt,
                status="uncertain",
                operation_id=operation.id if operation is not None else None,
                input_digest=(
                    operation.arguments_digest if operation is not None else None
                ),
            )
        )

    step_order = {step_id: index for index, step_id in enumerate(step_ids)}
    attempts = tuple(
        sorted(
            (*committed, *uncertain),
            key=lambda attempt: (step_order[attempt.step_id], attempt.attempt),
        )
    )

    return TaskProgress(
        current_revision=task.current_revision,
        status=task.status,
        step_ids=step_ids,
        attempts=attempts,
        verified_outputs=_verified_outputs(task),
        verified_steps=_verified_steps(task),
        skipped_steps=tuple(_json(task.skipped_steps_json, [])),
        call_bindings=bindings,
        pending_decision=_pending_decision(db, task),
        counters=_counters(task),
        last_disposition=task.last_disposition,
    )
