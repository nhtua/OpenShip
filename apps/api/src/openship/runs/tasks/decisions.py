"""Transactional maintenance for durable task decisions.

The caller owns commit and rollback. Parking and command consumption are not
implemented here until their lifecycle integration can enforce safe delivery.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ...chat.models import Conversation
from ...events.service import append_event
from ...workspace.models import Project
from ..models import Run, RunJob
from .models import TaskDecisionRecord, TaskRecord

_WAIT_STATUS = {
    "plan_confirmation": "waiting_plan",
    "clarification": "waiting_input",
    "configuration": "waiting_config",
}


def expire_task_decisions(db: Session, *, now: datetime, batch_size: int = 100) -> int:
    """Expire up to batch_size matching waits, without committing the caller's work."""
    if now.tzinfo is None or now.utcoffset() is None or batch_size < 1:
        raise ValueError("an aware timestamp and positive batch size are required")
    now = now.astimezone(UTC)
    candidates = (
        db.query(TaskDecisionRecord.id, Run.conversation_id, Run.project_id)
        .join(Run, Run.id == TaskDecisionRecord.run_id)
        .filter(
            TaskDecisionRecord.state == "open", TaskDecisionRecord.expires_at <= now
        )
        .order_by(TaskDecisionRecord.expires_at, TaskDecisionRecord.id)
        .limit(batch_size)
        .all()
    )
    expired = 0
    for decision_id, conversation_id, project_id in candidates:
        conversation = (
            db.query(Conversation)
            .filter_by(id=conversation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        project = (
            db.query(Project)
            .filter_by(id=project_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        identity = (
            db.query(TaskDecisionRecord.run_id).filter_by(id=decision_id).one_or_none()
        )
        if identity is None:
            continue
        run = (
            db.query(Run)
            .filter_by(id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        job = (
            db.query(RunJob)
            .filter_by(run_id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        task = (
            db.query(TaskRecord)
            .filter_by(run_id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        decision = (
            db.query(TaskDecisionRecord)
            .filter_by(id=decision_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if not all((conversation, project, run, job, task, decision)):
            continue
        deadline = decision.expires_at
        if deadline.tzinfo is None:  # SQLite returns naive UTC timestamps.
            deadline = deadline.replace(tzinfo=UTC)
        if (
            decision.state != "open"
            or deadline > now
            or run.conversation_id != conversation.id
            or run.project_id != project.id
            or conversation.project_id != project.id
            or task.project_id != project.id
            or task.conversation_id != conversation.id
            or decision.task_id != task.id
            or task.status != "waiting"
            or run.status != _WAIT_STATUS.get(decision.kind)
            or job.owner_id is not None
            or job.lease_until is not None
        ):
            continue
        decision.state = "expired"
        decision.resolved_at = now
        task.status = "blocked"
        task.updated_at = now
        run.status = "timed_out"
        run.error_code = "decision_expired"
        run.ended_at = now
        run.fence = max(run.fence, job.fence) + 1
        job.fence = run.fence
        task.fence = run.fence
        append_event(
            db,
            conversation_id=conversation.id,
            run_id=run.id,
            event_type="task.decision_resolved",
            payload={
                "schema_version": 1,
                "run_id": str(run.id),
                "revision": decision.plan_revision,
                "request_id": str(decision.request_id),
                "state": "expired",
            },
        )
        db.flush()
        expired += 1
    return expired
