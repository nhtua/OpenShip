"""Transactional outbox contracts shared by events, runs and controls."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.events.service import append_event
from src.openship.messaging.models import MessageTransportState
from src.openship.messaging.outbox import (
    MessagingBacklogError,
    check_submission_capacity,
    enqueue_control,
    enqueue_event,
    enqueue_run,
)
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.runs.service import request_cancel
from src.openship.workspace.models import Project

_TEST_RUN_IDS: set[uuid.UUID] = set()


@pytest.fixture(autouse=True)
def cleanup_messaging_fixtures():
    yield
    if not _TEST_RUN_IDS:
        return
    with SessionLocal() as db:
        db.query(Outbox).filter(Outbox.run_id.in_(_TEST_RUN_IDS)).delete(
            synchronize_session=False
        )
        db.query(Run).filter(Run.id.in_(_TEST_RUN_IDS)).update(
            {Run.status: "stale"}, synchronize_session=False
        )
        db.commit()
    _TEST_RUN_IDS.clear()


def _setup_run(db):
    db.query(Outbox).delete(synchronize_session=False)
    state = db.get(MessageTransportState, 1)
    state.active = False
    state.admission_open = True
    state.execution_open = True
    user = User(
        username=f"msg_{uuid.uuid4().hex[:12]}",
        email=f"msg_{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Messaging test")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        project_id=project.id,
        title="Messaging test",
        next_event_sequence=0,
    )
    db.add(conversation)
    db.flush()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="queued",
        fence=0,
    )
    db.add(run)
    db.flush()
    _TEST_RUN_IDS.add(run.id)
    job = RunJob(run_id=run.id, fence=0, dispatch_generation=1)
    db.add(job)
    db.flush()
    assert state is not None
    db.commit()
    return user, project, conversation, run, job, state


def test_event_outbox_is_a_reference_and_shares_the_callers_transaction():
    db = SessionLocal()
    try:
        user, _project, conversation, run, _job, _state = _setup_run(db)
        event = append_event(
            db,
            conversation.id,
            run.id,
            "turn_submitted",
            {"content": "prompt must stay in PostgreSQL"},
            actor_id=user.id,
        )
        db.flush()

        row = db.query(Outbox).filter(Outbox.event_id == event.id).one()
        assert row.destination == "event"
        assert row.message_id is not None
        assert row.subject.endswith(str(conversation.id).replace("-", ""))
        assert "prompt must stay" not in row.payload
        assert json.loads(row.payload)["event_id"] == str(event.id)

        db.rollback()
        with SessionLocal() as reader:
            assert reader.get(Event, event.id) is None
            assert reader.query(Outbox).filter(Outbox.event_id == event.id).count() == 0
    finally:
        db.close()


def test_enqueue_event_is_idempotent_and_keeps_the_application_event_id():
    with SessionLocal() as db:
        _user, _project, conversation, run, _job, _state = _setup_run(db)
        event = append_event(db, conversation.id, run.id, "run.failed", {"error_code": "provider_error"})
        first_id = enqueue_event(db, event)
        second_id = enqueue_event(db, event)
        rows = db.query(Outbox).filter(Outbox.event_id == event.id).all()

        assert first_id == second_id
        assert len(rows) == 1
        assert rows[0].destination == "event"
        assert json.loads(rows[0].payload) == {"event_id": str(event.id)}


def test_run_commands_are_idempotent_per_generation_and_epoch():
    with SessionLocal() as db:
        _user, _project, _conversation, run, job, state = _setup_run(db)
        first = enqueue_run(db, run.id, 1, epoch=state.transport_epoch)
        retry = enqueue_run(db, run.id, 1, epoch=state.transport_epoch)
        assert first == retry

        job.dispatch_generation = 2
        db.flush()
        resumed = enqueue_run(db, run.id, 2, epoch=state.transport_epoch)
        assert resumed != first

        state.transport_epoch = uuid.uuid4()
        job.dispatch_generation = 3
        db.flush()
        requeued = enqueue_run(db, run.id, 3, epoch=state.transport_epoch)
        assert requeued not in {first, resumed}

        commands = (
            db.query(Outbox)
            .filter(Outbox.destination == "command", Outbox.run_id == run.id)
            .order_by(Outbox.dispatch_generation)
            .all()
        )
        assert [row.dispatch_generation for row in commands] == [1, 2, 3]
        assert len({row.message_id for row in commands}) == 3
        for row in commands:
            envelope = json.loads(row.payload)
            assert envelope["run_id"] == str(run.id)
            assert envelope["dispatch_generation"] == row.dispatch_generation
            assert "content" not in envelope


def test_run_command_is_rolled_back_with_its_admission_transaction():
    with SessionLocal() as db:
        _user, _project, _conversation, run, _job, state = _setup_run(db)
        command_id = enqueue_run(db, run.id, 1, epoch=state.transport_epoch)
        db.rollback()

    with SessionLocal() as reader:
        assert reader.get(Outbox, command_id) is None


def test_run_command_rejects_a_stale_generation_or_epoch():
    with SessionLocal() as db:
        _user, _project, _conversation, run, job, state = _setup_run(db)
        with pytest.raises(ValueError, match="generation"):
            enqueue_run(db, run.id, 2, epoch=state.transport_epoch)

        job.dispatch_generation = 2
        db.flush()
        with pytest.raises(ValueError, match="epoch"):
            enqueue_run(db, run.id, 2, epoch=uuid.uuid4())


def test_control_message_is_distinct_from_its_event_and_stable():
    with SessionLocal() as db:
        _user, _project, conversation, run, _job, state = _setup_run(db)
        event = append_event(db, conversation.id, run.id, "run_cancelled", {"status": "cancelled"})
        event_id = enqueue_event(db, event)
        first = enqueue_control(db, run, event_id=event.id)
        retry = enqueue_control(db, run, event_id=event.id)

        assert first == retry
        assert first != event_id
        row = db.query(Outbox).filter(Outbox.id == first).one()
        assert row.destination == "control"
        assert row.run_id == run.id
        assert row.transport_epoch == state.transport_epoch


def _insert_pending_commands(db, conversation_id, run_id, epoch, count, created_at=None):
    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid.uuid4(),
            "conversation_id": conversation_id,
            "event_id": None,
            "type": "run.execute",
            "payload": "{}",
            "created_at": created_at or now,
            "processed_at": None,
            "destination": "command",
            "schema_version": 1,
            "message_id": uuid.uuid4(),
            "subject": "openship.test.v1.command.run.execute",
            "run_id": run_id,
            "dispatch_generation": index + 1,
            "transport_epoch": epoch,
            "publish_attempts": 0,
            "next_attempt_at": now,
        }
        for index in range(count)
    ]
    db.execute(Outbox.__table__.insert(), rows)
    db.flush()


def test_submission_backlog_limits_do_not_disable_cancellation():
    with SessionLocal() as db:
        user, _project, conversation, run, _job, state = _setup_run(db)
        state.active = True
        db.flush()
        _insert_pending_commands(db, conversation.id, run.id, state.transport_epoch, 10_000)
        db.commit()

        with pytest.raises(MessagingBacklogError, match="pending"):
            check_submission_capacity(db)

        # Cancellation remains a durable user command even when ordinary
        # admissions are back-pressured by an unavailable relay.
        cancelled = request_cancel(db, user.id, str(run.id))
        assert cancelled.status == "cancelled"
        db.refresh(cancelled)
        assert (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "run_cancelled")
            .count()
            == 1
        )


def test_backlog_rejection_does_not_create_partial_admission(monkeypatch):
    from src.openship.runs import service as run_service

    with SessionLocal() as db:
        user, _project, conversation, run, _job, state = _setup_run(db)
        run.status = "completed"
        state.active = True
        db.flush()
        _insert_pending_commands(db, conversation.id, run.id, state.transport_epoch, 10_000)
        db.commit()

        before = (
            db.query(Run).count(),
            db.query(Event).count(),
            db.query(Outbox).count(),
        )
        monkeypatch.setattr(
            run_service,
            "_resolve_selection",
            lambda *_args: pytest.fail("selection resolution must follow capacity admission"),
        )

        with pytest.raises(MessagingBacklogError, match="pending"):
            run_service.submit_turn(
                db,
                user.id,
                str(conversation.id),
                "must not be admitted",
                str(uuid.uuid4()),
            )
        db.rollback()

        assert (
            db.query(Run).count(),
            db.query(Event).count(),
            db.query(Outbox).count(),
        ) == before


def test_oldest_pending_publication_limit_is_enforced():
    with SessionLocal() as db:
        _user, _project, conversation, run, _job, state = _setup_run(db)
        state.active = True
        old = datetime.now(UTC) - timedelta(seconds=301)
        _insert_pending_commands(db, conversation.id, run.id, state.transport_epoch, 1, old)
        db.commit()

        with pytest.raises(MessagingBacklogError, match="300 seconds"):
            check_submission_capacity(db)


def test_capacity_check_is_inactive_before_transport_activation():
    with SessionLocal() as db:
        _user, _project, conversation, run, _job, state = _setup_run(db)
        _insert_pending_commands(db, conversation.id, run.id, state.transport_epoch, 1)
        state.active = False
        db.commit()

        check_submission_capacity(db)
