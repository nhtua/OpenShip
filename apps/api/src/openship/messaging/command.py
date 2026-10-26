"""Bounded v1 run-command validation and exact fenced acquisition."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..runs.models import Outbox, Run
from ..runs.queue import ClaimDecision, _claim_run_exact
from .models import MessageDisposition, MessageTransportState

MAX_ENVELOPE_BYTES = 16 * 1024
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
        "conversation_id",
        "project_id",
        "run_id",
        "correlation_id",
        "causation_id",
        "dispatch_generation",
    }
)


class InvalidCommandEnvelope(ValueError):
    """Safe, content-free rejection of an untrusted command envelope."""

    def __init__(self) -> None:
        super().__init__("invalid_run_command")


@dataclass(frozen=True)
class RunCommand:
    message_id: uuid.UUID
    installation_id: uuid.UUID
    transport_epoch: uuid.UUID
    conversation_id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    correlation_id: uuid.UUID
    causation_id: uuid.UUID
    dispatch_generation: int
    created_at: datetime
    subject: str
    body_digest: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidCommandEnvelope()
        value[key] = item
    return value


def _uuid(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidCommandEnvelope()
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidCommandEnvelope() from exc
    if str(parsed) != value:
        raise InvalidCommandEnvelope()
    return parsed


def parse_run_command(
    body: bytes,
    *,
    subject: str,
    installation_id: uuid.UUID,
) -> RunCommand:
    """Parse only the reference fields emitted by the run-command relay."""
    if not isinstance(body, bytes) or len(body) > MAX_ENVELOPE_BYTES:
        raise InvalidCommandEnvelope()
    expected_subject = f"openship.{installation_id.hex}.v1.command.run.execute"
    if subject != expected_subject:
        raise InvalidCommandEnvelope()
    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or record.keys() != _FIELDS:
            raise InvalidCommandEnvelope()
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise InvalidCommandEnvelope()
        if record["kind"] != "run.execute" or record["source_service"] != "openship":
            raise InvalidCommandEnvelope()
        if record["subject"] != expected_subject:
            raise InvalidCommandEnvelope()
        if (
            type(record["dispatch_generation"]) is not int
            or record["dispatch_generation"] < 1
        ):
            raise InvalidCommandEnvelope()
        if not isinstance(record["created_at"], str):
            raise InvalidCommandEnvelope()
        created_at = datetime.fromisoformat(record["created_at"])
        if created_at.tzinfo is None:
            raise InvalidCommandEnvelope()
        result = RunCommand(
            message_id=_uuid(record["message_id"]),
            installation_id=_uuid(record["installation_id"]),
            transport_epoch=_uuid(record["transport_epoch"]),
            conversation_id=_uuid(record["conversation_id"]),
            project_id=_uuid(record["project_id"]),
            run_id=_uuid(record["run_id"]),
            correlation_id=_uuid(record["correlation_id"]),
            causation_id=_uuid(record["causation_id"]),
            dispatch_generation=record["dispatch_generation"],
            created_at=created_at.astimezone(UTC),
            subject=subject,
            body_digest=hashlib.sha256(body).hexdigest(),
        )
        if result.installation_id != installation_id:
            raise InvalidCommandEnvelope()
        return result
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        raise InvalidCommandEnvelope() from None


def claim_command(
    db: Session,
    body: bytes,
    *,
    subject: str,
    worker_id: str,
    lease_seconds: int,
) -> ClaimDecision:
    """Bind a received identity to its outbox row before one exact claim."""
    state = db.get(MessageTransportState, 1)
    if state is None:
        return ClaimDecision("disposed", code="transport_missing")
    command = parse_run_command(
        body, subject=subject, installation_id=state.installation_id
    )
    row = (
        db.query(Outbox)
        .filter(
            Outbox.run_id == command.run_id,
            Outbox.dispatch_generation == command.dispatch_generation,
        )
        .one_or_none()
    )
    run = db.get(Run, command.run_id)
    expected_message_id = uuid.uuid5(
        state.installation_id,
        f"run.execute:{command.run_id}:{command.dispatch_generation}:{command.transport_epoch}",
    )
    if (
        row is None
        or run is None
        or state.transport_epoch != command.transport_epoch
        or row.destination != "command"
        or row.type != "run.execute"
        or row.schema_version != 1
        or row.message_id != command.message_id
        or row.message_id != expected_message_id
        or row.transport_epoch != command.transport_epoch
        or row.subject != command.subject
        or row.conversation_id != command.conversation_id
        or run.conversation_id != command.conversation_id
        or run.project_id != command.project_id
        or command.correlation_id != command.run_id
        or command.causation_id != command.run_id
        or row.created_at is None
        or _utc(row.created_at) != command.created_at
    ):
        return ClaimDecision("disposed", code="identity_mismatch")

    decision = _claim_run_exact(
        db,
        run_id=command.run_id,
        generation=command.dispatch_generation,
        epoch=command.transport_epoch,
        message_id=command.message_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        conversation_id=command.conversation_id,
        project_id=command.project_id,
        payload_digest=command.body_digest,
    )
    if decision.code != "digest_mismatch":
        disposition = (
            "deferred"
            if decision.kind in {"defer", "reconcile_required"}
            else decision.kind
        )
        previous = db.get(MessageDisposition, command.message_id)
        if previous is None:
            db.add(
                MessageDisposition(
                    message_id=command.message_id,
                    disposition=disposition,
                    payload_digest=command.body_digest,
                    handler_version=1,
                    error_code=decision.code,
                )
            )
        else:
            previous.disposition = disposition
            previous.error_code = decision.code
    db.commit()
    return decision


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
