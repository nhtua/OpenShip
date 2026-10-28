"""Explicit operator-enqueued maintenance task contracts."""

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.openship.auth.models import User as _User
from src.openship.chat.models import Conversation as _Conversation
from src.openship.database.session import SessionLocal
from src.openship.events.retention import dry_run_retention, run_retention
from src.openship.messaging import maintenance_consumer
from src.openship.messaging.maintenance import (
    MaintenanceCommandError,
    claim_retention_task,
    complete_retention_task,
    enqueue_retention_task,
    execute_retention_claim,
    fail_retention_task,
    parse_retention_command,
    record_retention_ack,
    record_retention_ack_intent,
)
from src.openship.messaging.models import (
    MessageDisposition,
    MessageExhaustion,
    MessageFailure,
    MessageFailureConflict,
    MessageTransportState,
    RetentionTask,
)
from src.openship.messaging.outbox import enqueue_run
from src.openship.messaging.relay import claim_outbox_batch
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.workspace.models import Project as _Project


def test_retention_request_and_command_outbox_commit_together():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="ops@example.test",
            event_days=21,
            checkpoint_days=45,
            dry_run=True,
        )

        row = db.query(Outbox).filter_by(maintenance_task_id=task.id).one()
        assert task.state == "requested"
        assert task.requested_by == "ops@example.test"
        assert task.event_retention_days == 21
        assert task.checkpoint_retention_days == 45
        assert task.dry_run is True
        assert task.batch_size == 1000
        assert row.destination == "maintenance"
        assert row.type == "retention.execute"
        assert row.transport_epoch == state.transport_epoch
        assert row.subject == (
            f"openship.{state.installation_id.hex}.v1.command.maintenance.retention"
        )
        assert "ops@example.test" not in row.payload
        assert json.loads(row.payload) == {"task_id": str(task.id)}

        task_id, outbox_id = task.id, row.id
        db.rollback()

    with SessionLocal() as db:
        assert db.get(type(task), task_id) is None
        assert db.get(Outbox, outbox_id) is None


@pytest.mark.parametrize(
    ("requested_by", "event_days", "checkpoint_days"),
    [
        ("  ", 30, 30),
        ("operator", 0, 30),
        ("operator", 30, -1),
        ("x" * 256, 30, 30),
    ],
)
def test_retention_request_rejects_unbounded_or_missing_arguments(
    requested_by, event_days, checkpoint_days
):
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        with pytest.raises(ValueError):
            enqueue_retention_task(
                db,
                requested_by=requested_by,
                event_days=event_days,
                checkpoint_days=checkpoint_days,
                dry_run=False,
            )


def test_retention_request_requires_active_messaging_transport():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = False
        with pytest.raises(RuntimeError, match="messaging transport is inactive"):
            enqueue_retention_task(
                db,
                requested_by="operator",
                event_days=30,
                checkpoint_days=30,
                dry_run=False,
            )


@pytest.mark.parametrize("closed_gate", ["admission_open", "execution_open"])
def test_retention_request_requires_open_messaging_gates(closed_gate):
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        setattr(state, closed_gate, False)
        with pytest.raises(RuntimeError, match="admission is closed"):
            enqueue_retention_task(
                db,
                requested_by="operator",
                event_days=30,
                checkpoint_days=30,
                dry_run=False,
            )


def test_retention_command_parser_checks_exact_durable_identity():
    installation_id = uuid.uuid4()
    task_id = uuid.uuid4()
    epoch = uuid.uuid4()
    subject = f"openship.{installation_id.hex}.v1.command.maintenance.retention"
    envelope = {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "kind": "retention.execute",
        "installation_id": str(installation_id),
        "transport_epoch": str(epoch),
        "source_service": "openship",
        "created_at": "2026-10-27T00:00:00+00:00",
        "subject": subject,
        "task_id": str(task_id),
        "correlation_id": str(task_id),
        "causation_id": str(task_id),
    }
    command = parse_retention_command(
        json.dumps(envelope, separators=(",", ":")).encode(),
        subject=subject,
        installation_id=installation_id,
    )
    assert command.task_id == task_id
    assert command.transport_epoch == epoch

    envelope["requested_by"] = "forged@example.test"
    with pytest.raises(MaintenanceCommandError):
        parse_retention_command(
            json.dumps(envelope).encode(),
            subject=subject,
            installation_id=installation_id,
        )


def test_relay_publishes_only_the_retention_task_reference():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="operator",
            event_days=30,
            checkpoint_days=30,
            messaging_days=14,
            dry_run=False,
        )
        task_id = task.id
        db.commit()

    records = claim_outbox_batch(maintenance_task_id=task_id)
    assert len(records) == 1
    envelope = json.loads(records[0].body)
    assert envelope["kind"] == "retention.execute"
    assert envelope["task_id"] == str(task_id)
    assert envelope["correlation_id"] == str(task_id)
    assert envelope["causation_id"] == str(task_id)
    assert "operator" not in records[0].body.decode()
    assert "event_retention_days" not in envelope


def test_task_state_is_committed_before_execution_and_terminal_redelivery_acks():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="operator",
            event_days=30,
            checkpoint_days=30,
            dry_run=True,
        )
        task_id = task.id
        db.commit()

    record = claim_outbox_batch(maintenance_task_id=task_id)[0]
    claim = claim_retention_task(SessionLocal(), record.body, subject=record.subject)
    assert claim.action == "execute"
    assert claim.task_id == task_id
    assert claim.state == "running"
    assert claim.attempt == 1
    assert claim.requested_by == "operator"
    assert claim.event_days == 30
    assert claim.batch_size == 1000
    assert claim.dry_run is True
    assert claim.message_id == record.message_id
    assert claim.body_digest

    altered_encoding = json.dumps(json.loads(record.body), indent=1).encode()
    mismatched = claim_retention_task(
        SessionLocal(), altered_encoding, subject=record.subject
    )
    assert mismatched.action == "defer"
    assert mismatched.state == "delivery_digest_mismatch"

    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None
        assert task.state == "running"
        assert task.attempts == 1
        disposition = db.get(MessageDisposition, record.message_id)
        assert disposition is not None
        assert disposition.payload_digest == claim.body_digest
        assert disposition.disposition == "claimed"

    with SessionLocal() as db:
        assert complete_retention_task(
            db,
            task_id,
            {"pruned_events": 0},
            message_id=claim.message_id,
            body_digest=claim.body_digest,
        )
    terminal = claim_retention_task(SessionLocal(), record.body, subject=record.subject)
    assert terminal.action == "ack"
    assert terminal.state == "completed"
    assert terminal.message_id == record.message_id
    with SessionLocal() as db:
        assert record_retention_ack_intent(
            db,
            task_id,
            message_id=terminal.message_id,
            body_digest=terminal.body_digest,
            stream_sequence=101,
            consumer_sequence=5,
        )
    with SessionLocal() as db:
        assert record_retention_ack(db, task_id, stream_sequence=101)
    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.consumer_acked_at is not None


def test_task_pruning_and_terminal_result_share_one_transaction(monkeypatch):
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="operator",
            event_days=30,
            checkpoint_days=30,
            dry_run=True,
        )
        task_id = task.id
        db.commit()

    record = claim_outbox_batch(maintenance_task_id=task_id)[0]
    claim = claim_retention_task(SessionLocal(), record.body, subject=record.subject)

    def bounded_retention(db, **kwargs):
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.state == "running"
        assert db.in_transaction()
        assert kwargs["commit"] is False
        return {"pruned_events": 0, "limit_reached": False}

    monkeypatch.setattr(
        "src.openship.events.retention.run_retention", bounded_retention
    )
    assert execute_retention_claim(SessionLocal, claim)

    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.state == "completed"
        assert json.loads(task.result_json) == {
            "limit_reached": False,
            "pruned_events": 0,
        }
        disposition = db.get(MessageDisposition, record.message_id)
        assert disposition is not None and disposition.disposition == "completed"


def test_failed_task_state_is_durable_and_replay_is_terminal():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="operator",
            event_days=30,
            checkpoint_days=30,
            dry_run=False,
        )
        task_id = task.id
        db.commit()

    record = claim_outbox_batch(maintenance_task_id=task_id)[0]
    claim = claim_retention_task(SessionLocal(), record.body, subject=record.subject)
    with SessionLocal() as db:
        assert fail_retention_task(
            db,
            task_id,
            error_code="retention_execution_failed",
            message_id=claim.message_id,
            body_digest=claim.body_digest,
        )
    terminal = claim_retention_task(SessionLocal(), record.body, subject=record.subject)
    assert terminal.action == "ack"
    assert terminal.state == "failed"


def test_messaging_retention_prunes_old_settled_ledgers_but_keeps_open_work():
    old = datetime.now(UTC) - timedelta(days=60)
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        user = _User(
            username=f"retention_{uuid.uuid4().hex[:12]}",
            email=f"retention_{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = _Project(owner_user_id=user.id, name="Retention task test")
        db.add(project)
        db.flush()
        conversation = _Conversation(
            user_id=user.id,
            project_id=project.id,
            title="Retention task test",
            next_event_sequence=0,
        )
        db.add(conversation)
        db.flush()
        terminal_run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="completed",
            fence=0,
            ended_at=old,
        )
        db.add(terminal_run)
        db.flush()
        terminal_event = Event(
            project_id=project.id,
            conversation_id=conversation.id,
            run_id=terminal_run.id,
            sequence=1,
            type="run.completed",
            payload="{}",
            created_at=old,
        )
        db.add(terminal_event)
        db.flush()
        pending_event_outbox = Outbox(
            type="run.completed",
            payload=json.dumps({"event_id": str(terminal_event.id)}),
            destination="event",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.event.{conversation.id.hex}",
            conversation_id=conversation.id,
            event_id=terminal_event.id,
            run_id=terminal_run.id,
            transport_epoch=state.transport_epoch,
            created_at=old,
        )
        settled_control_outbox = Outbox(
            type="run.cancel",
            payload="{}",
            destination="control",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.control.{terminal_run.id.hex}",
            conversation_id=conversation.id,
            event_id=terminal_event.id,
            run_id=terminal_run.id,
            transport_epoch=state.transport_epoch,
            processed_at=old,
            created_at=old,
        )
        published_event = Event(
            project_id=project.id,
            conversation_id=conversation.id,
            sequence=2,
            type="workspace.ready",
            payload="{}",
            created_at=old,
        )
        db.add(published_event)
        db.flush()
        published_event_outbox = Outbox(
            type="workspace.ready",
            payload=json.dumps({"event_id": str(published_event.id)}),
            destination="event",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.event.{conversation.id.hex}",
            conversation_id=conversation.id,
            event_id=published_event.id,
            transport_epoch=state.transport_epoch,
            processed_at=old,
            created_at=old,
        )
        published_event_disposition = MessageDisposition(
            message_id=published_event_outbox.message_id,
            disposition="completed",
            payload_digest="f" * 64,
            handler_version=1,
            observed_at=old,
        )
        db.add_all(
            [
                pending_event_outbox,
                settled_control_outbox,
                published_event_outbox,
                published_event_disposition,
            ]
        )
        queued_run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="queued",
            fence=0,
        )
        db.add(queued_run)
        db.flush()
        job = RunJob(run_id=queued_run.id, fence=0, dispatch_generation=1)
        db.add(job)
        db.flush()
        queued_outbox_id = enqueue_run(
            db, queued_run.id, 1, epoch=state.transport_epoch
        )
        queued_outbox = db.get(Outbox, queued_outbox_id)
        assert queued_outbox is not None
        queued_outbox.processed_at = old
        queued_disposition = MessageDisposition(
            message_id=queued_outbox.message_id,
            disposition="deferred",
            payload_digest="e" * 64,
            handler_version=1,
            observed_at=old,
        )
        db.add(queued_disposition)
        resolved = MessageExhaustion(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_001,
            source_timestamp=old,
            consumer_name="run-v1",
            delivery_count=10,
            state="resolved",
            created_at=old,
            updated_at=old,
            resolved_at=old,
        )
        watching = MessageExhaustion(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_002,
            source_timestamp=old,
            consumer_name="run-v1",
            delivery_count=10,
            state="watching_live_owner",
            run_id=uuid.uuid4(),
            dispatch_generation=1,
            owner_id="worker-old",
            owner_fence=1,
            lease_until=old + timedelta(days=100),
            next_check_at=old,
            created_at=old,
            updated_at=old,
        )
        blocked = MessageExhaustion(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_003,
            source_timestamp=old,
            consumer_name="run-v1",
            delivery_count=10,
            state="blocked",
            error_code="source_digest_mismatch",
            created_at=old,
            updated_at=old,
        )
        disposition = MessageDisposition(
            message_id=uuid.uuid4(),
            disposition="completed",
            payload_digest="a" * 64,
            handler_version=1,
            observed_at=old,
        )
        conflict = MessageFailureConflict(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_004,
            source_timestamp=old,
            observed_digest="b" * 64,
            observed_classification="quarantine",
            observed_error_code="invalid_run_command",
            conflict_code="source_digest_conflict",
            created_at=old,
        )
        settled_failure = MessageFailure(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_005,
            source_timestamp=old,
            body_digest="c" * 64,
            classification="quarantine",
            error_code="invalid_run_command",
            created_at=old,
        )
        pending_failure = MessageFailure(
            id=uuid.uuid4(),
            installation_id=state.installation_id,
            source_transport_epoch=state.transport_epoch,
            source_stream="OS_RUNS",
            source_sequence=10_000_006,
            source_timestamp=old,
            body_digest="d" * 64,
            classification="quarantine",
            error_code="invalid_run_command",
            created_at=old,
        )
        completed_task = RetentionTask(
            id=uuid.uuid4(),
            requested_by="operator",
            event_retention_days=30,
            checkpoint_retention_days=30,
            messaging_retention_days=30,
            batch_size=1000,
            dry_run=True,
            state="completed",
            attempts=1,
            requested_at=old,
            started_at=old,
            completed_at=old,
            consumer_acked_at=old,
            consumer_stream_sequence=100,
            consumer_sequence=100,
            ack_pending_at=old,
            result_json="{}",
        )
        completed_unacked_task = RetentionTask(
            id=uuid.uuid4(),
            requested_by="operator",
            event_retention_days=30,
            checkpoint_retention_days=30,
            messaging_retention_days=30,
            batch_size=1000,
            dry_run=True,
            state="completed",
            attempts=1,
            requested_at=old,
            started_at=old,
            completed_at=old,
            result_json="{}",
        )
        requested_task = RetentionTask(
            id=uuid.uuid4(),
            requested_by="operator",
            event_retention_days=30,
            checkpoint_retention_days=30,
            messaging_retention_days=30,
            batch_size=1000,
            dry_run=False,
            state="requested",
            attempts=0,
            requested_at=old,
        )
        db.add_all(
            [
                resolved,
                watching,
                blocked,
                disposition,
                conflict,
                settled_failure,
                pending_failure,
                completed_task,
                completed_unacked_task,
                requested_task,
            ]
        )
        db.flush()
        settled_notice = Outbox(
            type="quarantine",
            payload=json.dumps({"failure_id": str(settled_failure.id)}),
            destination="failure",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.failure.quarantine",
            transport_epoch=state.transport_epoch,
            failure_id=settled_failure.id,
            processed_at=old,
            created_at=old,
        )
        pending_notice = Outbox(
            type="quarantine",
            payload=json.dumps({"failure_id": str(pending_failure.id)}),
            destination="failure",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.failure.quarantine",
            transport_epoch=state.transport_epoch,
            failure_id=pending_failure.id,
            created_at=old,
        )
        settled_task_outbox = Outbox(
            type="retention.execute",
            payload=json.dumps({"task_id": str(completed_task.id)}),
            destination="maintenance",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.command.maintenance.retention",
            transport_epoch=state.transport_epoch,
            maintenance_task_id=completed_task.id,
            processed_at=old,
            created_at=old,
        )
        unacked_task_outbox = Outbox(
            type="retention.execute",
            payload=json.dumps({"task_id": str(completed_unacked_task.id)}),
            destination="maintenance",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.command.maintenance.retention",
            transport_epoch=state.transport_epoch,
            maintenance_task_id=completed_unacked_task.id,
            processed_at=old,
            created_at=old,
        )
        pending_task_outbox = Outbox(
            type="retention.execute",
            payload=json.dumps({"task_id": str(requested_task.id)}),
            destination="maintenance",
            schema_version=1,
            message_id=uuid.uuid4(),
            subject=f"openship.{state.installation_id.hex}.v1.command.maintenance.retention",
            transport_epoch=state.transport_epoch,
            maintenance_task_id=requested_task.id,
            created_at=old,
        )
        db.add_all(
            [
                settled_notice,
                pending_notice,
                settled_task_outbox,
                unacked_task_outbox,
                pending_task_outbox,
            ]
        )
        db.commit()

        ids = {
            "resolved": resolved.id,
            "watching": watching.id,
            "blocked": blocked.id,
            "disposition": disposition.message_id,
            "queued_disposition": queued_disposition.message_id,
            "queued_outbox": queued_outbox_id,
            "pending_event": terminal_event.id,
            "pending_event_outbox": pending_event_outbox.id,
            "settled_control_outbox": settled_control_outbox.id,
            "published_event": published_event.id,
            "published_event_outbox": published_event_outbox.id,
            "published_event_disposition": published_event_disposition.message_id,
            "conflict": conflict.id,
            "settled_failure": settled_failure.id,
            "pending_failure": pending_failure.id,
            "settled_task": completed_task.id,
            "unacked_task": completed_unacked_task.id,
            "pending_task": requested_task.id,
            "settled_notice": settled_notice.id,
            "pending_notice": pending_notice.id,
            "settled_task_outbox": settled_task_outbox.id,
            "unacked_task_outbox": unacked_task_outbox.id,
            "pending_task_outbox": pending_task_outbox.id,
        }
        dry_run = dry_run_retention(
            db,
            event_retention_days=30,
            checkpoint_retention_days=30,
            messaging_retention_days=30,
            dry_run=True,
        )
        assert dry_run["eligible_message_dispositions"] == 2
        assert dry_run["eligible_message_exhaustions"] == 1
        assert dry_run["eligible_message_failures"] == 1
        assert dry_run["eligible_message_failure_conflicts"] == 1
        assert dry_run["eligible_retention_tasks"] == 1
        assert dry_run["eligible_events"] == 0
        assert dry_run["eligible_run_dispatch_outbox"] == 1
        assert dry_run["eligible_event_outbox"] == 1

        result = run_retention(
            db,
            event_retention_days=30,
            checkpoint_retention_days=30,
            messaging_retention_days=30,
        )
        assert result["pruned_message_dispositions"] == 2
        assert result["pruned_message_exhaustions"] == 1
        assert result["pruned_message_failures"] == 1
        assert result["pruned_message_failure_conflicts"] == 1
        assert result["pruned_retention_tasks"] == 1
        assert result["pruned_events"] == 0
        assert result["pruned_run_dispatch_outbox"] == 1
        assert result["pruned_event_outbox"] == 1
        assert db.get(MessageExhaustion, ids["resolved"]) is None
        assert db.get(MessageExhaustion, ids["watching"]) is not None
        assert db.get(MessageExhaustion, ids["blocked"]) is not None
        assert db.get(MessageDisposition, ids["disposition"]) is None
        assert db.get(MessageDisposition, ids["queued_disposition"]) is not None
        assert db.get(Outbox, ids["queued_outbox"]) is not None
        assert db.get(Event, ids["pending_event"]) is not None
        assert db.get(Outbox, ids["pending_event_outbox"]) is not None
        assert db.get(Outbox, ids["settled_control_outbox"]) is None
        assert db.get(Event, ids["published_event"]) is not None
        assert db.get(Outbox, ids["published_event_outbox"]) is None
        assert db.get(MessageDisposition, ids["published_event_disposition"]) is None
        assert db.get(MessageFailure, ids["settled_failure"]) is None
        assert db.get(MessageFailure, ids["pending_failure"]) is not None
        assert db.get(MessageFailureConflict, ids["conflict"]) is None
        assert db.get(RetentionTask, ids["settled_task"]) is None
        assert db.get(RetentionTask, ids["unacked_task"]) is not None
        assert db.get(RetentionTask, ids["pending_task"]) is not None
        assert db.get(Outbox, ids["settled_notice"]) is None
        assert db.get(Outbox, ids["pending_notice"]) is not None
        assert db.get(Outbox, ids["settled_task_outbox"]) is None
        assert db.get(Outbox, ids["unacked_task_outbox"]) is not None
        assert db.get(Outbox, ids["pending_task_outbox"]) is not None


@pytest.mark.asyncio
async def test_maintenance_consumer_persists_terminal_state_before_confirmed_ack(
    monkeypatch,
):
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        state.active = True
        task = enqueue_retention_task(
            db,
            requested_by="operator",
            event_days=30,
            checkpoint_days=30,
            dry_run=True,
        )
        task_id = task.id
        db.commit()
    record = claim_outbox_batch(maintenance_task_id=task_id)[0]
    order = []

    def execute(_session_factory, claim):
        with SessionLocal() as db:
            assert complete_retention_task(
                db,
                claim.task_id,
                {},
                message_id=claim.message_id,
                body_digest=claim.body_digest,
            )
        order.append("result_committed")
        return True

    monkeypatch.setattr(maintenance_consumer, "execute_retention_claim", execute)

    async def consumer_info(_stream, _consumer):
        return SimpleNamespace(ack_floor=SimpleNamespace(stream_seq=23, consumer_seq=6))

    js = SimpleNamespace(consumer_info=consumer_info)
    client = SimpleNamespace(
        is_connected=True,
        stats={"reconnects": 0},
        jetstream=lambda **_kwargs: js,
    )
    consumer = maintenance_consumer.RetentionConsumer(
        client,
        session_factory=SessionLocal,
    )
    consumer._checked_reconnect = 0

    class InlineSlot:
        async def run(self, function, *args, **kwargs):
            return function(object(), *args, **kwargs)

    class InlinePool:
        @asynccontextmanager
        async def reserve(self):
            yield InlineSlot()

        async def close(self):
            return None

    consumer._metadata = InlinePool()

    class Message:
        data = record.body
        subject = record.subject
        metadata = SimpleNamespace(
            stream="OS_MAINTENANCE",
            consumer="maintenance-v1",
            sequence=SimpleNamespace(stream=23, consumer=7),
        )

        async def in_progress(self):
            return None

        async def ack_sync(self, *, timeout):
            assert timeout == 2.0
            with SessionLocal() as db:
                task = db.get(RetentionTask, task_id)
                assert task is not None and task.state == "completed"
                assert task.consumer_acked_at is None
                assert task.consumer_stream_sequence == 23
                assert task.consumer_sequence == 7
                assert task.ack_pending_at is not None
            order.append("ack")

        async def nak(self, *, delay):
            raise AssertionError(f"unexpected NAK delay {delay}")

    def record_ack_unavailable(*_args, **_kwargs):
        raise RuntimeError("simulated post-ACK database failure")

    monkeypatch.setattr(maintenance_consumer, "_record_ack", record_ack_unavailable)
    await asyncio.wait_for(consumer._handle(InlineSlot(), Message()), timeout=3)

    assert order == ["result_committed", "ack"]
    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.consumer_acked_at is None

    await consumer._reconcile_acknowledgements()
    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.consumer_acked_at is None

    async def consumer_info_after_ack(_stream, _consumer):
        return SimpleNamespace(ack_floor=SimpleNamespace(stream_seq=23, consumer_seq=7))

    js.consumer_info = consumer_info_after_ack
    consumer._next_ack_reconcile = 0.0
    await consumer._reconcile_acknowledgements()
    with SessionLocal() as db:
        task = db.get(RetentionTask, task_id)
        assert task is not None and task.consumer_acked_at is not None
    await consumer._metadata.close()


def test_worker_maintenance_cli_keeps_direct_retention_and_adds_explicit_enqueue(
    monkeypatch, capsys
):
    from src.openship.runs import worker_commands

    calls = []
    monkeypatch.setattr(
        worker_commands,
        "run_retention_command",
        lambda **kwargs: calls.append(("direct", kwargs)) or {"dry_run": True},
    )
    monkeypatch.setattr(
        worker_commands,
        "enqueue_retention_command",
        lambda requested_by, **kwargs: (
            calls.append(("enqueue", requested_by, kwargs))
            or {"task_id": str(uuid.UUID(int=5)), "state": "requested"}
        ),
    )
    monkeypatch.setattr(
        worker_commands,
        "retention_status_command",
        lambda task_id: {"task_id": task_id, "state": "running"},
    )

    monkeypatch.setattr(sys, "argv", ["worker_commands", "retention", "--dry-run"])
    worker_commands.main()
    assert calls[-1][0] == "direct"
    assert calls[-1][1]["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["dry_run"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker_commands",
            "enqueue-retention",
            "--requested-by",
            "operator",
            "--dry-run",
        ],
    )
    worker_commands.main()
    assert calls[-1][0:2] == ("enqueue", "operator")
    assert calls[-1][2]["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["state"] == "requested"

    monkeypatch.setattr(
        sys,
        "argv",
        ["worker_commands", "retention-status", str(uuid.UUID(int=5))],
    )
    worker_commands.main()
    assert json.loads(capsys.readouterr().out)["state"] == "running"
