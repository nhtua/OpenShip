"""Contracts for messages awaiting an ordered routing decision."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.openship.events.schemas import TASK_EVENT_SCHEMAS
from src.openship.runs.inbox.admission import submit_message
from src.openship.runs.inbox.contracts import (
    MessageAdmissionRequest,
    MessageAdmissionResponse,
)
from src.openship.runs.inbox.models import ConversationInboxEntry
from src.openship.runs.tasks.decisions import park_task_wait
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_state import _identity


def test_admission_can_report_an_unassigned_message():
    response = MessageAdmissionResponse(
        message_id=uuid4(), disposition=None, status="awaiting"
    )
    assert response.disposition is None
    assert response.status == "awaiting"


def test_assignment_event_has_an_explicit_registered_schema():
    event_class = TASK_EVENT_SCHEMAS["message.assigned"]
    message_id = uuid4()
    event = event_class(
        schema_version=1,
        message_id=message_id,
        disposition="queued",
        status="queued",
    )
    assert event.model_dump()["message_id"] == message_id


def test_inbox_schema_allows_unassigned_rows():
    table = ConversationInboxEntry.__table__
    assert table.c.disposition.nullable
    constraints = {constraint.name: str(constraint.sqltext) for constraint in table.constraints
                   if hasattr(constraint, "sqltext")}
    assert "awaiting" in constraints["ck_inbox_status"]


def test_migration_preserves_existing_rows_and_guards_downgrade():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/038_inbox_deferred_routing.py"
    spec = importlib.util.spec_from_file_location("inbox038", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE conversation_inbox (id INTEGER PRIMARY KEY, "
            "disposition VARCHAR(24) NOT NULL, status VARCHAR(24) NOT NULL, "
            "CONSTRAINT ck_inbox_disposition CHECK (disposition IN ('clarification', 'queued')), "
            "CONSTRAINT ck_inbox_status CHECK (status IN ('attached', 'queued', 'promoted', 'failed')))"
        )
        conn.exec_driver_sql("INSERT INTO conversation_inbox VALUES (1, 'clarification', 'attached')")
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.exec_driver_sql("SELECT disposition, status FROM conversation_inbox").one() == (
            "clarification", "attached"
        )
        conn.exec_driver_sql("INSERT INTO conversation_inbox VALUES (2, NULL, 'awaiting')")
        with pytest.raises(RuntimeError, match="unassigned"):
            migration.downgrade()
        conn.exec_driver_sql("DELETE FROM conversation_inbox WHERE id = 2")
        migration.downgrade()


def test_assignment_queues_oldest_unassigned_message_for_busy_run():
    from src.openship.runs.inbox.assignment import assign_next_message

    with TestSessionLocal() as db:
        user, _project, conversation, _run, _claim = _identity(db)
        db.commit()
        admitted = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(content="next", client_request_id=str(uuid4())),
        )
        entry = db.query(ConversationInboxEntry).filter_by(message_id=admitted.message_id).one()
        entry.status, entry.disposition = "awaiting", None
        db.flush()
        assigned = assign_next_message(db, conversation_id=conversation.id, user_id=user.id, now=datetime.now(UTC))
        assert assigned.message_id == admitted.message_id
        assert assigned.status == "queued"
        assert assigned.disposition == "queued"


def test_open_clarification_admission_attaches_to_open_request():
    from src.openship.runs.inbox.admission import submit_message
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest
    from src.openship.runs.tasks.decisions import park_task_wait
    from tests.conftest import TestSessionLocal
    from tests.test_task_decisions import _pinned_execution
    from tests.test_task_state import _identity

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        park_task_wait(db, claim, kind="clarification", prompt="Which?", now=datetime.now(UTC))
        result = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(content="morning", client_request_id=str(uuid4())),
        )
        assert result.disposition == "clarification"
        assert result.status == "attached"
        assert result.request_id is not None
        assert result.run_id == run.id


def test_assignment_binds_only_one_reply_before_assessment():
    from src.openship.runs.inbox.assignment import assign_next_message

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        admitted_ids = []
        for text in ("first", "second"):
            admitted = submit_message(
                db, user_id=user.id, conversation_id=conversation.id,
                request=MessageAdmissionRequest(content=text, client_request_id=str(uuid4())),
            )
            admitted_ids.append(admitted.message_id)
        # Force all admitted entries back to awaiting (simulating pre-assignment).
        db.query(ConversationInboxEntry).filter(
            ConversationInboxEntry.message_id.in_(admitted_ids)
        ).update(
            {ConversationInboxEntry.status: "awaiting", ConversationInboxEntry.disposition: None},
            synchronize_session=False,
        )
        db.flush()
        park_task_wait(db, claim, kind="clarification", prompt="Which?", now=datetime.now(UTC))
        oldest = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=conversation.id, status="awaiting")
            .order_by(ConversationInboxEntry.sequence)
            .first()
        )
        assert oldest is not None
        first = assign_next_message(db, conversation_id=conversation.id, user_id=user.id, now=datetime.now(UTC))
        assert first.message_id == oldest.message_id
        assert first.disposition == "clarification"
        assert assign_next_message(db, conversation_id=conversation.id, user_id=user.id, now=datetime.now(UTC)) is None
        remaining = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=conversation.id, status="awaiting")
            .order_by(ConversationInboxEntry.sequence)
            .all()
        )
        assert len(remaining) == 1
        assert remaining[0].message_id != oldest.message_id


def test_direct_turn_cannot_bypass_unassigned_message(client, authorized_client):
    from src.openship.database.session import SessionLocal
    from src.openship.runs.models import Run

    conversation = client.post(
        "/api/conversations", json={"title": "FIFO"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "older", "client_request_id": str(uuid4())},
        headers=authorized_client,
    )
    from uuid import UUID

    with SessionLocal() as db:
        db.get(Run, UUID(first.json()["id"])).status = "completed"
        entry = db.query(ConversationInboxEntry).filter_by(message_id=UUID(reply.json()["message_id"])).one()
        entry.status, entry.disposition = "awaiting", None
        db.commit()
    direct = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "newer", "client_request_id": str(uuid4())},
        headers=authorized_client,
    )
    assert direct.status_code == 409
