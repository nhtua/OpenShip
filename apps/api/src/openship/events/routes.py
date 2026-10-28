"""Routes for event snapshot and SSE streaming."""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation
from ..database.session import SessionLocal, get_db
from ..runs.models import Event
from .fanout import HEARTBEAT_SECONDS, BrokerUnavailable, EventFanout, SnapshotRequired
from .schemas import SnapshotResponse, StreamResponse
from .service import (
    AuthorizedScope,
    _event_to_read,
    read_snapshot,
    resolve_event_cursor,
    stream_events,
)

router = APIRouter(prefix="/api/conversations", tags=["events"])


def _sse_format(event_id: str, event_type: str, data: dict) -> str:
    """Format an SSE event."""
    payload = json.dumps(data)
    return f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"


def _sse_envelope(event: Event) -> dict:
    """Serialize the same complete event contract used by snapshot reads."""
    return _event_to_read(event).model_dump(mode="json")


def _sse_heartbeat() -> str:
    """Format an SSE heartbeat comment."""
    return ": heartbeat\n\n"


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _snapshot_required_response(conversation_id: uuid.UUID) -> StreamingResponse:
    async def generator():
        yield _sse_format(
            "pruned",
            "snapshot.required",
            {"conversation_id": str(conversation_id)},
        )

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


def _resolve_authorized_sse_cursor(
    user_id: uuid.UUID,
    conversation_id: str,
    header: str | None,
    query: int,
):
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_conversation_id",
                    "message": "Invalid conversation ID format",
                }
            },
        )

    db = SessionLocal()
    try:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.id == conv_id,
                Conversation.user_id == user_id,
            )
            .one_or_none()
        )
        if conversation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "conversation_not_found",
                        "message": "Conversation not found",
                    }
                },
            )
        scope = AuthorizedScope(user_id=user_id, conversation_id=conv_id)
        cursor = resolve_event_cursor(
            db,
            scope,
            header=header,
            query=query,
        )
        return scope, cursor
    finally:
        db.close()


def _read_authorized_snapshot(
    user_id: uuid.UUID,
    conversation_id: str,
    artifact_limit: int,
    artifact_cursor: str | None,
    operation_limit: int,
):
    db = SessionLocal()
    try:
        return read_snapshot(
            db,
            user_id,
            conversation_id,
            artifact_limit=artifact_limit,
            artifact_cursor=artifact_cursor,
            operation_limit=operation_limit,
        )
    finally:
        db.close()


@router.get("/{conversation_id}/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    conversation_id: str,
    artifact_limit: int = Query(default=50, ge=1, le=100),
    artifact_cursor: str | None = None,
    operation_limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_jwt),
):
    """Get the current authoritative state snapshot for a conversation."""
    return await asyncio.to_thread(
        _read_authorized_snapshot,
        user.id,
        conversation_id,
        artifact_limit,
        artifact_cursor,
        operation_limit,
    )


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
    request: Request,
    after_sequence: int = Query(
        -1, ge=-1, description="Cursor: last received event sequence"
    ),
    user: User = Depends(require_jwt),
):
    """SSE stream using JetStream notices and database-authoritative replay."""
    last_event_id = request.headers.get("last-event-id") if request else None
    scope, cursor = await asyncio.to_thread(
        _resolve_authorized_sse_cursor,
        user.id,
        conversation_id,
        last_event_id,
        after_sequence,
    )
    if cursor.snapshot_required:
        return _snapshot_required_response(scope.conversation_id)

    fanout: EventFanout | None = getattr(request.app.state, "event_fanout", None)
    if fanout is None or not fanout.ready:
        return _snapshot_required_response(scope.conversation_id)

    async def event_generator():
        stream = fanout.subscribe_conversation(scope, cursor.sequence)
        next_event = asyncio.create_task(anext(stream))
        try:
            while True:
                done, _pending = await asyncio.wait(
                    (next_event,),
                    timeout=HEARTBEAT_SECONDS,
                )
                if not done:
                    if await request.is_disconnected():
                        return
                    yield _sse_heartbeat()
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    return
                yield _sse_format(event.id, event.type, event.model_dump(mode="json"))
                next_event = asyncio.create_task(anext(stream))
        except (SnapshotRequired, BrokerUnavailable):
            yield _sse_format(
                "pruned",
                "snapshot.required",
                {"conversation_id": str(scope.conversation_id)},
            )
        finally:
            if not next_event.done():
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
            await stream.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )
