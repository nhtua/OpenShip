"""Assign the oldest durable conversation message under its scope lock."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from ...chat.models import Message
from ...events.schemas import InboxMessageSummary
from ...events.service import append_event
from ..models import ACTIVE_RUN_STATUSES, Run, RunJob
from ..service import _lock_active_conversation_scope
from ..tasks.models import TaskDecisionRecord, TaskRecord
from .models import ConversationInboxEntry


def assign_next_message(
    db: Session, *, conversation_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> InboxMessageSummary | None:
    """Assign one awaiting entry; the caller commits or rolls back the change."""
    _lock_active_conversation_scope(db, user_id=user_id, conversation_id=conversation_id)
    active = (
        db.query(Run)
        .filter(Run.conversation_id == conversation_id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .with_for_update()
        .one_or_none()
    )
    decision = None
    if active is not None:
        db.query(RunJob).filter_by(run_id=active.id).with_for_update().one_or_none()
        db.query(TaskRecord).filter_by(run_id=active.id).with_for_update().one_or_none()
        decision = (
            db.query(TaskDecisionRecord)
            .filter(
                TaskDecisionRecord.run_id == active.id,
                TaskDecisionRecord.kind == "clarification",
                TaskDecisionRecord.state == "open",
                TaskDecisionRecord.expires_at > now,
            )
            .with_for_update()
            .one_or_none()
        )
    entry = (
        db.query(ConversationInboxEntry)
        .filter_by(conversation_id=conversation_id, status="awaiting")
        .order_by(ConversationInboxEntry.sequence)
        .with_for_update()
        .first()
    )
    if entry is None:
        return None
    if decision is not None:
        # Do not attach a second answer while the first is under assessment.
        if active.status != "waiting_input":
            return None
        entry.disposition, entry.status = "clarification", "attached"
        entry.run_id, entry.decision_id = active.id, decision.id
        message = db.get(Message, entry.message_id)
        message.run_id, message.message_kind = active.id, "clarification_reply"
        db.flush()
        from ..tasks.clarification import enqueue_clarification_reply

        enqueue_clarification_reply(
            db, run_id=active.id, decision_id=decision.id, message_id=entry.message_id, now=now
        )
    else:
        entry.disposition, entry.status = "queued", "queued"
    db.flush()
    append_event(
        db, conversation_id, entry.run_id, "message.assigned",
        {"schema_version": 1, "message_id": str(entry.message_id),
         "disposition": entry.disposition, "status": entry.status,
         "run_id": str(entry.run_id) if entry.run_id else None,
         "request_id": str(decision.request_id) if decision else None},
    )
    return InboxMessageSummary(
        message_id=entry.message_id, disposition=entry.disposition, status=entry.status,
        run_id=entry.run_id, request_id=decision.request_id if decision else None,
        sequence=entry.sequence,
    )
