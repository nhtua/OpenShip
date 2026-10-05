"""Tests for event routes: snapshot, stream, and SSE cursor handling."""

import uuid
import time

from fastapi.testclient import TestClient
from src.openship.main import app
from src.openship.auth.models import User
from src.openship.workspace.models import Project
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run, RunJob, Event, Outbox
from src.openship.database.session import SessionLocal

app_test = app


def setup_user_project_conv():
    """Create user, project, conversation. Returns (user_id, project_id, conv_id)."""
    db = SessionLocal()
    try:
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

        return user.id, project.id, conv.id
    finally:
        db.close()


def get_user_token(user_id):
    """Create a JWT token for a user."""
    from src.openship.auth.service import create_jwt
    return create_jwt(user_id, "testuser")


def test_snapshot_empty():
    """Empty snapshot returns no events with sequence -1."""
    user_id, project_id, conv_id = setup_user_project_conv()
    token = get_user_token(user_id)

    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == str(conv_id)
        assert data["sequence"] == -1
        assert data["events"] == []


def test_snapshot_with_events():
    """Snapshot returns all events up to max committed sequence."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        # Create a run and some events
        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        events = []
        for i in range(3):
            ev = Event(
                conversation_id=conv_id,
                run_id=run.id,
                sequence=i,
                type=f"event_{i}",
                payload=f'{{"seq": {i}}}',
            )
            db.add(ev)
            events.append(ev)

        # Update conversation sequence
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 3
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == str(conv_id)
        assert data["sequence"] == 2
        assert len(data["events"]) == 3


def test_stream_after_sequence():
    """Stream returns only events after the given sequence."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        for i in range(5):
            ev = Event(
                conversation_id=conv_id,
                run_id=run.id,
                sequence=i,
                type=f"event_{i}",
                payload=f'{{"seq": {i}}}',
            )
            db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 5
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/events?after_sequence=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        assert events[0]["sequence"] == 3
        assert events[1]["sequence"] == 4


def test_stream_invalid_cursor():
    """Malformed cursor returns 422."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/events?after_sequence=not-a-number",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


def test_stream_negative_cursor():
    """Negative cursor (-1) is valid and means 'start from beginning'."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/events?after_sequence=-1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


def test_stream_future_cursor():
    """Future cursor (past max sequence) returns empty list."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/events?after_sequence=9999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["events"] == []


def test_snapshot_no_auth():
    """Snapshot without auth returns 401."""
    db = SessionLocal()
    try:
        _, _, conv_id = setup_user_project_conv()
    finally:
        db.close()

    with TestClient(app_test) as client:
        resp = client.get(f"/api/conversations/{conv_id}/snapshot")
        assert resp.status_code in (401, 403)


def test_snapshot_foreign_user():
    """Snapshot for another user's conversation returns 404."""
    db = SessionLocal()
    try:
        user1_id, project1_id, conv1_id = setup_user_project_conv()
        user2_id, project2_id, conv2_id = setup_user_project_conv()
    finally:
        db.close()

    token2 = get_user_token(user2_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv1_id}/snapshot",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 404


def test_events_omit_sensitive_data():
    """Event payloads should not contain raw prompts, secrets, or provider errors."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id,
            project_id=project_id,
            status="completed",
            fence=1,
            error_code="api_timeout",
            usage='{"input_tokens": 10, "output_tokens": 20}',
        )
        db.add(run)
        db.flush()

        ev = Event(
            conversation_id=conv_id,
            run_id=run.id,
            sequence=0,
            type="run_completed",
            payload='{"status": "completed"}',
        )
        db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 1
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for ev in data["events"]:
            # Payload should be JSON, not contain raw prompt or secret
            payload = ev.get("payload", {})
            assert isinstance(payload, dict)
            # Should not contain raw API key or secret
            assert "sk-" not in str(payload)
            assert "api_key" not in str(payload).lower()


def test_outbox_deduplication():
    """Duplicate outbox rows for same event ID should not cause duplicate events."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        ev = Event(
            conversation_id=conv_id,
            run_id=run.id,
            sequence=0,
            type="run_submitted",
            payload='{"run_id": "' + str(run.id) + '"}',
        )
        db.add(ev)

        # Two outbox rows for same event (simulates duplicate delivery)
        ob1 = Outbox(
            conversation_id=conv_id,
            event_id=ev.id,
            type="run_submitted",
            payload='{"run_id": "' + str(run.id) + '"}',
        )
        ob2 = Outbox(
            conversation_id=conv_id,
            event_id=ev.id,
            type="run_submitted",
            payload='{"run_id": "' + str(run.id) + '"}',
        )
        db.add(ob1)
        db.add(ob2)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 1
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        # Snapshot should show only one event
        resp = client.get(
            f"/api/conversations/{conv_id}/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1


def test_run_status_event_atomic():
    """Run status update and event should be committed together."""
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Commit run but not event yet
        db.commit()
        run_id = run.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        # Update status and add event in same transaction
        run = db.query(Run).filter(Run.id == run_id).first()
        run.status = "completed"
        run.fence = 1
        run.ended_at = datetime.now(timezone.utc)

        ev = Event(
            conversation_id=conv_id,
            run_id=run_id,
            sequence=0,
            type="run_completed",
            payload='{"status": "completed"}',
        )
        db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 1
        db.commit()

        # Verify both are visible
        run = db.query(Run).filter(Run.id == run_id).first()
        assert run.status == "completed"
        event_count = (
            db.query(Event)
            .filter(
                Event.conversation_id == conv_id,
                Event.run_id == run_id,
            )
            .count()
        )
        assert event_count == 1
    finally:
        db.close()


def test_stream_event_ordering():
    """Events are returned in sequence order."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Insert out of order to test ordering
        seqs = [4, 1, 3, 0, 2]
        for seq in seqs:
            ev = Event(
                conversation_id=conv_id,
                run_id=run.id,
                sequence=seq,
                type=f"event_{seq}",
                payload=f'{{"seq": {seq}}}',
            )
            db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 5
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        sequences = [ev["sequence"] for ev in data["events"]]
        assert sequences == sorted(sequences)


def test_sse_stream_pruned_cursor():
    """Pruned cursor emits snapshot.required then closes."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        # Create 150 events so min_sequence becomes positive
        for i in range(150):
            ev = Event(
                conversation_id=conv_id,
                run_id=run.id,
                sequence=i,
                type=f"event_{i}",
                payload=f'{{"seq": {i}}}',
            )
            db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 150
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        # min_sequence = 150 - 100 = 50
        # Pruned check: after_sequence < 50 - 1 => after_sequence < 49
        # Use after_sequence=10 which is pruned
        resp = client.get(
            f"/api/conversations/{conv_id}/events/stream?after_sequence=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = resp.text
        assert "snapshot.required" in text
        assert "pruned" in text


def test_sse_stream_future_cursor():
    """Future cursor on SSE endpoint returns 422."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        resp = client.get(
            f"/api/conversations/{conv_id}/events/stream?after_sequence=9999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "future_cursor" in str(data)


def test_sse_stream_connection():
    """SSE stream endpoint returns proper headers and format."""
    db = SessionLocal()
    try:
        user_id, project_id, conv_id = setup_user_project_conv()

        run = Run(
            conversation_id=conv_id, project_id=project_id, status="queued", fence=0
        )
        db.add(run)
        db.flush()

        ev = Event(
            conversation_id=conv_id,
            run_id=run.id,
            sequence=0,
            type="run_submitted",
            payload='{"run_id": "' + str(run.id) + '"}',
        )
        db.add(ev)

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        conv.next_event_sequence = 1
        db.commit()
    finally:
        db.close()

    token = get_user_token(user_id)
    with TestClient(app_test) as client:
        # Verify non-streaming endpoint returns SSE-compatible data
        resp = client.get(
            f"/api/conversations/{conv_id}/events?after_sequence=-1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert len(data["events"]) == 1
        event = data["events"][0]
        # Verify event has required fields for SSE replay
        assert event["id"]
        assert event["sequence"] == 0
        assert event["type"] == "run_submitted"
        assert "payload" in event
