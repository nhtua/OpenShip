"""Event persistence and snapshot/stream service."""

import json
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..artifacts.contracts import ArtifactSummary
from ..artifacts.models import Artifact
from ..chat.lifecycle import ConversationLifecycleError, get_owned_conversation_scope
from ..chat.models import Conversation, Message
from ..execution.pagination import decode_cursor, encode_cursor
from ..execution.reads import read_operation_page
from ..runs.models import Event, ProviderAttempt, Run
from ..runs.schemas import (
    _attempt_projection,
    _error_projection,
    _model_projection,
    _usage_projection,
)
from ..workspace.lifecycle import format_archive_timestamp
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


def _run_to_summary(run: Run, attempts=(), operation_page=None) -> RunSummary:
    """Convert Run model to RunSummary schema: only the public projection."""
    return RunSummary(
        id=str(run.id),
        status=run.status,
        attempt=run.attempt,
        started_at=run.created_at.isoformat() if run.created_at else None,
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
        error_code=run.error_code,
        kind=run.kind or "chat",
        graph_version=run.graph_version or 1,
        model=_model_projection(run.model_configuration, attempts),
        usage=_usage_projection(run.usage),
        error=_error_projection(run.error_code),
        provider_attempts=[_attempt_projection(attempt) for attempt in attempts],
        operations=list(operation_page.items) if operation_page else [],
        operations_next_cursor=operation_page.next_cursor if operation_page else None,
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


def read_snapshot(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: str,
    *,
    artifact_limit: int = 50,
    artifact_cursor: str | None = None,
    operation_limit: int = 50,
) -> SnapshotResponse:
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

    try:
        conv, project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=conv_id,
        )
    except ConversationLifecycleError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        ) from None

    if not 1 <= artifact_limit <= 100 or not 1 <= operation_limit <= 100:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_snapshot_limit", "message": "Snapshot page sizes must be between 1 and 100"}},
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

    artifact_query = db.query(Artifact).filter(
        Artifact.conversation_id == conv_id,
        Artifact.status.in_(("ready", "unavailable", "deleting", "deleted")),
    )
    if artifact_cursor:
        try:
            cursor_created_at, cursor_id = decode_cursor(artifact_cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "invalid_artifact_cursor", "message": "Artifact cursor is invalid"}},
            ) from exc
        artifact_query = artifact_query.filter(
            or_(
                Artifact.created_at < cursor_created_at,
                and_(Artifact.created_at == cursor_created_at, Artifact.id < cursor_id),
            )
        )
    artifact_rows = artifact_query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(artifact_limit + 1).all()
    artifacts_have_more = len(artifact_rows) > artifact_limit
    artifact_rows = artifact_rows[:artifact_limit]
    artifact_summaries = [
        ArtifactSummary(
            id=item.id,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            media_type=item.media_type,
            conversation_id=item.conversation_id,
            project_id=item.project_id,
            name=item.display_name,
            source=item.source,
            status=item.status,
            created_at=item.created_at,
            mutability=item.mutability,
        )
        for item in artifact_rows
    ]

    run_summaries = []
    for run in runs:
        operation_page = read_operation_page(db, run.id, limit=operation_limit)
        run_summaries.append(
            _run_to_summary(run, recorded_attempts.get(run.id, ()), operation_page)
        )

    return SnapshotResponse(
        conversation_id=str(conv_id),
        project_id=str(conv.project_id),
        archived_at=format_archive_timestamp(conv.archived_at),
        project_archived_at=format_archive_timestamp(project.archived_at),
        sequence=max_sequence,
        events=[_event_to_read(ev) for ev in events],
        messages=[_message_to_summary(msg) for msg in messages],
        runs=run_summaries,
        artifacts=artifact_summaries,
        artifacts_next_cursor=(
            encode_cursor(artifact_rows[-1].created_at, artifact_rows[-1].id)
            if artifacts_have_more and artifact_rows
            else None
        ),
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
