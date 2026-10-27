"""Synchronous delivery decisions grounded in exact acquisition and durable state.

Call these functions with a slot-owned, dedicated Session. They commit or roll
back their transactions; no broker ACK or quarantine publication happens here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Outbox, Run, RunJob
from ..runs.queue import Claim, ClaimDecision
from .command import (
    InvalidCommandEnvelope,
    RunCommand,
    claim_command,
    parse_run_command,
)
from .models import MessageDisposition, MessageTransportState


@dataclass(frozen=True)
class DeliveryDisposition:
    action: Literal["ack", "hold", "defer", "reconcile", "quarantine"]
    code: str
    delay: float | None = None


@dataclass(frozen=True)
class PreparedDelivery:
    command: RunCommand | None
    decision: ClaimDecision | None
    disposition: DeliveryDisposition | None


def _unclaimed(decision: ClaimDecision) -> DeliveryDisposition | None:
    if decision.kind == "claimed":
        return None
    if decision.kind == "defer":
        if decision.code in {"transport_inactive", "execution_closed"}:
            return DeliveryDisposition("hold", decision.code)
        return DeliveryDisposition("defer", "claim_deferred", decision.delay or 1.0)
    if decision.kind == "reconcile_required":
        return DeliveryDisposition("reconcile", "ownership_expired")
    if decision.code in {"run_not_executable", "obsolete_generation"}:
        return DeliveryDisposition("ack", decision.code)
    if decision.code == "transport_missing":
        return DeliveryDisposition("defer", "transport_missing", 30.0)
    if decision.code in {"run_missing", "reference_mismatch", "obsolete_epoch"}:
        return DeliveryDisposition("reconcile", "durable_state_incomplete")
    return DeliveryDisposition("quarantine", decision.code or "invalid_run_command")


def prepare_run_delivery(
    db: Session,
    body: bytes,
    *,
    subject: str,
    worker_id: str,
    lease_seconds: int,
) -> PreparedDelivery:
    """Validate, acquire exactly once, and expose each subsequent step explicitly."""
    command = None
    try:
        state = db.get(MessageTransportState, 1)
        if state is None:
            db.rollback()
            return PreparedDelivery(
                None, None, DeliveryDisposition("defer", "transport_missing", 30.0)
            )
        command = parse_run_command(
            body, subject=subject, installation_id=state.installation_id
        )
        decision = claim_command(
            db, body, subject=subject, worker_id=worker_id, lease_seconds=lease_seconds
        )
        disposition = _unclaimed(decision)
        if disposition is not None and disposition.action == "quarantine":
            db.rollback()
        return PreparedDelivery(command, decision, disposition)
    except (InvalidCommandEnvelope, RecursionError):
        db.rollback()
        return PreparedDelivery(
            None, None, DeliveryDisposition("quarantine", "invalid_run_command")
        )
    except SQLAlchemyError:
        db.rollback()
        return PreparedDelivery(
            command, None, DeliveryDisposition("defer", "database_unavailable", 5.0)
        )


def settle_run_delivery(
    db: Session, command: RunCommand, claim: Claim
) -> DeliveryDisposition:
    """Inspect committed outcome, never the bool returned by process_job."""
    # Discard staged mutations and identity-map state from the execution. Only
    # another committed runtime transaction can authorize completion.
    db.rollback()
    try:
        disposition = _settle_locked(db, command, claim)
        db.commit()
        return disposition
    except SQLAlchemyError:
        db.rollback()
        return DeliveryDisposition("defer", "database_unavailable", 5.0)


def _settle_locked(
    db: Session, command: RunCommand, claim: Claim
) -> DeliveryDisposition:
    conversation = (
        db.query(Conversation)
        .filter_by(id=command.conversation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    run = (
        db.query(Run)
        .filter_by(id=command.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = (
        db.query(RunJob)
        .filter_by(run_id=command.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    previous = (
        db.query(MessageDisposition)
        .filter_by(message_id=command.message_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if previous is not None and previous.payload_digest != command.body_digest:
        return DeliveryDisposition("quarantine", "digest_mismatch")
    if (
        claim.run_id != command.run_id
        or previous is None
        or previous.disposition not in {"claimed", "deferred", "completed"}
        or (previous.disposition != "completed" and previous.error_code is not None)
    ):
        return DeliveryDisposition("reconcile", "admission_unproven")

    if previous.disposition == "completed":
        return DeliveryDisposition("ack", "delivery_completed")

    def complete(code: str) -> DeliveryDisposition:
        # Never replace the initial digest or handler version.
        previous.disposition = "completed"
        previous.error_code = code
        return DeliveryDisposition("ack", code)

    if run is None:
        # MessageDisposition intentionally survives the run's cascading deletion.
        return complete("admitted_run_deleted")
    if (
        conversation is None
        or job is None
        or run.conversation_id != command.conversation_id
        or run.project_id != command.project_id
        or conversation.project_id != command.project_id
    ):
        return DeliveryDisposition("reconcile", "durable_state_incomplete")
    if run.status in {"completed", "failed", "cancelled", "timed_out", "stale"}:
        return complete("run_terminal")
    if run.status == "waiting_connector":
        if job.owner_id is None and job.lease_until is None:
            return complete("connector_wait")
        return DeliveryDisposition("reconcile", "connector_wait_incomplete")
    if job.dispatch_generation > command.dispatch_generation:
        state = (
            db.query(MessageTransportState)
            .filter_by(id=1)
            .populate_existing()
            .one_or_none()
        )
        replacement = (
            db.query(Outbox)
            .filter_by(
                run_id=run.id,
                dispatch_generation=job.dispatch_generation,
                destination="command",
                type="run.execute",
                schema_version=1,
            )
            .one_or_none()
        )
        if (
            state is not None
            and replacement is not None
            and replacement.conversation_id == command.conversation_id
            and replacement.transport_epoch == state.transport_epoch
            and replacement.subject
            == f"openship.{state.installation_id.hex}.v1.command.run.execute"
            and replacement.message_id
            == uuid.uuid5(
                state.installation_id,
                f"run.execute:{run.id}:{job.dispatch_generation}:{state.transport_epoch}",
            )
        ):
            return complete("generation_handoff")
        return DeliveryDisposition("reconcile", "replacement_missing")
    if run.fence != claim.fence or job.fence != claim.fence:
        return DeliveryDisposition("reconcile", "ownership_moved")
    now = datetime.now(UTC)
    lease = job.lease_until
    if lease is not None:
        lease = (
            lease.replace(tzinfo=UTC) if lease.tzinfo is None else lease.astimezone(UTC)
        )
    if job.owner_id is not None and lease is not None and lease > now:
        return DeliveryDisposition("defer", "execution_incomplete", 5.0)
    return DeliveryDisposition("reconcile", "ownership_expired")
