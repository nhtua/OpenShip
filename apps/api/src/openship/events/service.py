"""Event persistence and snapshot/stream service."""

import json
import uuid

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..chat.models import Conversation, Message
from ..runs.models import Event, ProviderAttempt, Run
from ..runs.schemas import (
    _attempt_projection,
    _error_projection,
    _model_projection,
    _usage_projection,
)
from .schemas import (
    EventRead,
    MessageSummary,
    RunSummary,
    SnapshotResponse,
    StreamResponse,
)


def _event_to_read(ev: Event) -> EventRead:
    """Convert Event model to EventRead schema."""
    payload = {}
    try:
        payload = json.loads(ev.payload) if ev.payload else {}
    except (json.JSONDecodeError, TypeError):
        payload = {"raw": ev.payload}

    return EventRead(
        id=str(ev.id),
        conversation_id=str(ev.conversation_id),
        run_id=str(ev.run_id) if ev.run_id else None,
        sequence=ev.sequence,
        type=ev.type,
        payload=payload,
        created_at=ev.created_at.isoformat() if ev.created_at else "",
    )


def _message_to_summary(msg: Message) -> MessageSummary:
    """Convert Message model to MessageSummary schema."""
    return MessageSummary(
        id=str(msg.id),
        role=msg.role,
        content=msg.content,
        run_id=str(msg.run_id) if msg.run_id else None,
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )


def _run_to_summary(run: Run, attempts=()) -> RunSummary:
    """Convert Run model to RunSummary schema: only the public projection."""
    return RunSummary(
        id=str(run.id),
        status=run.status,
        attempt=run.attempt,
        started_at=run.created_at.isoformat() if run.created_at else None,
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
        error_code=run.error_code,
        model=_model_projection(run.model_configuration, attempts),
        usage=_usage_projection(run.usage),
        error=_error_projection(run.error_code),
        provider_attempts=[_attempt_projection(attempt) for attempt in attempts],
    )


def append_event(
    db: Session,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
    actor_id: uuid.UUID | None = None,
) -> Event:
    """Append a durable semantic event to a conversation.

    Uses the caller's transaction and increments the conversation's
    next_event_sequence atomically via UPDATE...RETURNING. The event is
    committed with the caller's transaction.

    Args:
        db: Database session (caller's transaction).
        conversation_id: Target conversation ID.
        run_id: Associated run ID (optional).
        event_type: Semantic event type (e.g., 'run_started', 'turn_submitted').
        payload: JSON-serializable payload (omit prompts/secrets).
        actor_id: ID of the actor who triggered the event (optional).

    Returns:
        The persisted Event object.
    """
    # Flush pending changes first, then allocate a sequence with one atomic
    # update. Reading an ORM Conversation here can return a stale identity-map
    # value after a tool gateway session has appended events during this run.
    db.flush()
    row = db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(next_event_sequence=Conversation.next_event_sequence + 1)
        .returning(Conversation.next_event_sequence, Conversation.project_id)
        .execution_options(synchronize_session=False)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": f"Conversation {conversation_id} not found",
                }
            },
        )

    next_sequence, project_id = row
    seq = next_sequence - 1
    conv = db.get(Conversation, conversation_id)
    if conv is not None:
        set_committed_value(conv, "next_event_sequence", next_sequence)

    event = Event(
        project_id=project_id,
        conversation_id=conversation_id,
        run_id=run_id,
        sequence=seq,
        type=event_type,
        payload=json.dumps(payload),
        actor_id=actor_id,
    )
    db.add(event)
    # Make the sequence increment visible to another append_event call in the
    # same transaction, even when that session has a cached Conversation row.
    db.flush()
    return event


def read_snapshot(db: Session, user_id: uuid.UUID, conversation_id: str) -> SnapshotResponse:
    """Read the current authoritative state snapshot for a conversation.

    The snapshot captures the maximum committed sequence and all events
    up to that point. This is authoritative over event replay.

    Args:
        db: Database session.
        user_id: The requesting user (for ownership check).
        conversation_id: Target conversation ID (string or UUID).

    Returns:
        SnapshotResponse with conversation state and event list.
    """
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
        )

    conv = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )

    # Read all events up to the current committed sequence
    events = (
        db.query(Event)
        .filter(
            Event.conversation_id == conv_id,
            Event.sequence < conv.next_event_sequence,
        )
        .order_by(Event.sequence.asc())
        .all()
    )

    # Read messages
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    # Read runs
    runs = (
        db.query(Run)
        .filter(Run.conversation_id == conv_id)
        .order_by(Run.created_at.asc())
        .all()
    )

    # Attempts belong to the run's own story, in the order the worker made them
    recorded_attempts: dict[uuid.UUID, list[ProviderAttempt]] = {}
    if runs:
        for attempt in db.query(ProviderAttempt).filter(
            ProviderAttempt.run_id.in_([run.id for run in runs])
        ).order_by(ProviderAttempt.ordinal).all():
            recorded_attempts.setdefault(attempt.run_id, []).append(attempt)

    max_sequence = conv.next_event_sequence - 1
    if max_sequence < 0:
        max_sequence = -1

    return SnapshotResponse(
        conversation_id=str(conv_id),
        sequence=max_sequence,
        events=[_event_to_read(ev) for ev in events],
        messages=[_message_to_summary(msg) for msg in messages],
        runs=[_run_to_summary(run, recorded_attempts.get(run.id, ())) for run in runs],
    )


def stream_events(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: str,
    after_sequence: int = -1,
) -> StreamResponse:
    """Stream events after a cursor (last received sequence).

    Reads committed events in order starting from the cursor.

    Args:
        db: Database session.
        user_id: The requesting user (for ownership check).
        conversation_id: Target conversation ID.
        after_sequence: Cursor — return events with sequence > this value.
                        Use -1 to get all events.

    Returns:
        StreamResponse with events after the cursor.
    """
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
        )

    conv = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )

    # Validate cursor is not in the future
    if after_sequence >= conv.next_event_sequence:
        return StreamResponse(
            conversation_id=str(conv_id),
            after_sequence=after_sequence,
            events=[],
        )

    # Read events after cursor
    events = (
        db.query(Event)
        .filter(
            Event.conversation_id == conv_id,
            Event.sequence > after_sequence,
            Event.sequence < conv.next_event_sequence,
        )
        .order_by(Event.sequence.asc())
        .all()
    )

    return StreamResponse(
        conversation_id=str(conv_id),
        after_sequence=after_sequence,
        events=[_event_to_read(ev) for ev in events],
    )
