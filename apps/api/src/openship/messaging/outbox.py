"""Transactional creation and admission controls for messaging records."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..runs.models import Event, Outbox, Run, RunJob
from .models import MessageTransportState

OUTBOX_MAX_PENDING = 10_000
OUTBOX_MAX_AGE_SECONDS = 300
_PUBLISHABLE = ("event", "command", "control", "failure", "maintenance")


class MessagingBacklogError(HTTPException):
    """New submissions are paused because the durable transport backlog is full."""

    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=503,
            detail={
                "error": {
                    "code": "messaging_unavailable",
                    "message": "Messaging is temporarily unavailable; retry shortly.",
                }
            },
            headers={"Retry-After": "30"},
        )
        self.message = message

    def __str__(self) -> str:
        return self.message


def _state(db: Session, *, lock: bool = False) -> MessageTransportState:
    query = db.query(MessageTransportState).filter(MessageTransportState.id == 1)
    if lock:
        query = query.with_for_update()
    state = query.one_or_none()
    if state is None:
        raise RuntimeError("messaging state is missing; run database migrations first")
    return state


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_subject(state: MessageTransportState, conversation_id: uuid.UUID) -> str:
    return f"openship.{state.installation_id.hex}.v1.event.{conversation_id.hex}"


def enqueue_event(db: Session, event: Event) -> uuid.UUID:
    """Create one reference-only event publication in the caller transaction."""
    db.flush()
    existing = (
        db.query(Outbox)
        .filter(Outbox.event_id == event.id, Outbox.destination == "event")
        .one_or_none()
    )
    if existing is not None:
        return existing.id

    state = _state(db)
    message_id = uuid.uuid5(state.installation_id, f"event:{event.id}")
    row = Outbox(
        conversation_id=event.conversation_id,
        event_id=event.id,
        run_id=event.run_id,
        type=event.type,
        payload=_json({"event_id": str(event.id)}),
        destination="event",
        schema_version=1,
        message_id=message_id,
        subject=_event_subject(state, event.conversation_id),
        transport_epoch=state.transport_epoch,
        next_attempt_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row.id


def enqueue_run(
    db: Session,
    run_id: uuid.UUID,
    generation: int,
    *,
    epoch: uuid.UUID,
) -> uuid.UUID:
    """Create a run command only for its current queued generation and epoch."""
    db.flush()
    state = _state(db)
    if state.transport_epoch != epoch:
        raise ValueError("command transport epoch is stale")
    run = db.query(Run).filter(Run.id == run_id).one_or_none()
    job = db.query(RunJob).filter(RunJob.run_id == run_id).one_or_none()
    if run is None or run.status != "queued" or job is None:
        raise ValueError("run command requires a queued run and job")
    if job.dispatch_generation != generation:
        raise ValueError("run command generation is stale")

    existing = (
        db.query(Outbox)
        .filter(
            Outbox.destination == "command",
            Outbox.run_id == run_id,
            Outbox.dispatch_generation == generation,
        )
        .one_or_none()
    )
    if existing is not None:
        if existing.transport_epoch != epoch:
            raise ValueError("run command generation already belongs to another epoch")
        return existing.id

    message_id = uuid.uuid5(
        state.installation_id, f"run.execute:{run_id}:{generation}:{epoch}"
    )
    row = Outbox(
        conversation_id=run.conversation_id,
        event_id=None,
        type="run.execute",
        payload=_json(
            {
                "run_id": str(run_id),
                "dispatch_generation": generation,
                "transport_epoch": str(epoch),
            }
        ),
        destination="command",
        schema_version=1,
        message_id=message_id,
        subject=f"openship.{state.installation_id.hex}.v1.command.run.execute",
        run_id=run_id,
        dispatch_generation=generation,
        transport_epoch=epoch,
        next_attempt_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row.id


def enqueue_control(db: Session, run: Run, *, event_id: uuid.UUID) -> uuid.UUID:
    """Create a stable control command causally linked to a run event."""
    db.flush()
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.run_id == run.id)
        .one_or_none()
    )
    if event is None:
        raise ValueError("control event does not belong to the run")
    state = _state(db)
    message_id = uuid.uuid5(
        state.installation_id, f"control.cancel:{run.id}:{event_id}"
    )
    existing = (
        db.query(Outbox)
        .filter(Outbox.destination == "control", Outbox.message_id == message_id)
        .one_or_none()
    )
    if existing is not None:
        return existing.id
    row = Outbox(
        conversation_id=run.conversation_id,
        event_id=event_id,
        type="run.cancel",
        payload=_json({"run_id": str(run.id), "event_id": str(event_id)}),
        destination="control",
        schema_version=1,
        message_id=message_id,
        subject=f"openship.{state.installation_id.hex}.v1.control.{run.id.hex}",
        run_id=run.id,
        transport_epoch=state.transport_epoch,
        next_attempt_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row.id


def check_submission_capacity(db: Session) -> None:
    """Serialize new-work admission against bounded unpublished transport work."""
    state = _state(db, lock=True)
    if not state.active:
        return
    if not state.admission_open:
        raise MessagingBacklogError("new message admission is closed")
    pending = db.query(func.count(Outbox.id)).filter(
        Outbox.destination.in_(_PUBLISHABLE), Outbox.processed_at.is_(None)
    ).scalar()
    if pending >= OUTBOX_MAX_PENDING:
        raise MessagingBacklogError("messaging backlog has reached its pending limit")

    oldest = (
        db.query(Outbox.created_at)
        .filter(Outbox.destination.in_(_PUBLISHABLE), Outbox.processed_at.is_(None))
        .order_by(Outbox.created_at.asc())
        .limit(1)
        .scalar()
    )
    if oldest is None:
        return
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    age = datetime.now(UTC) - oldest.astimezone(UTC)
    if age >= timedelta(seconds=OUTBOX_MAX_AGE_SECONDS):
        raise MessagingBacklogError("messaging backlog oldest publication is at least 300 seconds old")
