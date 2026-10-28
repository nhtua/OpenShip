"""Focused outbox relay ordering, retry and envelope contracts."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.events.service import append_event
from src.openship.messaging.models import MessageTransportState
from src.openship.messaging.outbox import enqueue_run
from src.openship.messaging.relay import (
    claim_outbox_batch,
    next_outbox_due_at,
    publish_pending_batch,
)
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.workspace.models import Project

_FIXTURE_IDS: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []


@pytest.fixture(autouse=True)
def cleanup_relay_fixtures():
    yield
    if not _FIXTURE_IDS:
        return
    with SessionLocal() as db:
        conversation_ids = [item[2] for item in _FIXTURE_IDS]
        project_ids = [item[1] for item in _FIXTURE_IDS]
        user_ids = [item[0] for item in _FIXTURE_IDS]
        db.query(Outbox).filter(Outbox.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        db.query(Event).filter(Event.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        run_ids = db.query(Run.id).filter(Run.conversation_id.in_(conversation_ids))
        db.query(RunJob).filter(RunJob.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
        db.query(Run).filter(Run.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        db.query(Conversation).filter(Conversation.id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        db.query(Project).filter(Project.id.in_(project_ids)).delete(
            synchronize_session=False
        )
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.commit()
    _FIXTURE_IDS.clear()


def _conversation(db):
    user = User(
        username=f"relay_{uuid.uuid4().hex[:12]}",
        email=f"relay_{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Relay test")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        project_id=project.id,
        title="Relay test",
        next_event_sequence=0,
    )
    db.add(conversation)
    db.commit()
    _FIXTURE_IDS.append((user.id, project.id, conversation.id))
    return conversation


class _Ack:
    stream = "OS_EVENTS"
    seq = 1
    duplicate = False


class _LostAckOnce:
    def __init__(self):
        self.publications = []

    async def publish(self, subject, body, *, headers):
        self.publications.append((subject, body, headers))
        if len(self.publications) == 1:
            raise TimeoutError("simulated lost publish confirmation")
        return _Ack()


def test_lost_confirmation_republishes_immutable_command_after_generation_advance():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        conversation = _conversation(db)
        conversation_id = conversation.id
        run = Run(
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            status="queued",
            fence=0,
        )
        db.add(run)
        db.flush()
        job = RunJob(run_id=run.id, fence=0, dispatch_generation=1)
        db.add(job)
        db.flush()
        epoch = db.get(MessageTransportState, 1).transport_epoch
        first_id = enqueue_run(db, run.id, 1, epoch=epoch)
        run_id = run.id
        db.commit()

    (original,) = claim_outbox_batch(
        conversation_id=conversation_id, now=now + timedelta(seconds=1), lease_seconds=5
    )
    with SessionLocal() as db:
        job = db.query(RunJob).filter_by(run_id=run_id).one()
        job.dispatch_generation = 2
        replacement_id = enqueue_run(db, run_id, 2, epoch=epoch)
        event = append_event(db, conversation_id, run_id, "run.requeued", {})
        event_id = event.id
        db.commit()

    retried = claim_outbox_batch(
        conversation_id=conversation_id, now=now + timedelta(seconds=7), lease_seconds=5
    )
    by_id = {record.id: record for record in retried}
    assert by_id[first_id].body == original.body
    assert by_id[first_id].message_id == original.message_id
    assert json.loads(by_id[first_id].body)["dispatch_generation"] == 1
    assert json.loads(by_id[replacement_id].body)["dispatch_generation"] == 2
    assert any(
        json.loads(record.body).get("event_id") == str(event_id) for record in retried
    )


def test_event_claims_publish_only_authorized_reference():
    with SessionLocal() as db:
        conversation = _conversation(db)
        conversation_id = conversation.id
        first = append_event(
            db,
            conversation_id,
            None,
            "turn_submitted",
            {"content": "private prompt"},
        )
        second = append_event(
            db,
            conversation_id,
            None,
            "run.succeeded",
            {"content": "private answer"},
        )
        first_id = first.id
        second_id = second.id
        db.commit()

    first_batch = claim_outbox_batch(conversation_id=conversation_id)
    assert len(first_batch) == 1
    first_envelope = json.loads(first_batch[0].body)
    assert first_envelope == {"event_id": str(first_id)}
    with SessionLocal() as db:
        installation_id = db.get(MessageTransportState, 1).installation_id
    assert first_batch[0].subject == (
        f"openship.{installation_id.hex}.v1.event.{conversation_id.hex}"
    )
    assert "private prompt" not in first_batch[0].body.decode()
    assert "private answer" not in first_batch[0].body.decode()

    from src.openship.messaging.relay import _settle_publication

    assert _settle_publication(SessionLocal, first_batch[0], error=None)
    second_batch = claim_outbox_batch(conversation_id=conversation_id)
    assert len(second_batch) == 1
    second_envelope = json.loads(second_batch[0].body)
    assert second_envelope == {"event_id": str(second_id)}


@pytest.mark.asyncio
async def test_lost_puback_retries_the_same_message_identity(monkeypatch):
    async def run_inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    with SessionLocal() as db:
        conversation = _conversation(db)
        conversation_id = conversation.id
        event = append_event(db, conversation_id, None, "artifact.created", {"size": 8})
        db.commit()
        event_id = event.id

    js = _LostAckOnce()
    monkeypatch.setattr(asyncio, "to_thread", run_inline)
    first = await publish_pending_batch(js, conversation_id=conversation_id)
    assert first.claimed == 1
    assert first.published == 0
    with SessionLocal() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        assert row.processed_at is None
        assert row.last_error == "TimeoutError"
        message_id = row.message_id
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    second = await publish_pending_batch(js, conversation_id=conversation_id)
    assert second.claimed == 1
    assert second.published == 1
    assert [item[2]["Nats-Msg-Id"] for item in js.publications] == [
        str(message_id),
        str(message_id),
    ]
    with SessionLocal() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        assert row.processed_at is not None
        assert row.last_error is None


def test_expired_publisher_claim_can_be_reclaimed_with_same_message_identity():
    with SessionLocal() as db:
        conversation = _conversation(db)
        conversation_id = conversation.id
        event = append_event(db, conversation_id, None, "run.started", {})
        event_id = event.id
        db.commit()
    now = datetime.now(UTC)

    first = claim_outbox_batch(
        now=now,
        lease_seconds=5,
        conversation_id=conversation_id,
    )
    assert len(first) == 1
    assert (
        claim_outbox_batch(
            now=now + timedelta(seconds=4),
            lease_seconds=5,
            conversation_id=conversation_id,
        )
        == []
    )

    reclaimed = claim_outbox_batch(
        now=now + timedelta(seconds=6),
        lease_seconds=5,
        conversation_id=conversation_id,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].claim_id != first[0].claim_id
    assert reclaimed[0].message_id == first[0].message_id
    with SessionLocal() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        assert row.publish_attempts == 2


def test_next_due_skips_later_event_while_an_earlier_event_is_delayed():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        conversation = _conversation(db)
        conversation_id = conversation.id
        append_event(db, conversation_id, None, "first", {})
        append_event(db, conversation_id, None, "second", {})
        db.flush()
        rows = (
            db.query(Outbox)
            .filter(Outbox.conversation_id == conversation_id)
            .order_by(Outbox.created_at.asc(), Outbox.id.asc())
            .all()
        )
        first = min(rows, key=lambda row: row.event_id.hex)
        # Event sequence is the source of order even when creation timestamps
        # tie and the random outbox UUID sorts differently.
        event = db.get(Event, first.event_id)
        if event.sequence != 0:
            first = next(
                row for row in rows if db.get(Event, row.event_id).sequence == 0
            )
        first.next_attempt_at = now + timedelta(seconds=10)
        second = next(row for row in rows if row.id != first.id)
        second.next_attempt_at = now - timedelta(seconds=1)
        db.commit()

    due = next_outbox_due_at(conversation_id=conversation_id)
    assert due is not None
    assert due >= now + timedelta(seconds=9)
