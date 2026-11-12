"""Contracts for messages awaiting an ordered routing decision."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.openship.events.schemas import TASK_EVENT_SCHEMAS
from src.openship.runs.inbox.contracts import MessageAdmissionResponse
from src.openship.runs.inbox.models import ConversationInboxEntry


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
