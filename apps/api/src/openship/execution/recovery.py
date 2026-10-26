"""Fenced tool-operation cancellation and expired-run recovery helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import release_artifact_reservation
from src.openship.events.service import append_event

from .models import ExecutionSlot, ToolAttempt, ToolOperation

_SAFE_MESSAGES = {
    "cancelled": "The run was cancelled.",
    "execution_interrupted": "The tool execution was interrupted and will not be repeated automatically.",
}


def cancel_tool_operations(
    db: Session,
    *,
    run_id,
    conversation_id,
    prior_fence: int,
) -> None:
    """Cancel undispatched calls and interrupt calls whose outcome is uncertain."""
    operations = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run_id,
            ToolOperation.status.in_(("admitted", "dispatching", "running")),
        )
        .with_for_update()
        .all()
    )
    for operation in operations:
        attempts = (
            db.query(ToolAttempt)
            .filter(ToolAttempt.operation_id == operation.id)
            .with_for_update()
            .all()
        )
        dispatched = operation.status in ("dispatching", "running") or any(
            attempt.outcome == "started" for attempt in attempts
        )
        status = "interrupted" if dispatched else "cancelled"
        code = "execution_interrupted" if dispatched else "cancelled"
        operation.status = status
        operation.error_code = code
        operation.result_json = None
        operation.result_digest = None
        operation.output_artifact_ids = "[]"
        operation.finished_at = _now()
        operation.updated_at = operation.finished_at
        for attempt in attempts:
            if attempt.outcome == "started":
                attempt.outcome = "interrupted" if dispatched else "cancelled"
                attempt.error_code = code
                attempt.finished_at = _now()
        _release_staging_outputs(db, operation.id)
        _append_operation_event(
            db,
            conversation_id,
            operation,
            status,
            code,
            event_type="tool.cancelled",
        )

    slots = (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.run_id == run_id,
            ExecutionSlot.fence == prior_fence,
        )
        .with_for_update()
        .all()
    )
    for slot in slots:
        slot.operation_id = None
        slot.run_id = None
        slot.fence = None
        slot.owner_id = None
        slot.lease_until = None
        slot.updated_at = _now()
    db.flush()


def mark_expired_tool_interrupted(
    db: Session,
    *,
    run_id,
    conversation_id,
    old_fence: int,
) -> bool:
    """Make every active dispatched tool operation terminal without retry."""
    operations = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run_id,
            ToolOperation.status.in_(("dispatching", "running")),
        )
        .with_for_update()
        .all()
    )
    for operation in operations:
        operation.status = "interrupted"
        operation.error_code = "execution_interrupted"
        operation.result_json = None
        operation.result_digest = None
        operation.output_artifact_ids = "[]"
        operation.finished_at = _now()
        operation.updated_at = operation.finished_at
        for attempt in (
            db.query(ToolAttempt)
            .filter(ToolAttempt.operation_id == operation.id)
            .with_for_update()
            .all()
        ):
            if attempt.outcome == "started":
                attempt.outcome = "interrupted"
                attempt.error_code = "execution_interrupted"
                attempt.finished_at = _now()
        _release_staging_outputs(db, operation.id)
        _append_operation_event(
            db,
            conversation_id,
            operation,
            "interrupted",
            "execution_interrupted",
            event_type="tool.failed",
        )

    slots = (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.run_id == run_id,
            ExecutionSlot.fence == old_fence,
        )
        .with_for_update()
        .all()
    )
    for slot in slots:
        slot.operation_id = None
        slot.run_id = None
        slot.fence = None
        slot.owner_id = None
        slot.lease_until = None
        slot.updated_at = _now()
    return bool(operations)


def has_uncertain_tool_dispatch(db: Session, run_id) -> bool:
    """Whether a tool intent or started physical attempt crossed dispatch."""
    operation = (
        db.query(ToolOperation.id)
        .filter(
            ToolOperation.run_id == run_id,
            ToolOperation.status.in_(("dispatching", "running")),
        )
        .first()
    )
    if operation is not None:
        return True
    attempt = (
        db.query(ToolAttempt.id)
        .join(ToolOperation, ToolOperation.id == ToolAttempt.operation_id)
        .filter(
            ToolOperation.run_id == run_id,
            ToolAttempt.outcome == "started",
        )
        .first()
    )
    return attempt is not None


def append_run_terminal_event(db: Session, run, conversation_id, *, error_code: str) -> None:
    message = {
        "model_interrupted": "The model stopped before completing the answer. Try again.",
        **_SAFE_MESSAGES,
    }.get(error_code, "The run could not be completed safely.")
    append_event(
        db,
        conversation_id,
        run.id,
        "run.failed",
        {"error_code": error_code, "error": message, "model": None},
    )


def _append_operation_event(
    db: Session,
    conversation_id,
    operation: ToolOperation,
    status: str,
    error_code: str,
    *,
    event_type: str,
) -> None:
    append_event(
        db,
        conversation_id,
        operation.run_id,
        event_type,
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "attempt_id": None,
            "capability_id": operation.capability_id,
            "status": status,
            "error_code": error_code,
        },
    )


def _release_staging_outputs(db: Session, operation_id) -> None:
    """Free unpublished output reservations when execution becomes terminal."""
    artifact_ids = (
        db.query(Artifact.id)
        .filter(
            Artifact.originating_operation_id == operation_id,
            Artifact.status == "staging",
        )
        .all()
    )
    for (artifact_id,) in artifact_ids:
        release_artifact_reservation(db, artifact_id)


def _now():
    return datetime.now(UTC)
