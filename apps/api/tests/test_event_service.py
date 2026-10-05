"""Tests for the events service layer, specifically append_event."""

import uuid

from src.openship.auth.models import User
from src.openship.workspace.models import Project
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run, Event
from src.openship.database.session import SessionLocal
from src.openship.events.service import append_event, read_snapshot, stream_events


def setup_user_project_conv(db):
    """Create user, project, conversation in the given session.
    Returns (user, project, conv)."""
    user = User(
        username=f"evtest_{uuid.uuid4().hex[:8]}",
        email=f"evtest_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    db.add(user)
    db.flush()

    project = Project(owner_user_id=user.id, name="Test Project")
    db.add(project)
    db.flush()

    conv = Conversation(
        user_id=user.id, project_id=project.id, title="Test", next_event_sequence=0
    )
    db.add(conv)
    db.commit()

    return user, project, conv


def test_append_event_increments_sequence():
    """append_event atomically increments conversation sequence."""
    db = SessionLocal()
    try:
        user, project, conv = setup_user_project_conv(db)

        run = Run(
            conversation_id=conv.id, project_id=project.id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Append first event
        ev1 = append_event(
            db, conv.id, run.id, "run_submitted", {"run_id": str(run.id)}, actor_id=user.id
        )
        db.flush()
        assert ev1.sequence == 0

        # Append second event
        ev2 = append_event(
            db, conv.id, run.id, "run_started", {}, actor_id=user.id
        )
        db.flush()
        assert ev2.sequence == 1

        # Append third event
        ev3 = append_event(
            db, conv.id, run.id, "run_completed", {"status": "completed"}, actor_id=user.id
        )
        db.flush()
        assert ev3.sequence == 2

        # Conversation sequence should be 3 now
        db.refresh(conv)
        assert conv.next_event_sequence == 3

        db.commit()
    finally:
        db.close()


def test_append_event_stores_actor_id():
    """append_event stores the actor_id on the event."""
    db = SessionLocal()
    try:
        user, project, conv = setup_user_project_conv(db)

        run = Run(
            conversation_id=conv.id, project_id=project.id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        ev = append_event(
            db, conv.id, run.id, "test_event", {}, actor_id=user.id
        )
        db.flush()

        assert ev.actor_id == user.id
        db.commit()
    finally:
        db.close()


def test_append_event_persists_with_caller_transaction():
    """append_event is committed with the caller's transaction."""
    db = SessionLocal()
    try:
        user, project, conv = setup_user_project_conv(db)

        run = Run(
            conversation_id=conv.id, project_id=project.id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        ev = append_event(
            db, conv.id, run.id, "test_event", {"data": "test"}, actor_id=user.id
        )
        db.commit()  # Caller commits

        # Verify event is visible in the same session after commit
        event_count = (
            db.query(Event)
            .filter(Event.conversation_id == conv.id)
            .count()
        )
        assert event_count == 1
    finally:
        db.close()


def test_read_snapshot_after_append_event():
    """read_snapshot returns events created via append_event."""
    db = SessionLocal()
    try:
        user, project, conv = setup_user_project_conv(db)

        run = Run(
            conversation_id=conv.id, project_id=project.id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Append events using the service function
        append_event(db, conv.id, run.id, "event_0", {"i": 0}, actor_id=user.id)
        append_event(db, conv.id, run.id, "event_1", {"i": 1}, actor_id=user.id)
        append_event(db, conv.id, run.id, "event_2", {"i": 2}, actor_id=user.id)
        db.commit()

        # Read snapshot
        snapshot = read_snapshot(db, user.id, str(conv.id))
        assert snapshot.sequence == 2
        assert len(snapshot.events) == 3
        assert snapshot.events[0].type == "event_0"
        assert snapshot.events[1].type == "event_1"
        assert snapshot.events[2].type == "event_2"
    finally:
        db.close()


def test_stream_events_after_append_event():
    """stream_events returns events after cursor created via append_event."""
    db = SessionLocal()
    try:
        user, project, conv = setup_user_project_conv(db)

        run = Run(
            conversation_id=conv.id, project_id=project.id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Append 4 events
        for i in range(4):
            append_event(db, conv.id, run.id, f"event_{i}", {"i": i}, actor_id=user.id)
        db.commit()

        # Stream after sequence 1
        stream = stream_events(db, user.id, str(conv.id), after_sequence=1)
        assert len(stream.events) == 2
        assert stream.events[0].type == "event_2"
        assert stream.events[1].type == "event_3"
    finally:
        db.close()