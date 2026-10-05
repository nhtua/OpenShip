"""Routes for event snapshot and SSE streaming."""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation
from ..database.session import get_db
from ..runs.models import Event
from .schemas import SnapshotResponse, StreamResponse
from .service import read_snapshot, stream_events

router = APIRouter(prefix="/api/conversations", tags=["events"])


def _sse_format(event_id: str, event_type: str, data: dict) -> str:
    """Format an SSE event."""
    payload = json.dumps(data)
    return f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"


def _sse_heartbeat() -> str:
    """Format an SSE heartbeat comment."""
    return ": heartbeat\n\n"


@router.get("/{conversation_id}/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Get the current authoritative state snapshot for a conversation."""
    return read_snapshot(db, user.id, conversation_id)


@router.get("/{conversation_id}/events", response_model=StreamResponse)
async def get_events_after(
    conversation_id: str,
    after_sequence: int = Query(-1, ge=-1, description="Cursor: last received event sequence"),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Get events after a sequence cursor (non-streaming)."""
    return stream_events(db, user.id, conversation_id, after_sequence)


@router.get("/{conversation_id}/events/stream")
async def stream_event_stream(
    conversation_id: str,
    after_sequence: int = Query(-1, ge=-1, description="Cursor: last received event sequence"),
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """SSE stream of events after a cursor.

    Uses database polling as Phase 2 fallback. Emits events as they are
    committed and sends periodic heartbeats. For a pruned cursor, emits
    'snapshot.required' then closes.

    Supports the Last-Event-ID header as an alternative cursor: if provided,
    it takes precedence over the after_sequence query parameter.
    """
    # Check for Last-Event-ID header as alternative cursor
    last_event_id = request.headers.get("last-event-id") if request else None
    if last_event_id is not None:
        try:
            # Last-Event-ID should be the sequence number
            after_sequence = int(last_event_id)
        except (ValueError, TypeError):
            pass  # Fall back to query parameter
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
        )

    conv = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == user.id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )

    # Validate cursor is not in the future
    if after_sequence >= conv.next_event_sequence:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "future_cursor", "message": "Cursor is past the latest event"}},
        )

    # Check if cursor is pruned (no events before cutoff)
    min_sequence = conv.next_event_sequence - 100  # Retention: keep last 100
    if after_sequence < min_sequence - 1:
        # Cursor is too old — tell client to snapshot
        async def pruned_generator():
            yield _sse_format("pruned", "snapshot.required", {"conversation_id": str(conv_id)})

        return StreamingResponse(
            pruned_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def event_generator():
        """Generate SSE events by polling the database."""
        last_sequence = after_sequence
        last_poll = time.time()
        heartbeat_interval = 15  # seconds
        poll_interval = 1  # seconds

        try:
            while True:
                # Check for heartbeat
                now = time.time()
                if now - last_poll >= heartbeat_interval:
                    yield _sse_heartbeat()
                    last_poll = now

                # Poll for new events (no upper bound to catch events committed after subscription)
                events = (
                    db.query(Event)
                    .filter(
                        Event.conversation_id == conv_id,
                        Event.sequence > last_sequence,
                    )
                    .order_by(Event.sequence.asc())
                    .all()
                )

                if events:
                    for ev in events:
                        payload = {}
                        try:
                            payload = json.loads(ev.payload) if ev.payload else {}
                        except (json.JSONDecodeError, TypeError):
                            payload = {"raw": ev.payload}

                        yield _sse_format(
                            str(ev.id),
                            ev.type,
                            {
                                "id": str(ev.id),
                                "sequence": ev.sequence,
                                "type": ev.type,
                                "payload": payload,
                            },
                        )
                        last_sequence = ev.sequence

                # Sleep and retry (async)
                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
