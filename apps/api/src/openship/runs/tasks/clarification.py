"""Durable clarification replies and fenced assessment scheduling."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...chat.models import Conversation, Message
from ...events.service import append_event
from ...messaging.models import MessageTransportState
from ...messaging.outbox import enqueue_run
from ...workspace.models import Project
from ..inbox.models import ConversationInboxEntry
from ..models import Run, RunJob
from .contracts import ClarificationAssessment
from .models import TaskDecisionRecord, TaskRecord


def clarification_replies(
    db: Session, *, decision_id: uuid.UUID
) -> tuple[ConversationInboxEntry, ...]:
    return tuple(
        db.query(ConversationInboxEntry)
        .filter_by(decision_id=decision_id, disposition="clarification")
        .order_by(ConversationInboxEntry.sequence)
        .all()
    )


def enqueue_clarification_reply(
    db: Session,
    *,
    run_id: uuid.UUID,
    decision_id: uuid.UUID,
    message_id: uuid.UUID,
    now: datetime,
) -> None:
    """Schedule one fenced assessment without resolving the bound request."""
    run = db.query(Run).filter_by(id=run_id).with_for_update().populate_existing().one()
    job = (
        db.query(RunJob)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    task = (
        db.query(TaskRecord)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    decision = (
        db.query(TaskDecisionRecord)
        .filter_by(id=decision_id, run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    entry = (
        db.query(ConversationInboxEntry)
        .filter_by(message_id=message_id, decision_id=decision_id)
        .one()
    )
    deadline = decision.expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if (
        decision.kind != "clarification"
        or decision.state != "open"
        or deadline <= now
        or run.ended_at is not None
        or task.fence > run.fence
        or job.fence != run.fence
        or (run.status == "waiting_input" and task.fence != run.fence)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "decision_conflict",
                    "message": "Clarification is no longer available",
                }
            },
        )
    assessed = json.loads(decision.request_json).get("assessed_sequence", -1)
    if entry.sequence <= assessed or run.status in ("queued", "running"):
        return
    if (
        run.status != "waiting_input"
        or job.owner_id is not None
        or job.lease_until is not None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "decision_conflict",
                    "message": "Clarification is no longer available",
                }
            },
        )
    run.status = "queued"
    job.available_at = now
    job.dispatch_generation += 1
    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)
    db.flush()


def _latest_reply_sequence(db: Session, decision_id: uuid.UUID) -> int:
    latest = (
        db.query(func.max(ConversationInboxEntry.sequence))
        .filter_by(decision_id=decision_id, disposition="clarification")
        .scalar()
    )
    return int(latest) if latest is not None else -1


def _lock_decision_scope(db: Session, *, run_id: uuid.UUID, decision_id: uuid.UUID):
    """Lock conversation → project and the run's durable rows, in that order."""
    run = (
        db.query(Run)
        .filter_by(id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if run is None:
        return None
    conversation = (
        db.query(Conversation)
        .filter_by(id=run.conversation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    project = (
        db.query(Project)
        .filter_by(id=run.project_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = (
        db.query(RunJob)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    task = (
        db.query(TaskRecord)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    decision = (
        db.query(TaskDecisionRecord)
        .filter_by(id=decision_id, run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if not all((conversation, project, job, task, decision)):
        return None
    return conversation, project, run, job, task, decision


def _expired(decision: TaskDecisionRecord, now: datetime) -> bool:
    deadline = decision.expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= now


def apply_clarification_assessment(
    db: Session,
    claim,
    *,
    decision_id: uuid.UUID,
    included_sequence: int,
    assessment: ClarificationAssessment,
    now: datetime,
) -> bool:
    """Apply one assessment only while the reply high-water and fence still match.

    ``False`` means a newer reply (or a lost lease) beat this assessment: the
    caller reloads and evaluates again, so the reply is never lost and a stale
    verdict never resolves the request.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    now = now.astimezone(UTC)
    identity = (
        db.query(TaskDecisionRecord.run_id).filter_by(id=decision_id).one_or_none()
    )
    if identity is None or identity.run_id != claim.run_id:
        return False
    scope = _lock_decision_scope(db, run_id=claim.run_id, decision_id=decision_id)
    if scope is None:
        return False
    conversation, project, run, job, task, decision = scope
    if (
        decision.kind != "clarification"
        or decision.state != "open"
        or _expired(decision, now)
        or run.ended_at is not None
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
        or task.fence > run.fence
        or conversation.archived_at is not None
        or project.archived_at is not None
        or task.project_id != project.id
    ):
        return False
    if _latest_reply_sequence(db, decision_id) > included_sequence:
        return False

    if assessment.verdict == "insufficient":
        request = json.loads(decision.request_json)
        if not isinstance(request, dict) or assessment.follow_up is None:
            return False
        request["prompt"] = assessment.follow_up
        request["assessed_sequence"] = included_sequence
        decision.request_json = json.dumps(request, sort_keys=True)
        follow_up = Message(
            conversation_id=conversation.id,
            run_id=run.id,
            role="assistant",
            message_kind="clarification_follow_up",
            content=assessment.follow_up,
        )
        db.add(follow_up)
        db.flush()
        task.status = "waiting"
        task.updated_at = now
        run.status = "waiting_input"
        run.fence = claim.fence + 1
        job.fence = task.fence = run.fence
        job.owner_id = job.lease_until = None
        job.available_at = now
        deadline = decision.expires_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        append_event(
            db,
            conversation_id=conversation.id,
            run_id=run.id,
            event_type="task.decision_updated",
            payload={
                "schema_version": 1,
                "run_id": str(run.id),
                "revision": decision.plan_revision,
                "request_id": str(decision.request_id),
                "kind": "clarification",
                "prompt": assessment.follow_up,
                "message_id": str(follow_up.id),
                "expires_at": deadline.isoformat(),
            },
        )
        db.flush()
        return True

    decision.state = "answered"
    decision.resolved_at = now
    task.status = "planned" if task.current_revision else "unknown"
    task.updated_at = now
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
            "state": "answered",
        },
    )
    db.flush()
    return True


def release_clarification_claim(
    db: Session,
    claim,
    *,
    decision_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Return the run to its wait without changing the request or its replies.

    A transient inference failure keeps everything durable: the same request
    stays open, every reply stays bound, and only the worker's claim is fenced
    back so another turn can try again.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    now = now.astimezone(UTC)
    identity = (
        db.query(TaskDecisionRecord.run_id).filter_by(id=decision_id).one_or_none()
    )
    if identity is None or identity.run_id != claim.run_id:
        return False
    scope = _lock_decision_scope(db, run_id=claim.run_id, decision_id=decision_id)
    if scope is None:
        return False
    _conversation, _project, run, job, task, decision = scope
    if (
        decision.kind != "clarification"
        or decision.state != "open"
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
    ):
        return False
    run.status = "waiting_input"
    run.fence = claim.fence + 1
    job.fence = task.fence = run.fence
    job.owner_id = job.lease_until = None
    job.available_at = now
    db.flush()
    return True
