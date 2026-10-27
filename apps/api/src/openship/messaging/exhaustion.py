"""Validate MaxDeliver advisories and persist live-owner observations."""

# Broker, database, and provider boundaries retain advisory deliveries on error.

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Outbox, Run, RunJob
from .command import (
    InvalidCommandEnvelope,
    RunCommand,
    parse_run_command,
)
from .failure import (
    JetStreamSourceIdentity,
)
from .models import MessageDisposition, MessageExhaustion, MessageTransportState

MAX_ADVISORY_BYTES = 4096
LIVE_OWNER_CHECK_SECONDS = 10
MAX_DELIVERIES_SUBJECT = "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_RUNS.run-v1"
_ADVISORY_FIELDS = frozenset(
    {"type", "id", "timestamp", "stream", "consumer", "stream_seq", "deliveries"}
)


class InvalidMaxDeliverAdvisory(ValueError):
    """A MaxDeliver advisory is malformed or belongs to another durable."""


@dataclass(frozen=True)
class MaxDeliverAdvisory:
    """Validated, non-authoritative delivery-exhaustion metadata."""

    source_sequence: int
    delivery_count: int
    emitted_at: datetime


@dataclass(frozen=True)
class ExhaustionObservationReceipt:
    """Small detached result for one durable owner observation."""

    id: uuid.UUID
    state: str


@dataclass(frozen=True)
class UnboundExhaustionSource:
    """A source definitively lacks a valid persisted command identity."""


@dataclass(frozen=True)
class ExhaustionSettlementClaim:
    """Short database claim protecting one exact source cleanup attempt."""

    id: uuid.UUID
    installation_id: uuid.UUID
    source_transport_epoch: uuid.UUID
    source_sequence: int
    source_timestamp: datetime | None
    payload_digest: str | None


@dataclass(frozen=True)
class ExhaustionObservationSnapshot:
    """Content-free identity needed to verify a stored source message."""

    id: uuid.UUID
    installation_id: uuid.UUID
    source_transport_epoch: uuid.UUID
    source_sequence: int
    source_timestamp: datetime | None
    payload_digest: str | None
    state: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidMaxDeliverAdvisory()
        value[key] = item
    return value


def parse_max_deliver_advisory(subject: str, body: bytes) -> MaxDeliverAdvisory:
    """Parse only the exact pinned-server advisory for the run durable."""
    if (
        subject != MAX_DELIVERIES_SUBJECT
        or not isinstance(body, bytes)
        or len(body) > MAX_ADVISORY_BYTES
    ):
        raise InvalidMaxDeliverAdvisory()
    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or record.keys() != _ADVISORY_FIELDS:
            raise InvalidMaxDeliverAdvisory()
        if (
            record["type"] != "io.nats.jetstream.advisory.v1.max_deliver"
            or record["stream"] != "OS_RUNS"
            or record["consumer"] != "run-v1"
            or not isinstance(record["id"], str)
            or not record["id"]
            or len(record["id"]) > 128
            or type(record["stream_seq"]) is not int
            or record["stream_seq"] < 1
            or type(record["deliveries"]) is not int
            or record["deliveries"] < 1
        ):
            raise InvalidMaxDeliverAdvisory()
        emitted_at = datetime.fromisoformat(record["timestamp"])
        if emitted_at.tzinfo is None or emitted_at.utcoffset() is None:
            raise InvalidMaxDeliverAdvisory()
        return MaxDeliverAdvisory(
            source_sequence=record["stream_seq"],
            delivery_count=record["deliveries"],
            emitted_at=emitted_at.astimezone(UTC),
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise InvalidMaxDeliverAdvisory() from None


def _observation_id(installation_id, epoch, source) -> uuid.UUID:
    return uuid.uuid5(
        installation_id,
        f"max-deliver:v1:{epoch}:{source.stream}:{source.sequence}",
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _command_is_persisted(db: Session, command: RunCommand) -> bool:
    row = (
        db.query(Outbox)
        .filter_by(
            run_id=command.run_id,
            dispatch_generation=command.dispatch_generation,
        )
        .one_or_none()
    )
    run = db.get(Run, command.run_id)
    if row is None or run is None:
        return False
    expected_id = uuid.uuid5(
        command.installation_id,
        f"run.execute:{command.run_id}:{command.dispatch_generation}:{command.transport_epoch}",
    )
    return (
        row.destination == "command"
        and row.type == "run.execute"
        and row.schema_version == 1
        and row.message_id == command.message_id == expected_id
        and row.transport_epoch == command.transport_epoch
        and row.subject == command.subject
        and row.conversation_id == command.conversation_id
        and row.run_id == command.run_id
        and row.dispatch_generation == command.dispatch_generation
        and row.created_at is not None
        and _utc(row.created_at) == command.created_at
        and run.conversation_id == command.conversation_id
        and run.project_id == command.project_id
    )


def record_live_owner_observation(
    db: Session,
    command: RunCommand,
    *,
    body: bytes,
    source_identity: JetStreamSourceIdentity,
    delivery_count: int,
    checked_at: datetime | None = None,
) -> ExhaustionObservationReceipt | None:
    """Persist a watching state only while this exact generation has a live lease.

    The advisory delivery count is retained for diagnosis but is never used to
    fail a run, steal its lease, or replay its effect. Caller may ACK the
    advisory after this transaction commits; the exhausted source remains in
    JetStream until a later recovery step commits and removes it by exact seq.
    """
    source = source_identity.validated()
    if (
        source is None
        or type(delivery_count) is not int
        or delivery_count < 1
        or not isinstance(body, bytes)
    ):
        db.rollback()
        return None
    checked_at = _utc(checked_at or datetime.now(UTC))
    try:
        initial_state = db.get(MessageTransportState, 1)
        if initial_state is None:
            db.rollback()
            return None
        parsed = parse_run_command(
            body,
            subject=command.subject,
            installation_id=initial_state.installation_id,
        )
        if parsed != command or not _command_is_persisted(db, command):
            db.rollback()
            return None

        conversation_id = (
            db.query(Run.conversation_id).filter_by(id=command.run_id).scalar()
        )
        if conversation_id is None:
            db.rollback()
            return None
        conversation = (
            db.query(Conversation)
            .filter_by(id=conversation_id)
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
        state = (
            db.query(MessageTransportState)
            .filter_by(id=1)
            .with_for_update(read=True)
            .populate_existing()
            .one_or_none()
        )
        lease_until = _utc(job.lease_until) if job and job.lease_until else None
        if (
            conversation is None
            or run is None
            or job is None
            or state is None
            or state.installation_id != command.installation_id
            or state.transport_epoch != command.transport_epoch
            or source.stream != "OS_RUNS"
            or run.status != "running"
            or job.dispatch_generation != command.dispatch_generation
            or not job.owner_id
            or job.fence < 1
            or run.fence != job.fence
            or lease_until is None
            or lease_until <= checked_at
        ):
            db.rollback()
            return None

        observation_id = _observation_id(
            state.installation_id, state.transport_epoch, source
        )
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if observation is None:
            observation = MessageExhaustion(
                id=observation_id,
                installation_id=state.installation_id,
                source_transport_epoch=state.transport_epoch,
                source_stream=source.stream,
                source_sequence=source.sequence,
                source_timestamp=source.timestamp,
                consumer_name="run-v1",
                delivery_count=delivery_count,
                state="watching_live_owner",
            )
            db.add(observation)
        elif observation.state in {"resolved", "source_missing", "blocked"}:
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)
        elif (
            observation.source_timestamp is None
            or _utc(observation.source_timestamp) != source.timestamp
        ):
            db.rollback()
            return None

        observation.delivery_count = max(observation.delivery_count, delivery_count)
        observation.state = "watching_live_owner"
        observation.payload_digest = command.body_digest
        observation.message_id = command.message_id
        observation.run_id = command.run_id
        observation.dispatch_generation = command.dispatch_generation
        observation.owner_id = job.owner_id
        observation.owner_fence = job.fence
        observation.lease_until = lease_until
        observation.next_check_at = min(
            checked_at + timedelta(seconds=LIVE_OWNER_CHECK_SECONDS), lease_until
        )
        observation.error_code = None
        observation.updated_at = checked_at
        db.commit()
        return ExhaustionObservationReceipt(observation_id, "watching_live_owner")
    except (InvalidCommandEnvelope, SQLAlchemyError):
        db.rollback()
        return None


def record_exhaustion_observation(
    db: Session,
    *,
    body: bytes,
    subject: str,
    source_identity: JetStreamSourceIdentity,
    delivery_count: int,
    checked_at: datetime | None = None,
    advisory_sequence: int | None = None,
    discovery_method: str = "advisory",
    recovery_operator: str | None = None,
) -> ExhaustionObservationReceipt | UnboundExhaustionSource | None:
    """Persist an observation or classify a definitively unbound source.

    ``None`` means validation or persistence could not be confirmed and must be
    retried. Only ``UnboundExhaustionSource`` authorizes the quarantine path.
    """
    source = source_identity.validated()
    operator = recovery_operator.strip() if isinstance(recovery_operator, str) else None
    valid_discovery = (
        discovery_method == "advisory"
        and type(delivery_count) is int
        and delivery_count >= 1
        and recovery_operator is None
    ) or (
        discovery_method == "capacity_scan"
        and type(delivery_count) is int
        and delivery_count == 0
        and operator is not None
        and 1 <= len(operator) <= 255
    )
    if (
        source is None
        or not valid_discovery
        or not isinstance(body, bytes)
        or (
            advisory_sequence is not None
            and (
                type(advisory_sequence) is not int
                or advisory_sequence != source.sequence
            )
        )
    ):
        db.rollback()
        return None
    checked_at = _utc(checked_at or datetime.now(UTC))
    try:
        state = db.get(MessageTransportState, 1)
        if state is None:
            db.rollback()
            return None
        command = parse_run_command(
            body, subject=subject, installation_id=state.installation_id
        )
        if not _command_is_persisted(db, command):
            db.rollback()
            return UnboundExhaustionSource()
        state = (
            db.query(MessageTransportState)
            .filter_by(id=1)
            .with_for_update(read=True)
            .populate_existing()
            .one_or_none()
        )
        if (
            state is None
            or state.installation_id != command.installation_id
            or state.transport_epoch != command.transport_epoch
        ):
            db.rollback()
            return UnboundExhaustionSource()

        observation_id = _observation_id(
            state.installation_id, state.transport_epoch, source
        )
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if observation is None:
            observation = MessageExhaustion(
                id=observation_id,
                installation_id=state.installation_id,
                source_transport_epoch=state.transport_epoch,
                source_stream=source.stream,
                source_sequence=source.sequence,
                source_timestamp=source.timestamp,
                consumer_name="run-v1",
                delivery_count=delivery_count,
                discovery_method=discovery_method,
                recovery_operator=operator,
                state="pending",
            )
            db.add(observation)
        elif observation.state in {"resolved", "source_missing", "blocked"}:
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)
        elif (
            observation.source_timestamp is None
            or _utc(observation.source_timestamp) != source.timestamp
            or observation.payload_digest not in (None, command.body_digest)
            or observation.message_id not in (None, command.message_id)
            or observation.run_id not in (None, command.run_id)
            or observation.dispatch_generation
            not in (None, command.dispatch_generation)
        ):
            observation.state = "blocked"
            observation.error_code = "source_identity_conflict"
            observation.next_check_at = None
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)
        observation.delivery_count = max(observation.delivery_count, delivery_count)
        observation.state = (
            "watching_live_owner"
            if observation.state == "watching_live_owner"
            else "pending"
        )
        observation.payload_digest = command.body_digest
        observation.message_id = command.message_id
        observation.run_id = command.run_id
        observation.dispatch_generation = command.dispatch_generation
        observation.updated_at = checked_at
        db.commit()
        return refresh_live_owner_observation(db, observation_id, checked_at=checked_at)
    except InvalidCommandEnvelope:
        db.rollback()
        return UnboundExhaustionSource()
    except SQLAlchemyError:
        db.rollback()
        return None


def _block_observation(
    db: Session, observation_id: uuid.UUID, code: str, checked_at: datetime
) -> ExhaustionObservationReceipt | None:
    observation = (
        db.query(MessageExhaustion)
        .filter_by(id=observation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if observation is None:
        db.rollback()
        return None
    observation.state = "blocked"
    observation.error_code = code
    observation.next_check_at = None
    observation.updated_at = checked_at
    db.commit()
    return ExhaustionObservationReceipt(observation.id, observation.state)


def record_missing_source_observation(
    db: Session,
    *,
    source_sequence: int,
    delivery_count: int,
    checked_at: datetime | None = None,
) -> ExhaustionObservationReceipt | None:
    """Record an advisory whose OS_RUNS source was already removed."""
    checked_at = _utc(checked_at or datetime.now(UTC))
    if (
        type(source_sequence) is not int
        or source_sequence < 1
        or type(delivery_count) is not int
        or delivery_count < 1
    ):
        db.rollback()
        return None
    try:
        state = db.get(MessageTransportState, 1)
        if state is None:
            db.rollback()
            return None
        source = JetStreamSourceIdentity("OS_RUNS", source_sequence, checked_at)
        observation_id = _observation_id(
            state.installation_id, state.transport_epoch, source
        )
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if observation is None:
            observation = MessageExhaustion(
                id=observation_id,
                installation_id=state.installation_id,
                source_transport_epoch=state.transport_epoch,
                source_stream="OS_RUNS",
                source_sequence=source_sequence,
                source_timestamp=None,
                consumer_name="run-v1",
                delivery_count=delivery_count,
                state="source_missing",
                error_code="source_missing_before_observation",
                updated_at=checked_at,
            )
            db.add(observation)
        else:
            observation.delivery_count = max(observation.delivery_count, delivery_count)
            if observation.state == "pending":
                observation.state = "source_missing"
                observation.error_code = "source_missing_before_observation"
                observation.updated_at = checked_at
        db.commit()
        return ExhaustionObservationReceipt(observation.id, observation.state)
    except SQLAlchemyError:
        db.rollback()
        return None


def due_exhaustion_observation_ids(
    db: Session,
    *,
    checked_at: datetime | None = None,
    limit: int = 16,
) -> list[uuid.UUID]:
    """Return a bounded restart-resumable batch of due durable observations."""
    checked_at = _utc(checked_at or datetime.now(UTC))
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError("exhaustion recovery batch is outside the supported bound")
    try:
        rows = (
            db.query(MessageExhaustion.id)
            .filter(
                MessageExhaustion.state.in_(
                    ("pending", "watching_live_owner", "settling")
                ),
                or_(
                    MessageExhaustion.next_check_at.is_(None),
                    MessageExhaustion.next_check_at <= checked_at,
                ),
            )
            .order_by(MessageExhaustion.created_at, MessageExhaustion.id)
            .limit(limit)
            .all()
        )
        db.rollback()
        return [row[0] for row in rows]
    except SQLAlchemyError:
        db.rollback()
        return []


def exhaustion_observation_snapshot(
    db: Session, observation_id: uuid.UUID
) -> ExhaustionObservationSnapshot | None:
    """Read the persisted source binding without retaining a DB transaction."""
    try:
        row = db.get(MessageExhaustion, observation_id)
        if row is None:
            db.rollback()
            return None
        snapshot = ExhaustionObservationSnapshot(
            id=row.id,
            installation_id=row.installation_id,
            source_transport_epoch=row.source_transport_epoch,
            source_sequence=row.source_sequence,
            source_timestamp=row.source_timestamp,
            payload_digest=row.payload_digest,
            state=row.state,
        )
        db.rollback()
        return snapshot
    except SQLAlchemyError:
        db.rollback()
        return None


def claim_exhaustion_settlement(
    db: Session,
    observation_id: uuid.UUID,
    *,
    checked_at: datetime | None = None,
    lease_seconds: int = 30,
) -> ExhaustionSettlementClaim | None:
    """Claim a short exact-sequence deletion attempt, recoverable after timeout."""
    checked_at = _utc(checked_at or datetime.now(UTC))
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
        raise ValueError("exhaustion settlement lease is outside the supported bound")
    try:
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id, state="settling")
            .with_for_update(skip_locked=True)
            .populate_existing()
            .one_or_none()
        )
        if observation is None or (
            observation.next_check_at is not None
            and _utc(observation.next_check_at) > checked_at
        ):
            db.rollback()
            return None
        observation.next_check_at = checked_at + timedelta(seconds=lease_seconds)
        observation.updated_at = checked_at
        claim = ExhaustionSettlementClaim(
            id=observation.id,
            installation_id=observation.installation_id,
            source_transport_epoch=observation.source_transport_epoch,
            source_sequence=observation.source_sequence,
            source_timestamp=observation.source_timestamp,
            payload_digest=observation.payload_digest,
        )
        db.commit()
        return claim
    except SQLAlchemyError:
        db.rollback()
        return None


def mark_exhaustion_resolved(
    db: Session,
    observation_id: uuid.UUID,
    *,
    resolved_at: datetime | None = None,
) -> bool:
    """Commit completion after exact source cleanup or confirmed prior removal."""
    resolved_at = _utc(resolved_at or datetime.now(UTC))
    try:
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id, state="settling")
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if observation is None:
            db.rollback()
            return False
        observation.state = "resolved"
        observation.next_check_at = None
        observation.resolved_at = resolved_at
        observation.updated_at = resolved_at
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        return False


def _valid_replacement(
    db: Session,
    *,
    observation: MessageExhaustion,
    run: Run,
    job: RunJob,
    state: MessageTransportState,
) -> bool:
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
    return (
        replacement is not None
        and replacement.transport_epoch == state.transport_epoch
        and replacement.conversation_id == run.conversation_id
        and replacement.message_id
        == uuid.uuid5(
            state.installation_id,
            f"run.execute:{run.id}:{job.dispatch_generation}:{state.transport_epoch}",
        )
    )


def refresh_live_owner_observation(
    db: Session,
    observation_id: uuid.UUID,
    *,
    checked_at: datetime | None = None,
    _reconciled: bool = False,
) -> ExhaustionObservationReceipt | None:
    """Recheck DB ownership and advance only after a committed safe outcome.

    Expired leases use the existing recovery policy. An unstarted queued run
    receives a new generation and outbox command atomically before its old
    exhausted source can be removed. Provider/tool dispatch uncertainty stays
    blocked for the existing recovery/operator path.
    """
    checked_at = _utc(checked_at or datetime.now(UTC))
    try:
        observation = (
            db.query(MessageExhaustion)
            .filter_by(id=observation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if observation is None:
            db.rollback()
            return None
        if observation.state not in {"pending", "watching_live_owner"}:
            state = observation.state
            db.commit()
            return ExhaustionObservationReceipt(observation.id, state)
        if (
            observation.state == "watching_live_owner"
            and observation.next_check_at is not None
            and _utc(observation.next_check_at) > checked_at
        ):
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)
        if (
            observation.run_id is None
            or observation.dispatch_generation is None
            or observation.message_id is None
            or observation.payload_digest is None
            or (
                observation.state == "watching_live_owner"
                and (
                    observation.owner_id is None
                    or observation.owner_fence is None
                    or observation.lease_until is None
                )
            )
        ):
            return _block_observation(
                db, observation_id, "observation_identity_incomplete", checked_at
            )

        run_id = observation.run_id
        identity = db.query(Run.conversation_id).filter_by(id=run_id).scalar()
        if identity is None:
            return _block_observation(db, observation_id, "run_missing", checked_at)
        conversation = (
            db.query(Conversation)
            .filter_by(id=identity)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        run = (
            db.query(Run)
            .filter_by(id=run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        job = (
            db.query(RunJob)
            .filter_by(run_id=run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        state = (
            db.query(MessageTransportState)
            .filter_by(id=1)
            .with_for_update(read=True)
            .populate_existing()
            .one_or_none()
        )
        if conversation is None or run is None or job is None or state is None:
            return _block_observation(
                db, observation_id, "durable_state_incomplete", checked_at
            )
        if (
            state.installation_id != observation.installation_id
            or state.transport_epoch != observation.source_transport_epoch
            or run.conversation_id != conversation.id
        ):
            return _block_observation(
                db, observation_id, "source_epoch_moved", checked_at
            )

        if run.status in {"completed", "failed", "cancelled", "timed_out", "stale"}:
            observation.state = "settling"
            observation.next_check_at = None
            observation.error_code = "run_terminal"
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        if run.status == "waiting_connector":
            if job.owner_id is not None or job.lease_until is not None:
                return _block_observation(
                    db, observation_id, "connector_wait_incomplete", checked_at
                )
            observation.state = "settling"
            observation.next_check_at = None
            observation.error_code = "connector_wait"
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        if job.dispatch_generation > observation.dispatch_generation:
            if not _valid_replacement(
                db, observation=observation, run=run, job=job, state=state
            ):
                return _block_observation(
                    db, observation_id, "replacement_missing", checked_at
                )
            observation.state = "settling"
            observation.next_check_at = None
            observation.error_code = "generation_handoff"
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        if job.dispatch_generation != observation.dispatch_generation:
            return _block_observation(
                db, observation_id, "generation_moved_backwards", checked_at
            )

        lease_until = _utc(job.lease_until) if job.lease_until else None
        live_owner = (
            run.status == "running"
            and job.owner_id is not None
            and job.fence > 0
            and run.fence == job.fence
            and lease_until is not None
            and lease_until > checked_at
        )
        if live_owner and observation.state == "pending":
            observation.state = "watching_live_owner"
            observation.owner_id = job.owner_id
            observation.owner_fence = job.fence
            observation.lease_until = lease_until
            observation.next_check_at = min(
                checked_at + timedelta(seconds=LIVE_OWNER_CHECK_SECONDS), lease_until
            )
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)
        if (
            live_owner
            and job.owner_id == observation.owner_id
            and job.fence == observation.owner_fence
            and observation.state == "watching_live_owner"
        ):
            observation.lease_until = lease_until
            observation.next_check_at = min(
                checked_at + timedelta(seconds=LIVE_OWNER_CHECK_SECONDS), lease_until
            )
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        if live_owner and observation.state == "watching_live_owner":
            if job.fence < observation.owner_fence:
                return _block_observation(
                    db, observation_id, "owner_fence_moved_backwards", checked_at
                )
            if job.fence == observation.owner_fence:
                return _block_observation(
                    db, observation_id, "owner_changed_without_new_fence", checked_at
                )
            observation.owner_id = job.owner_id
            observation.owner_fence = job.fence
            observation.lease_until = lease_until
            observation.next_check_at = min(
                checked_at + timedelta(seconds=LIVE_OWNER_CHECK_SECONDS), lease_until
            )
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        expired_owner = (
            run.status == "running"
            and job.owner_id is not None
            and job.fence > 0
            and run.fence == job.fence
            and lease_until is not None
            and lease_until <= checked_at
        )
        if expired_owner and (
            observation.state == "pending"
            or (
                job.owner_id == observation.owner_id
                and job.fence == observation.owner_fence
            )
        ):
            db.rollback()
            if _reconciled:
                return _block_observation(
                    db, observation_id, "lease_reconcile_pending", checked_at
                )
            from ..runs.queue import reconcile_expired

            reclaimed = reconcile_expired(db)
            if reclaimed:
                return refresh_live_owner_observation(
                    db,
                    observation_id,
                    checked_at=checked_at,
                    _reconciled=True,
                )
            return _block_observation(
                db, observation_id, "lease_reconcile_pending", checked_at
            )

        if run.status == "queued" and job.owner_id is None and job.lease_until is None:
            from ..execution.recovery import has_uncertain_tool_dispatch

            if run.provider_started_at is not None or has_uncertain_tool_dispatch(
                db, run.id
            ):
                return _block_observation(
                    db, observation_id, "dispatch_uncertain", checked_at
                )
            old_message = (
                db.query(MessageDisposition)
                .filter_by(message_id=observation.message_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if (
                old_message is not None
                and old_message.payload_digest != observation.payload_digest
            ):
                return _block_observation(
                    db, observation_id, "source_digest_mismatch", checked_at
                )
            job.dispatch_generation += 1
            from ..messaging.outbox import enqueue_run

            enqueue_run(
                db,
                run.id,
                job.dispatch_generation,
                epoch=state.transport_epoch,
            )
            if old_message is not None:
                old_message.disposition = "completed"
                old_message.error_code = "max_deliver_requeued"
            observation.state = "settling"
            observation.next_check_at = None
            observation.error_code = "safe_requeue"
            observation.updated_at = checked_at
            db.commit()
            return ExhaustionObservationReceipt(observation.id, observation.state)

        return _block_observation(db, observation_id, "owner_state_changed", checked_at)
    except SQLAlchemyError:
        db.rollback()
        return None


def _call_with_resource(db: Session, function, args, kwargs):
    """Run one database operation and leave its thread-owned session clean."""
    db.rollback()
    try:
        return function(db, *args, **kwargs)
    finally:
        db.rollback()


def _call_without_resource(db: Session, function, args, kwargs):
    """Run a session-factory helper on the reserved maintenance thread."""
    db.rollback()
    try:
        return function(*args, **kwargs)
    finally:
        db.rollback()
