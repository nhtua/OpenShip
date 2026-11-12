"""Durable clarification replies and fenced assessment scheduling."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...messaging.models import MessageTransportState
from ...messaging.outbox import enqueue_run
from ..inbox.models import ConversationInboxEntry
from ..models import Run, RunJob
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
