"""Event persistence and snapshot/stream service."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Event
from .schemas import EventInput, EventRead, SnapshotResponse, StreamResponse


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


def append_event(
    db: Session,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
    actor_id: uuid.UUID = None,
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
    # Validate conversation exists (within caller's transaction)
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": f"Conversation {conversation_id} not found",
                }
            },
        )

    # Atomically increment sequence using SELECT...FOR UPDATE lock
    # and ORM update. This provides a conversation sequence lock
    # that prevents concurrent writers from reading the same sequence.
    locked_conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .with_for_update()
        .first()
    )
    if locked_conv is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": f"Conversation {conversation_id} not found",
                }
            },
        )

    # Increment within the same transaction
    seq = locked_conv.next_event_sequence
    locked_conv.next_event_sequence = seq + 1

    event = Event(
        conversation_id=conversation_id,
        run_id=run_id,
        sequence=seq,
        type=event_type,
        payload=json.dumps(payload),
        actor_id=actor_id,
    )
    db.add(event)
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

    max_sequence = conv.next_event_sequence - 1
    if max_sequence < 0:
        max_sequence = -1

    return SnapshotResponse(
        conversation_id=str(conv_id),
        sequence=max_sequence,
        events=[_event_to_read(ev) for ev in events],
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
