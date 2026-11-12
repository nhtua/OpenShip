"""Serialize durable conversation messages and bind their disposition once."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from ...capabilities.contracts import canonical_digest
from ...chat.models import Conversation, Message
from ...config import settings
from ...events.service import append_event
from ..models import ACTIVE_RUN_STATUSES, Run
from ..service import (
    _lock_active_conversation_scope,
    _locked_agent_preference,
    _resolve_selection,
    _resolve_task_selection,
    _stale_agent_preference,
    require_owned_conversation,
)
from ..tasks.models import TaskDecisionRecord
from .contracts import MessageAdmissionRequest, MessageAdmissionResponse
from .models import ConversationInboxEntry


def _response(db: Session, entry: ConversationInboxEntry) -> MessageAdmissionResponse:
    decision = (
        db.get(TaskDecisionRecord, entry.decision_id) if entry.decision_id else None
    )
    return MessageAdmissionResponse(
        message_id=entry.message_id,
        disposition=entry.disposition,
        status=entry.status,
        run_id=entry.run_id,
        request_id=decision.request_id if decision else None,
    )


def submit_message(
    db: Session,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request: MessageAdmissionRequest,
) -> MessageAdmissionResponse:
    """Admit one message in the caller's transaction; never commit here."""
    try:
        uuid.UUID(request.client_request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Invalid client_request_id"
        ) from exc

    conversation = require_owned_conversation(db, user_id, str(conversation_id))
    # SQLite ignores FOR UPDATE; acquire its write lock before the replay and
    # routing reads. PostgreSQL already holds the row lock from scope admission.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(next_event_sequence=Conversation.next_event_sequence)
        )

    submitted = request.model_dump(mode="json")
    digest = canonical_digest(submitted)
    existing = (
        db.query(ConversationInboxEntry)
        .filter_by(
            conversation_id=conversation_id, client_request_id=request.client_request_id
        )
        .one_or_none()
    )
    if existing is not None:
        if existing.request_digest != digest:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "client_request_id_conflict",
                        "message": "Message request differs from the original",
                    }
                },
            )
        return _response(db, existing)

    conversation = _lock_active_conversation_scope(
        db, user_id=user_id, conversation_id=conversation_id
    )
    active = (
        db.query(Run)
        .filter(
            Run.conversation_id == conversation.id, Run.status.in_(ACTIVE_RUN_STATUSES)
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    decision = None
    if active is not None:
        decision = (
            db.query(TaskDecisionRecord)
            .filter(
                TaskDecisionRecord.run_id == active.id,
                TaskDecisionRecord.kind == "clarification",
                TaskDecisionRecord.state == "open",
                TaskDecisionRecord.expires_at > datetime.now(UTC),
            )
            .with_for_update()
            .one_or_none()
        )

    frozen = dict(submitted)
    if decision is None:
        mode, revision = _locked_agent_preference(db, user_id)
        if request.agent_mode is not None and (
            request.agent_mode,
            request.agent_mode_revision,
        ) != (mode, revision):
            raise _stale_agent_preference(mode, revision)
        frozen["agent_mode"], frozen["agent_mode_revision"] = mode, revision
        resolver = (
            _resolve_task_selection
            if settings.agentic_tasks_enabled and request.enable_tools
            else _resolve_selection
        )
        selection = resolver(request.provider_profile_id, request.fallback_profile_ids)
        frozen["provider_profile_id"] = selection.primary.id
        frozen["fallback_profile_ids"] = [profile.id for profile in selection.fallbacks]

    message = Message(
        conversation_id=conversation_id,
        run_id=active.id if decision else None,
        role="user",
        content=request.content,
    )
    db.add(message)
    db.flush()
    disposition = "clarification" if decision else "queued"
    status = "attached" if decision else "queued"
    payload = {
        "schema_version": 1,
        "message_id": str(message.id),
        "disposition": disposition,
        "status": status,
        "run_id": str(active.id) if decision else None,
        "request_id": str(decision.request_id) if decision else None,
    }
    event = append_event(
        db,
        conversation_id,
        active.id if decision else None,
        "message.admitted",
        payload,
        actor_id=user_id,
    )
    entry = ConversationInboxEntry(
        conversation_id=conversation_id,
        message_id=message.id,
        client_request_id=request.client_request_id,
        request_digest=digest,
        sequence=event.sequence,
        disposition=disposition,
        status=status,
        run_id=active.id if decision else None,
        decision_id=decision.id if decision else None,
        request_json=json.dumps(frozen, sort_keys=True),
    )
    db.add(entry)
    db.flush()
    return _response(db, entry)
