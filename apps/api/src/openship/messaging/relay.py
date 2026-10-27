"""PostgreSQL-notified, confirmed JetStream outbox publication."""

from __future__ import annotations

import asyncio
import json
import logging
import select
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg2
from sqlalchemy import case, func
from sqlalchemy import select as sql_select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import aliased, sessionmaker

from ..config import settings
from ..database.session import SessionLocal
from ..events.service import Event
from ..runs.models import Outbox, Run, RunJob
from .connection import connect_nats
from .failure import FailureHandoff
from .models import MessageFailure, MessageTransportState, RetentionTask
from .topology import TopologyCompatibilityError, ensure_topology

logger = logging.getLogger("openship.messaging.relay")

_NOTIFY_CHANNEL = "openship_outbox"
_LEADER_KEY_A = 0x4F534850
_LEADER_KEY_B = 0x4D534731
_MAX_ENVELOPE_BYTES = 16 * 1024
_PUBLISHABLE = ("event", "command", "control", "failure", "maintenance")


@dataclass(frozen=True)
class OutboxEnvelope:
    id: uuid.UUID
    claim_id: uuid.UUID
    message_id: uuid.UUID
    subject: str
    body: bytes


@dataclass(frozen=True)
class PublicationReceipt:
    message_id: uuid.UUID
    stream: str
    sequence: int
    duplicate: bool


@dataclass(frozen=True)
class BatchResult:
    claimed: int
    published: int


def _utc_iso(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(UTC)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _record_envelope(
    row: Outbox,
    state: MessageTransportState,
    claim_id: uuid.UUID,
    *,
    event: Event | None,
    run: Run | None,
    job: RunJob | None,
    failure: MessageFailure | None = None,
    maintenance_task: RetentionTask | None = None,
) -> OutboxEnvelope:
    record: dict[str, object] = {
        "schema_version": row.schema_version,
        "message_id": str(row.message_id),
        "kind": row.type,
        "installation_id": str(state.installation_id),
        "transport_epoch": str(row.transport_epoch),
        "source_service": "openship",
        "created_at": _utc_iso(row.created_at),
        "subject": row.subject,
    }
    if row.destination not in {"failure", "maintenance"}:
        if row.conversation_id is None:
            raise ValueError(
                "conversation-scoped publication is missing its conversation"
            )
        record["conversation_id"] = str(row.conversation_id)
    if event is not None:
        record["project_id"] = str(event.project_id) if event.project_id else None
        record["event_id"] = str(event.id)
        record["sequence"] = event.sequence
        record["event_type"] = event.type
        record["correlation_id"] = str(event.run_id or event.conversation_id)
        record["causation_id"] = str(event.id)
    if run is not None:
        record["project_id"] = str(run.project_id)
        record["run_id"] = str(run.id)
        record["correlation_id"] = str(run.id)
        record["causation_id"] = str(event.id) if event is not None else str(run.id)
    if row.dispatch_generation is not None:
        record["dispatch_generation"] = row.dispatch_generation
    if row.destination == "event":
        if event is None:
            raise ValueError("event publication refers to a missing event")
        record["kind"] = "event"
    elif row.destination == "command":
        if run is None or row.dispatch_generation is None:
            raise ValueError("run command is missing its durable run reference")
        if job is None:
            raise ValueError("run command is missing its durable job reference")
        # A lost PubAck can leave this immutable command pending after its run
        # has resumed or safely requeued. Republish its original generation;
        # exact acquisition disposes obsolete deliveries without another effect.
        record["transport_epoch"] = str(row.transport_epoch)
    elif row.destination == "control":
        if run is None or event is None:
            raise ValueError("control publication is missing its durable run or event")
    elif row.destination == "failure":
        if (
            failure is None
            or row.failure_id != failure.id
            or failure.classification != row.type
            or row.conversation_id is not None
            or row.run_id is not None
            or row.event_id is not None
        ):
            raise ValueError(
                "failure publication is missing its durable failure reference"
            )
        record["kind"] = f"failure.{failure.classification}"
        record["failure_id"] = str(failure.id)
    elif row.destination == "maintenance":
        if (
            maintenance_task is None
            or row.maintenance_task_id != maintenance_task.id
            or row.type != "retention.execute"
            or row.conversation_id is not None
            or row.run_id is not None
            or row.event_id is not None
            or row.failure_id is not None
        ):
            raise ValueError("maintenance publication is missing its durable task")
        record["task_id"] = str(maintenance_task.id)
        record["correlation_id"] = str(maintenance_task.id)
        record["causation_id"] = str(maintenance_task.id)
    else:
        raise ValueError("outbox destination is not publishable")

    body = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_ENVELOPE_BYTES:
        raise ValueError("messaging envelope exceeds the 16 KiB limit")
    if row.message_id is None or row.subject is None or row.transport_epoch is None:
        raise ValueError("outbox publication is missing durable transport identity")
    expected_subject_prefix = f"openship.{state.installation_id.hex}.v1."
    if not row.subject.startswith(expected_subject_prefix):
        raise ValueError(
            "outbox publication subject is outside its installation namespace"
        )
    if row.destination == "failure" and row.subject != (
        f"{expected_subject_prefix}failure.{failure.classification}"
    ):
        raise ValueError(
            "failure publication subject does not match its classification"
        )
    if row.destination == "maintenance" and row.subject != (
        f"{expected_subject_prefix}command.maintenance.retention"
    ):
        raise ValueError("maintenance publication subject is not the retention command")
    return OutboxEnvelope(row.id, claim_id, row.message_id, row.subject, body)


def _earliest_unfinished_event_filter():
    previous_outbox = aliased(Outbox)
    previous_event = aliased(Event)
    return ~(
        sql_select(previous_outbox.id)
        .join(previous_event, previous_outbox.event_id == previous_event.id)
        .where(
            previous_outbox.destination == "event",
            previous_outbox.processed_at.is_(None),
            previous_outbox.conversation_id == Outbox.conversation_id,
            previous_event.sequence < Event.sequence,
        )
        .exists()
    )


def claim_outbox_batch(
    session_factory: sessionmaker = SessionLocal,
    *,
    limit: int | None = None,
    lease_seconds: int | None = None,
    now: datetime | None = None,
    conversation_id: uuid.UUID | None = None,
    outbox_id: uuid.UUID | None = None,
    failure_id: uuid.UUID | None = None,
    maintenance_task_id: uuid.UUID | None = None,
) -> list[OutboxEnvelope]:
    """Claim due publications in a short transaction, preserving event order."""
    claim_id = uuid.uuid4()
    claimed_at = now or datetime.now(UTC)
    batch_size = settings.messaging_publish_batch_size if limit is None else limit
    lease_duration = (
        settings.messaging_publish_lease_seconds
        if lease_seconds is None
        else lease_seconds
    )
    if not 1 <= batch_size <= 256:
        raise ValueError("outbox claim limit must be between 1 and 256")
    if not 5 <= lease_duration <= 300:
        raise ValueError("outbox publisher lease must be between 5 and 300 seconds")
    records: list[OutboxEnvelope] = []
    with session_factory() as db:
        state = db.get(MessageTransportState, 1)
        if state is None:
            raise RuntimeError(
                "messaging state is missing; apply database migrations first"
            )
        query = (
            db.query(Outbox)
            .outerjoin(Event, Outbox.event_id == Event.id)
            .filter(
                Outbox.destination.in_(_PUBLISHABLE),
                Outbox.processed_at.is_(None),
                Outbox.next_attempt_at <= claimed_at,
                (Outbox.publisher_lease_until.is_(None))
                | (Outbox.publisher_lease_until <= claimed_at),
            )
            .filter(
                (Outbox.destination != "event") | _earliest_unfinished_event_filter()
            )
        )
        if conversation_id is not None:
            query = query.filter(Outbox.conversation_id == conversation_id)
        if outbox_id is not None:
            query = query.filter(Outbox.id == outbox_id)
        if failure_id is not None:
            query = query.filter(
                Outbox.destination == "failure", Outbox.failure_id == failure_id
            )
        if maintenance_task_id is not None:
            query = query.filter(
                Outbox.destination == "maintenance",
                Outbox.maintenance_task_id == maintenance_task_id,
            )
        query = (
            query.order_by(
                Outbox.created_at.asc(), Event.sequence.asc(), Outbox.id.asc()
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=Outbox)
        )
        rows = query.all()
        if not rows:
            db.commit()
            return records
        event_ids = {row.event_id for row in rows if row.event_id is not None}
        run_ids = {row.run_id for row in rows if row.run_id is not None}
        failure_ids = {row.failure_id for row in rows if row.failure_id is not None}
        maintenance_task_ids = {
            row.maintenance_task_id
            for row in rows
            if row.maintenance_task_id is not None
        }
        events = {}
        runs = {}
        jobs = {}
        failures = {}
        maintenance_tasks = {}
        if event_ids:
            events = {
                event.id: event
                for event in db.query(Event).filter(Event.id.in_(event_ids)).all()
            }
        if run_ids:
            runs = {
                run.id: run for run in db.query(Run).filter(Run.id.in_(run_ids)).all()
            }
            jobs = {
                job.run_id: job
                for job in db.query(RunJob).filter(RunJob.run_id.in_(run_ids)).all()
            }
        if failure_ids:
            failures = {
                failure.id: failure
                for failure in db.query(MessageFailure)
                .filter(MessageFailure.id.in_(failure_ids))
                .all()
            }
        if maintenance_task_ids:
            maintenance_tasks = {
                task.id: task
                for task in db.query(RetentionTask)
                .filter(RetentionTask.id.in_(maintenance_task_ids))
                .all()
            }
        for row in rows:
            row.publisher_claim_id = claim_id
            row.publisher_lease_until = claimed_at + timedelta(seconds=lease_duration)
            row.publish_attempts += 1
            records.append(
                _record_envelope(
                    row,
                    state,
                    claim_id,
                    event=events.get(row.event_id),
                    run=runs.get(row.run_id),
                    job=jobs.get(row.run_id),
                    failure=failures.get(row.failure_id),
                    maintenance_task=maintenance_tasks.get(row.maintenance_task_id),
                )
            )
        db.commit()
        return records


async def publish_record(js, record: OutboxEnvelope) -> PublicationReceipt:
    """Publish one envelope and wait for JetStream's durable publish ACK."""
    ack = await js.publish(
        record.subject,
        record.body,
        headers={"Nats-Msg-Id": str(record.message_id)},
    )
    return PublicationReceipt(
        message_id=record.message_id,
        stream=ack.stream,
        sequence=ack.seq,
        duplicate=bool(ack.duplicate),
    )


def failure_handoff_state(
    session_factory: sessionmaker,
    handoff: FailureHandoff,
) -> tuple[bool, bool]:
    """Return whether the ledger/outbox binding is valid and already settled."""
    with session_factory() as db:
        row = (
            db.query(Outbox)
            .filter_by(
                id=handoff.outbox_id,
                failure_id=handoff.failure_id,
                destination="failure",
            )
            .one_or_none()
        )
        failure = db.get(MessageFailure, handoff.failure_id)
        state = db.get(MessageTransportState, 1)
        timestamp = failure.source_timestamp if failure is not None else None
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        expected_payload = json.dumps(
            {"failure_id": str(handoff.failure_id)}, separators=(",", ":")
        )
        expected_subject = (
            f"openship.{handoff.installation_id.hex}.v1.failure."
            f"{handoff.classification}"
        )
        valid = (
            row is not None
            and failure is not None
            and state is not None
            and state.installation_id == handoff.installation_id
            and state.transport_epoch == handoff.source_transport_epoch
            and failure.installation_id == handoff.installation_id
            and failure.source_transport_epoch == handoff.source_transport_epoch
            and failure.source_stream == handoff.source_identity.stream
            and failure.source_sequence == handoff.source_identity.sequence
            and timestamp == handoff.source_identity.timestamp
            and failure.body_digest == handoff.body_digest
            and failure.classification == handoff.classification
            and failure.error_code == handoff.error_code
            and row.type == handoff.classification
            and row.message_id
            == uuid.uuid5(
                handoff.installation_id,
                f"failure.notice:{handoff.failure_id}",
            )
            and row.subject == expected_subject
            and row.payload == expected_payload
            and row.transport_epoch == handoff.source_transport_epoch
            and row.conversation_id is None
            and row.run_id is None
            and row.event_id is None
            and row.dispatch_generation is None
        )
        return valid, bool(valid and row.processed_at is not None)


async def publish_failure_handoff(
    js,
    handoff: FailureHandoff,
    *,
    session_factory: sessionmaker = SessionLocal,
    run_sync: Callable = asyncio.to_thread,
    now: datetime | None = None,
) -> bool:
    """Confirm one exact failure row under the general relay's lease protocol."""
    valid, processed = await run_sync(failure_handoff_state, session_factory, handoff)
    if not valid:
        return False
    if processed:
        return True
    records = await run_sync(
        claim_outbox_batch,
        session_factory,
        limit=1,
        now=now,
        outbox_id=handoff.outbox_id,
        failure_id=handoff.failure_id,
    )
    if not records:
        valid, processed = await run_sync(
            failure_handoff_state, session_factory, handoff
        )
        return valid and processed
    record = records[0]
    try:
        await publish_record(js, record)
    except Exception as error:  # noqa: BLE001 - retain source until confirmed
        await run_sync(
            _settle_publication,
            session_factory,
            record,
            error=error,
        )
        logger.warning(
            "failure_notice_publish_deferred failure_id=%s error_kind=%s",
            handoff.failure_id,
            type(error).__name__,
        )
        return False
    return await run_sync(
        _settle_publication,
        session_factory,
        record,
        error=None,
    )


def _settle_publication(
    session_factory: sessionmaker,
    record: OutboxEnvelope,
    *,
    error: Exception | None,
    now: datetime | None = None,
) -> bool:
    settled_at = now or datetime.now(UTC)
    with session_factory() as db:
        row = (
            db.query(Outbox)
            .filter(
                Outbox.id == record.id,
                Outbox.processed_at.is_(None),
                Outbox.publisher_claim_id == record.claim_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            db.rollback()
            return False
        row.publisher_claim_id = None
        row.publisher_lease_until = None
        if error is None:
            row.processed_at = settled_at
            row.last_error = None
        else:
            delay = min(60, 2 ** min(row.publish_attempts, 6))
            row.next_attempt_at = settled_at + timedelta(seconds=delay)
            row.last_error = type(error).__name__[:160]
        db.commit()
        return True


async def publish_pending_batch(
    js,
    session_factory: sessionmaker = SessionLocal,
    *,
    limit: int | None = None,
    lease_seconds: int | None = None,
    conversation_id: uuid.UUID | None = None,
) -> BatchResult:
    """Claim and confirm one bounded batch; failed rows receive timed retries."""
    records = await asyncio.to_thread(
        claim_outbox_batch,
        session_factory,
        limit=limit,
        lease_seconds=lease_seconds,
        conversation_id=conversation_id,
    )
    published = 0
    for record in records:
        try:
            await publish_record(js, record)
        except Exception as error:  # noqa: BLE001 - a failed publish remains durable
            await asyncio.to_thread(
                _settle_publication,
                session_factory,
                record,
                error=error,
            )
            logger.warning(
                "outbox publish deferred message_id=%s error_kind=%s",
                record.message_id,
                type(error).__name__,
            )
        else:
            settled = await asyncio.to_thread(
                _settle_publication,
                session_factory,
                record,
                error=None,
            )
            published += int(settled)
    return BatchResult(len(records), published)


def next_outbox_due_at(
    session_factory: sessionmaker = SessionLocal,
    *,
    conversation_id: uuid.UUID | None = None,
) -> datetime | None:
    """Return the next retry/expired-claim deadline; None means wait for NOTIFY."""
    retry_at = func.coalesce(Outbox.next_attempt_at, Outbox.created_at)
    available_at = case(
        (Outbox.publisher_lease_until > retry_at, Outbox.publisher_lease_until),
        else_=retry_at,
    )
    with session_factory() as db:
        query = (
            db.query(func.min(available_at))
            .outerjoin(Event, Outbox.event_id == Event.id)
            .filter(
                Outbox.destination.in_(_PUBLISHABLE),
                Outbox.processed_at.is_(None),
            )
            .filter(
                (Outbox.destination != "event") | _earliest_unfinished_event_filter()
            )
        )
        if conversation_id is not None:
            query = query.filter(Outbox.conversation_id == conversation_id)
        due = query.scalar()
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return due.astimezone(UTC)


def _postgres_dsn(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("the messaging relay requires PostgreSQL LISTEN/NOTIFY")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _transport_installation_id(session_factory: sessionmaker) -> uuid.UUID:
    with session_factory() as db:
        state = db.get(MessageTransportState, 1)
        if state is None:
            raise RuntimeError(
                "messaging state is missing; apply database migrations first"
            )
        return state.installation_id


def _connect_listener(dsn: str):
    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(f"LISTEN {_NOTIFY_CHANNEL}")
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", (_LEADER_KEY_A, _LEADER_KEY_B)
        )
        is_leader = bool(cursor.fetchone()[0])
    return connection, is_leader


def _try_leadership(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", (_LEADER_KEY_A, _LEADER_KEY_B)
        )
        return bool(cursor.fetchone()[0])


def _wait_for_listener(connection, timeout: float) -> bool:
    readable, _writable, _exceptional = select.select(
        [connection.fileno()], [], [], max(0.0, timeout)
    )
    if not readable:
        return False
    connection.poll()
    received = bool(connection.notifies)
    connection.notifies.clear()
    return received


async def _wait_for_signal(
    stop: asyncio.Event,
    pg_connection,
    nats_wakeup: asyncio.Event,
    *,
    timeout: float | None,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + max(0.0, timeout)
    while not stop.is_set():
        if nats_wakeup.is_set():
            nats_wakeup.clear()
            return
        remaining = None if deadline is None else deadline - loop.time()
        if remaining is not None and remaining <= 0:
            return
        wait_seconds = 1.0 if remaining is None else min(1.0, remaining)
        listener_task = asyncio.create_task(
            asyncio.to_thread(_wait_for_listener, pg_connection, wait_seconds)
        )
        stop_task = asyncio.create_task(stop.wait())
        nats_task = asyncio.create_task(nats_wakeup.wait())
        tasks = {listener_task, stop_task, nats_task}
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if nats_task in done:
                nats_wakeup.clear()
                return
            if stop_task in done:
                return
            if listener_task in done and listener_task.result():
                return
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()


async def _run_leader(
    stop: asyncio.Event,
    pg_connection,
    session_factory: sessionmaker,
    nats_client_factory: Callable,
) -> None:
    nats_wakeup = asyncio.Event()

    async def on_reconnected() -> None:
        nats_wakeup.set()

    client = await nats_client_factory(
        name="openship-outbox-relay",
        reconnected_cb=on_reconnected,
    )
    js = client.jetstream(timeout=5)
    try:
        installation_id = await asyncio.to_thread(
            _transport_installation_id,
            session_factory,
        )
        topology = await ensure_topology(
            apply=False,
            js=js,
            installation_id=installation_id,
        )
        if not topology.ready:
            raise TopologyCompatibilityError(
                "relay startup refused missing or drifted JetStream streams"
            )
        nats_wakeup.set()
        while not stop.is_set():
            while not stop.is_set():
                result = await publish_pending_batch(js, session_factory)
                if result.claimed == 0:
                    break
            due = await asyncio.to_thread(next_outbox_due_at, session_factory)
            timeout = (
                None
                if due is None
                else max(0.0, (due - datetime.now(UTC)).total_seconds())
            )
            await _wait_for_signal(stop, pg_connection, nats_wakeup, timeout=timeout)
    finally:
        await client.drain()


async def run_relay(
    stop: asyncio.Event,
    *,
    session_factory: sessionmaker = SessionLocal,
    database_url: str | None = None,
    nats_client_factory: Callable = connect_nats,
) -> None:
    """Run one notification-driven publisher; PostgreSQL advisory lock elects its leader."""
    dsn = _postgres_dsn(database_url or settings.database_url)
    while not stop.is_set():
        connection = None
        try:
            connection, is_leader = await asyncio.to_thread(_connect_listener, dsn)
            if not is_leader:
                while not stop.is_set():
                    await asyncio.to_thread(_wait_for_listener, connection, 1.0)
                    is_leader = await asyncio.to_thread(_try_leadership, connection)
                    if is_leader:
                        break
                if stop.is_set():
                    break
            await _run_leader(stop, connection, session_factory, nats_client_factory)
        except asyncio.CancelledError:
            raise
        except TopologyCompatibilityError:
            logger.error(
                "outbox relay stopped because JetStream topology is incompatible"
            )
            raise
        except Exception as error:  # noqa: BLE001 - restart after connection loss
            logger.warning("outbox relay restarting after %s", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
        finally:
            if connection is not None:
                try:
                    await asyncio.to_thread(connection.close)
                except Exception as close_error:  # noqa: BLE001 - close best effort
                    logger.debug(
                        "relay listener close failed error_kind=%s",
                        type(close_error).__name__,
                    )


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run_relay(stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
