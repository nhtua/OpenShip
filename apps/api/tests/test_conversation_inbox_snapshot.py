"""Snapshot inbox states and admission events tell one durable story."""

import json
import uuid
from datetime import UTC, datetime

from src.openship.events.service import read_snapshot
from src.openship.runs.inbox.admission import submit_message
from src.openship.runs.inbox.contracts import MessageAdmissionRequest
from src.openship.runs.inbox.promotion import promote_next_message
from src.openship.runs.models import Event, RunJob
from src.openship.runs.tasks.decisions import park_task_wait
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_state import _identity


def _admit(db, user, conversation, content: str):
    return submit_message(
        db,
        user_id=user.id,
        conversation_id=conversation.id,
        request=MessageAdmissionRequest(
            content=content, client_request_id=str(uuid.uuid4())
        ),
    )


def _admitted_event(db, run_id, message_id):
    return (
        db.query(Event)
        .filter_by(run_id=run_id, type="message.admitted")
        .filter(Event.payload.contains(str(message_id)))
        .one()
    )


def test_snapshot_matches_inbox_admission_events():
    with TestSessionLocal() as db:
        user, _project, conversation, _run, _claim = _identity(db)
        db.commit()
        first = _admit(db, user, conversation, "first")
        second = _admit(db, user, conversation, "second")
        db.commit()

        snapshot = read_snapshot(db, user.id, str(conversation.id))
        assert [entry.message_id for entry in snapshot.inbox] == [
            first.message_id,
            second.message_id,
        ]
        # Admitted while a run owns the slot: both wait as queued turns.
        assert [entry.status for entry in snapshot.inbox] == ["queued", "queued"]
        assert [entry.disposition for entry in snapshot.inbox] == [
            "queued",
            "queued",
        ]
        for entry in snapshot.inbox:
            assert entry.run_id is None and entry.request_id is None


def test_snapshot_binds_clarification_reply_to_the_open_request():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=datetime.now(UTC)
        )
        db.commit()
        reply = _admit(db, user, conversation, "the morning CSV")
        db.commit()

        snapshot = read_snapshot(db, user.id, str(conversation.id))
        entry = next(
            item for item in snapshot.inbox if item.message_id == reply.message_id
        )
        assert entry.disposition == "clarification"
        assert entry.status == "attached"
        assert entry.request_id is not None
        assert entry.request_id == reply.request_id
        assert entry.run_id == run.id


def test_promoted_message_projects_the_run_that_adopted_it():
    with TestSessionLocal() as db:
        user, project, conversation, run, _claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        queued = _admit(db, user, conversation, "next turn")
        db.commit()

        run.status = "completed"
        run.ended_at = datetime.now(UTC)
        db.query(RunJob).filter_by(run_id=run.id).one().owner_id = None
        db.flush()
        promoted = promote_next_message(db, conversation_id=conversation.id)
        db.commit()
        assert promoted is not None and promoted.id != run.id

        snapshot = read_snapshot(db, user.id, str(conversation.id))
        entry = next(
            item for item in snapshot.inbox if item.message_id == queued.message_id
        )
        assert entry.status == "promoted"
        assert entry.run_id == promoted.id
        assert entry.run_id != run.id

        changed = (
            db.query(Event)
            .filter_by(run_id=promoted.id, type="message.status_changed")
            .one()
        )
        payload = json.loads(changed.payload)
        assert payload["message_id"] == str(queued.message_id)
        assert payload["status"] == entry.status
        assert payload["run_id"] == str(entry.run_id)


def test_inbox_projection_is_bounded_and_oldest_first():
    from src.openship.runs.inbox.projection import read_inbox_summaries

    with TestSessionLocal() as db:
        user, _project, conversation, _run, _claim = _identity(db)
        db.commit()
        for index in range(3):
            _admit(db, user, conversation, f"message {index}")
        db.commit()

        bounded = read_inbox_summaries(
            db, conversation_id=conversation.id, limit=2
        )
        assert len(bounded) == 2
        # The cap keeps the newest rows but still reports them oldest first.
        assert bounded[0].sequence < bounded[1].sequence
