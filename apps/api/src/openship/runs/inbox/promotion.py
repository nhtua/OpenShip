"""Promote durable messages without bypassing the conversation execution slot."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from ...chat.models import Conversation
from ...events.service import append_event
from ...workspace.models import Project
from ..models import ACTIVE_RUN_STATUSES, Run
from ..service import _lock_active_conversation_scope, submit_turn
from .assignment import assign_next_message
from .contracts import MessageAdmissionRequest
from .models import ConversationInboxEntry


def promote_next_message(db: Session, *, conversation_id: uuid.UUID) -> Run | None:
    """Promote the oldest queued entry inside the caller's transaction."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(next_event_sequence=Conversation.next_event_sequence)
        )
    conv = (
        db.query(Conversation)
        .filter_by(id=conversation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if conv is None:
        return None
    try:
        _lock_active_conversation_scope(
            db, user_id=conv.user_id, conversation_id=conversation_id
        )
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            return None
        raise
    if (
        db.query(Run.id)
        .filter(
            Run.conversation_id == conversation_id, Run.status.in_(ACTIVE_RUN_STATUSES)
        )
        .first()
    ):
        return None
    entry = (
        db.query(ConversationInboxEntry)
        .filter_by(
            conversation_id=conversation_id, disposition="queued", status="queued"
        )
        .order_by(ConversationInboxEntry.sequence)
        .with_for_update()
        .first()
    )
    if entry is None:
        return None
    request = MessageAdmissionRequest.model_validate_json(entry.request_json)
    try:
        with db.begin_nested():
            run = submit_turn(
                db,
                user_id=conv.user_id,
                conversation_id=str(conv.id),
                **request.model_dump(),
                _inbox_entry=entry,
            )
            entry.run_id = run.id
            entry.status = "promoted"
            db.flush()
            append_event(
                db,
                conv.id,
                run.id,
                "message.status_changed",
                {
                    "schema_version": 1,
                    "message_id": str(entry.message_id),
                    "status": "promoted",
                    "run_id": str(run.id),
                    "reason": None,
                },
            )
        return run
    except HTTPException as exc:
        if exc.status_code >= 500:
            raise
        details = exc.detail if isinstance(exc.detail, dict) else {}
        code = details.get("error", {}).get("code", "admission_failed")
        entry.status = "failed"
        entry.failure_code = code
        db.flush()
        append_event(
            db,
            conv.id,
            None,
            "message.status_changed",
            {
                "schema_version": 1,
                "message_id": str(entry.message_id),
                "status": "failed",
                "run_id": None,
                "reason": code,
            },
        )
        return None


def reconcile_queued_messages(db: Session, *, batch_size: int = 100) -> int:
    """Rescue awaiting and queued entries for conversations with no active run.

    Assigns the oldest awaiting entry first (to an open clarification or as a
    queued turn), then promotes the oldest queued entry. Idempotent across
    retries and workers.
    """
    active_run = (
        db.query(Run.id)
        .filter(
            Run.conversation_id == ConversationInboxEntry.conversation_id,
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .exists()
    )
    # Conversations holding awaiting or queued entries with no active run.
    conversation_ids = [
        row[0]
        for row in db.query(ConversationInboxEntry.conversation_id)
        .join(Conversation, Conversation.id == ConversationInboxEntry.conversation_id)
        .join(Project, Project.id == Conversation.project_id)
        .filter(
            ConversationInboxEntry.status.in_(("awaiting", "queued")),
            ConversationInboxEntry.disposition.in_(
                (None, "clarification", "queued")
            ),
            Conversation.archived_at.is_(None),
            Project.archived_at.is_(None),
            ~active_run,
        )
        .distinct()
        .limit(batch_size)
        .all()
    ]
    count = 0
    for conversation_id in conversation_ids:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            continue
        assign_next_message(
            db,
            conversation_id=conversation_id,
            user_id=conversation.user_id,
            now=datetime.now(UTC),
        )
        if promote_next_message(db, conversation_id=conversation_id) is not None:
            count += 1
    return count
