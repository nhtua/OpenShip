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
from .models import MessageTransportState
from .topology import TopologyCompatibilityError, ensure_topology

logger = logging.getLogger("openship.messaging.relay")

_NOTIFY_CHANNEL = "openship_outbox"
_LEADER_KEY_A = 0x4F534850
_LEADER_KEY_B = 0x4D534731
_MAX_ENVELOPE_BYTES = 16 * 1024
_PUBLISHABLE = ("event", "command", "control", "failure")


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
        if run is None:
            raise ValueError("failure publication is missing its durable run reference")
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
        events = {}
        runs = {}
        jobs = {}
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
