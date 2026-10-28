"""Durable, explicitly requested retention maintenance commands."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..runs.models import Outbox
from .models import MessageDisposition, MessageTransportState, RetentionTask

MAX_ENVELOPE_BYTES = 16 * 1024
MAX_RETENTION_DAYS = 36_500
_FIELDS = frozenset(
    {
        "schema_version",
        "message_id",
        "kind",
        "installation_id",
        "transport_epoch",
        "source_service",
        "created_at",
        "subject",
        "task_id",
        "correlation_id",
        "causation_id",
    }
)


class MaintenanceCommandError(ValueError):
    """Safe rejection of a malformed or mismatched maintenance command."""


@dataclass(frozen=True)
class RetentionCommand:
    message_id: uuid.UUID
    installation_id: uuid.UUID
    transport_epoch: uuid.UUID
    task_id: uuid.UUID
    created_at: datetime
    subject: str
    body_digest: str


@dataclass(frozen=True)
class RetentionClaim:
    action: str
    task_id: uuid.UUID
    state: str
    requested_by: str | None = None
    event_days: int | None = None
    checkpoint_days: int | None = None
    messaging_days: int | None = None
    batch_size: int | None = None
    dry_run: bool | None = None
    attempt: int | None = None
    message_id: uuid.UUID | None = None
    body_digest: str | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise MaintenanceCommandError("invalid_retention_command")
        value[key] = item
    return value


def _uuid(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise MaintenanceCommandError("invalid_retention_command")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise MaintenanceCommandError("invalid_retention_command") from error
    if str(parsed) != value:
        raise MaintenanceCommandError("invalid_retention_command")
    return parsed


def enqueue_retention_task(
    db: Session,
    *,
    requested_by: str,
    event_days: int,
    checkpoint_days: int,
    messaging_days: int | None = None,
    batch_size: int | None = None,
    dry_run: bool,
) -> RetentionTask:
    """Create the task and its stable JetStream outbox command in one transaction."""
    if not isinstance(requested_by, str) or not 1 <= len(requested_by.strip()) <= 255:
        raise ValueError("requested_by must contain between 1 and 255 characters")
    selected_messaging_days = (
        settings.messaging_retention_days if messaging_days is None else messaging_days
    )
    days = (event_days, checkpoint_days, selected_messaging_days)
    if any(
        type(value) is not int or not 1 <= value <= MAX_RETENTION_DAYS for value in days
    ):
        raise ValueError("retention days must be between 1 and 36500")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    selected_batch_size = (
        settings.messaging_retention_batch_size if batch_size is None else batch_size
    )
    if type(selected_batch_size) is not int or not 1 <= selected_batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")

    state = (
        db.query(MessageTransportState).filter_by(id=1).with_for_update().one_or_none()
    )
    if state is None:
        raise RuntimeError("messaging state is missing")
    if not state.active:
        raise RuntimeError("messaging transport is inactive")
    if not state.admission_open or not state.execution_open:
        raise RuntimeError("messaging maintenance admission is closed")

    task = RetentionTask(
        id=uuid.uuid4(),
        requested_by=requested_by.strip(),
        event_retention_days=event_days,
        checkpoint_retention_days=checkpoint_days,
        messaging_retention_days=days[2],
        batch_size=selected_batch_size,
        dry_run=dry_run,
        state="requested",
    )
    message_id = uuid.uuid5(
        state.installation_id,
        f"retention.execute:{task.id}:{state.transport_epoch}",
    )
    subject = f"openship.{state.installation_id.hex}.v1.command.maintenance.retention"
    outbox = Outbox(
        type="retention.execute",
        payload=json.dumps(
            {"task_id": str(task.id)}, sort_keys=True, separators=(",", ":")
        ),
        destination="maintenance",
        schema_version=1,
        message_id=message_id,
        subject=subject,
        transport_epoch=state.transport_epoch,
        maintenance_task_id=task.id,
        next_attempt_at=datetime.now(UTC),
    )
    db.add(task)
    db.add(outbox)
    db.flush()
    return task


def parse_retention_command(
    body: bytes,
    *,
    subject: str,
    installation_id: uuid.UUID,
) -> RetentionCommand:
    """Validate the small reference-only envelope before loading task state."""
    if not isinstance(body, bytes) or len(body) > MAX_ENVELOPE_BYTES:
        raise MaintenanceCommandError("invalid_retention_command")
    expected_subject = (
        f"openship.{installation_id.hex}.v1.command.maintenance.retention"
    )
    if subject != expected_subject:
        raise MaintenanceCommandError("invalid_retention_command")
    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or record.keys() != _FIELDS:
            raise MaintenanceCommandError("invalid_retention_command")
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise MaintenanceCommandError("invalid_retention_command")
        if (
            record["kind"] != "retention.execute"
            or record["source_service"] != "openship"
        ):
            raise MaintenanceCommandError("invalid_retention_command")
        if record["subject"] != expected_subject or not isinstance(
            record["created_at"], str
        ):
            raise MaintenanceCommandError("invalid_retention_command")
        task_id = _uuid(record["task_id"])
        if (
            _uuid(record["correlation_id"]) != task_id
            or _uuid(record["causation_id"]) != task_id
        ):
            raise MaintenanceCommandError("invalid_retention_command")
        created_at = datetime.fromisoformat(record["created_at"])
        if created_at.tzinfo is None:
            raise MaintenanceCommandError("invalid_retention_command")
        parsed_installation = _uuid(record["installation_id"])
        if parsed_installation != installation_id:
            raise MaintenanceCommandError("invalid_retention_command")
        return RetentionCommand(
            message_id=_uuid(record["message_id"]),
            installation_id=parsed_installation,
            transport_epoch=_uuid(record["transport_epoch"]),
            task_id=task_id,
            created_at=created_at.astimezone(UTC),
            subject=subject,
            body_digest=hashlib.sha256(body).hexdigest(),
        )
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        raise MaintenanceCommandError("invalid_retention_command") from None


def claim_retention_task(
    db: Session,
    body: bytes,
    *,
    subject: str,
) -> RetentionClaim:
    """Commit a task's running state before executing its idempotent pruning work."""
    state = (
        db.query(MessageTransportState).filter_by(id=1).with_for_update().one_or_none()
    )
    if state is None:
        db.rollback()
        return RetentionClaim("defer", uuid.UUID(int=0), "unavailable")
    try:
        command = parse_retention_command(
            body, subject=subject, installation_id=state.installation_id
        )
    except MaintenanceCommandError:
        db.rollback()
        raise

    task = (
        db.query(RetentionTask)
        .filter(RetentionTask.id == command.task_id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    row = (
        db.query(Outbox)
        .filter(
            Outbox.destination == "maintenance",
            Outbox.maintenance_task_id == command.task_id,
        )
        .one_or_none()
    )
    expected_message_id = uuid.uuid5(
        state.installation_id,
        f"retention.execute:{command.task_id}:{command.transport_epoch}",
    )
    if (
        task is None
        or row is None
        or not state.active
        or not state.execution_open
        or state.transport_epoch != command.transport_epoch
        or row.type != "retention.execute"
        or row.message_id != command.message_id
        or row.message_id != expected_message_id
        or row.transport_epoch != command.transport_epoch
        or row.subject != command.subject
        or row.created_at is None
        or _utc(row.created_at) != command.created_at
        or row.payload
        != json.dumps(
            {"task_id": str(command.task_id)}, sort_keys=True, separators=(",", ":")
        )
    ):
        db.rollback()
        return RetentionClaim("defer", command.task_id, "identity_or_gate_mismatch")

    disposition = (
        db.query(MessageDisposition)
        .filter_by(message_id=command.message_id)
        .with_for_update()
        .one_or_none()
    )
    if disposition is not None and disposition.payload_digest != command.body_digest:
        db.rollback()
        return RetentionClaim("defer", command.task_id, "delivery_digest_mismatch")
    if disposition is None:
        disposition = MessageDisposition(
            message_id=command.message_id,
            disposition="claimed",
            payload_digest=command.body_digest,
            handler_version=1,
        )
        db.add(disposition)
    elif disposition.disposition == "completed" and task.state not in {
        "completed",
        "failed",
    }:
        db.rollback()
        return RetentionClaim("defer", command.task_id, "terminal_disposition_mismatch")

    if task.state in {"completed", "failed"}:
        disposition.disposition = "completed"
        disposition.error_code = task.error_code
        db.commit()
        terminal = RetentionClaim("ack", task.id, task.state, attempt=task.attempts)
        return RetentionClaim(
            action=terminal.action,
            task_id=terminal.task_id,
            state=terminal.state,
            attempt=terminal.attempt,
            message_id=command.message_id,
            body_digest=command.body_digest,
        )

    task.state = "running"
    task.attempts += 1
    task.started_at = task.started_at or datetime.now(UTC)
    task.error_code = None
    task.result_json = None
    disposition.disposition = "claimed"
    disposition.error_code = None
    db.commit()
    return RetentionClaim(
        action="execute",
        task_id=task.id,
        state=task.state,
        requested_by=task.requested_by,
        event_days=task.event_retention_days,
        checkpoint_days=task.checkpoint_retention_days,
        messaging_days=task.messaging_retention_days,
        batch_size=task.batch_size,
        dry_run=task.dry_run,
        attempt=task.attempts,
        message_id=command.message_id,
        body_digest=command.body_digest,
    )


def complete_retention_task(
    db: Session,
    task_id: uuid.UUID,
    result: dict,
    *,
    message_id: uuid.UUID,
    body_digest: str,
) -> bool:
    """Persist a task result before the caller confirms the JetStream ACK."""
    task = (
        db.query(RetentionTask)
        .filter(RetentionTask.id == task_id)
        .with_for_update()
        .one_or_none()
    )
    if task is None:
        db.rollback()
        return False
    disposition = (
        db.query(MessageDisposition)
        .filter_by(message_id=message_id)
        .with_for_update()
        .one_or_none()
    )
    if disposition is None or disposition.payload_digest != body_digest:
        db.rollback()
        return False
    if task.state == "completed":
        db.rollback()
        return disposition.disposition == "completed"
    if task.state != "running":
        db.rollback()
        return False
    task.state = "completed"
    task.completed_at = datetime.now(UTC)
    task.error_code = None
    task.result_json = json.dumps(
        result, sort_keys=True, separators=(",", ":"), default=str
    )
    disposition.disposition = "completed"
    disposition.error_code = None
    db.commit()
    return True


def fail_retention_task(
    db: Session,
    task_id: uuid.UUID,
    *,
    error_code: str,
    message_id: uuid.UUID,
    body_digest: str,
) -> bool:
    """Persist a fixed safe failure code for a task that failed deterministically."""
    if not error_code or len(error_code) > 80:
        raise ValueError("retention task error code must contain 1 to 80 characters")
    task = (
        db.query(RetentionTask)
        .filter(RetentionTask.id == task_id)
        .with_for_update()
        .one_or_none()
    )
    if task is None:
        db.rollback()
        return False
    disposition = (
        db.query(MessageDisposition)
        .filter_by(message_id=message_id)
        .with_for_update()
        .one_or_none()
    )
    if disposition is None or disposition.payload_digest != body_digest:
        db.rollback()
        return False
    if task.state in {"completed", "failed"}:
        db.rollback()
        return task.state == "failed" and disposition.disposition == "completed"
    task.state = "failed"
    task.completed_at = datetime.now(UTC)
    task.error_code = error_code
    task.result_json = None
    disposition.disposition = "completed"
    disposition.error_code = error_code
    db.commit()
    return True


def record_retention_ack_intent(
    db: Session,
    task_id: uuid.UUID,
    *,
    message_id: uuid.UUID,
    body_digest: str,
    stream_sequence: int,
    consumer_sequence: int,
) -> bool:
    """Persist exact delivery sequences before asking JetStream to ACK."""
    if (
        type(stream_sequence) is not int
        or stream_sequence < 1
        or type(consumer_sequence) is not int
        or consumer_sequence < 1
    ):
        db.rollback()
        return False
    task = (
        db.query(RetentionTask)
        .filter(RetentionTask.id == task_id)
        .with_for_update()
        .one_or_none()
    )
    row = (
        db.query(Outbox)
        .filter(
            Outbox.destination == "maintenance",
            Outbox.maintenance_task_id == task_id,
            Outbox.message_id == message_id,
        )
        .with_for_update()
        .one_or_none()
    )
    disposition = (
        db.query(MessageDisposition)
        .filter_by(message_id=message_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        task is None
        or task.state not in {"completed", "failed"}
        or row is None
        or disposition is None
        or disposition.payload_digest != body_digest
        or disposition.disposition != "completed"
    ):
        db.rollback()
        return False
    if task.consumer_acked_at is None:
        task.consumer_stream_sequence = stream_sequence
        task.consumer_sequence = consumer_sequence
        task.ack_pending_at = task.ack_pending_at or datetime.now(UTC)
    db.commit()
    return True


def record_retention_ack(
    db: Session, task_id: uuid.UUID, *, stream_sequence: int
) -> bool:
    """Record a confirmed ACK for the task's exact JetStream stream sequence."""
    task = (
        db.query(RetentionTask)
        .filter(RetentionTask.id == task_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        task is None
        or task.state not in {"completed", "failed"}
        or task.consumer_stream_sequence != stream_sequence
        or task.consumer_sequence is None
        or task.ack_pending_at is None
    ):
        db.rollback()
        return False
    task.consumer_acked_at = task.consumer_acked_at or datetime.now(UTC)
    db.commit()
    return True


def reconcile_retention_ack_floor(
    db: Session,
    *,
    stream_sequence: int,
    consumer_sequence: int,
    limit: int = 100,
) -> int:
    """Mark a bounded set of ACK intents confirmed by JetStream's durable floor."""
    if (
        type(stream_sequence) is not int
        or stream_sequence < 0
        or type(consumer_sequence) is not int
        or consumer_sequence < 0
        or type(limit) is not int
        or not 1 <= limit <= 1000
    ):
        db.rollback()
        return 0
    tasks = (
        db.query(RetentionTask)
        .filter(
            RetentionTask.state.in_(("completed", "failed")),
            RetentionTask.consumer_acked_at.is_(None),
            RetentionTask.ack_pending_at.is_not(None),
            RetentionTask.consumer_sequence <= consumer_sequence,
            RetentionTask.consumer_stream_sequence <= stream_sequence,
        )
        .order_by(RetentionTask.consumer_sequence, RetentionTask.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    if not tasks:
        db.rollback()
        return 0
    now = datetime.now(UTC)
    for task in tasks:
        task.consumer_acked_at = now
    db.commit()
    return len(tasks)


def execute_retention_claim(session_factory, claim: RetentionClaim) -> bool:
    """Run one committed claim and persist a terminal result before ACK."""
    if claim.action != "execute" or None in (
        claim.event_days,
        claim.checkpoint_days,
        claim.messaging_days,
        claim.batch_size,
        claim.dry_run,
        claim.message_id,
        claim.body_digest,
    ):
        return False
    try:
        from ..events.retention import run_retention

        with session_factory() as db:
            task = (
                db.query(RetentionTask)
                .filter(RetentionTask.id == claim.task_id)
                .with_for_update()
                .one_or_none()
            )
            if task is None or task.state != "running":
                db.rollback()
                return False
            result = run_retention(
                db,
                event_retention_days=claim.event_days,
                checkpoint_retention_days=claim.checkpoint_days,
                dry_run=claim.dry_run,
                messaging_retention_days=claim.messaging_days,
                batch_size=claim.batch_size,
                commit=False,
            )
            return complete_retention_task(
                db,
                claim.task_id,
                result,
                message_id=claim.message_id,
                body_digest=claim.body_digest,
            )
    except Exception:  # noqa: BLE001 - convert operation errors to a safe task result
        try:
            with session_factory() as db:
                return fail_retention_task(
                    db,
                    claim.task_id,
                    error_code="retention_execution_failed",
                    message_id=claim.message_id,
                    body_digest=claim.body_digest,
                )
        except Exception:  # noqa: BLE001 - retain uncertain tasks for operator review
            return False


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
