"""PostgreSQL and JetStream relay recovery and ordering contracts."""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import select
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import nats
import pytest
import pytest_asyncio
from nats.errors import AuthorizationError
from nats.errors import Error as NatsError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.events.service import append_event
from src.openship.messaging import relay
from src.openship.messaging.command import claim_command
from src.openship.messaging.delivery import prepare_run_delivery, settle_run_delivery
from src.openship.messaging.health import (
    check_worker_health,
    health_reply_subject,
    health_request_subject,
    subscribe_worker_health,
)
from src.openship.messaging.models import MessageDisposition, MessageTransportState
from src.openship.messaging.outbox import enqueue_run
from src.openship.messaging.relay import (
    _settle_publication,
    claim_outbox_batch,
    publish_pending_batch,
    publish_record,
)
from src.openship.messaging.topology import (
    TopologyCompatibilityError,
    ensure_topology,
    stream_configs,
)
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import claim_run, finalize
from src.openship.workspace.models import Project

pytestmark = [pytest.mark.postgres, pytest.mark.jetstream]
_native_to_thread = asyncio.to_thread


def _pg_command(sessions):
    with sessions() as db:
        state = db.get(MessageTransportState, 1)
        state.active = True
        state.execution_open = True
        user = User(
            username=f"claim_{uuid.uuid4().hex[:12]}",
            email=f"claim_{uuid.uuid4().hex}@example.test",
            password_hash="test",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Exact claim")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id, project_id=project.id, title="Exact claim"
        )
        db.add(conversation)
        db.flush()
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="queued",
            fence=0,
        )
        db.add(run)
        db.flush()
        job = RunJob(
            run_id=run.id,
            fence=0,
            dispatch_generation=1,
            available_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        db.add(job)
        db.flush()
        row_id = enqueue_run(db, run.id, 1, epoch=state.transport_epoch)
        db.flush()
        row = db.get(Outbox, row_id)
        envelope = relay._record_envelope(
            row, state, uuid.uuid4(), event=None, run=run, job=job
        )
        run_id, job_id = run.id, job.id
        db.commit()
        return run_id, job_id, envelope.body, envelope.subject


def test_pg_command_claim_exact_run_and_single_fence(relay_postgres):
    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    other_run_id, other_job_id, _other_body, _other_subject = _pg_command(sessions)
    with sessions() as db:
        decision = claim_command(
            db, body, subject=subject, worker_id="pg-worker", lease_seconds=30
        )
        assert decision.kind == "claimed"
        assert decision.claim.run_id == run_id
        assert decision.claim.fence == 1
        assert db.get(RunJob, job_id).owner_id == "pg-worker"
        assert db.get(Run, other_run_id).status == "queued"
        assert db.get(RunJob, other_job_id).fence == 0


def test_pg_command_claim_competing_workers_only_one_wins(relay_postgres):
    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)

    def acquire(worker_id):
        with sessions() as db:
            return claim_command(
                db, body, subject=subject, worker_id=worker_id, lease_seconds=30
            ).kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        kinds = list(pool.map(acquire, ("pg-a", "pg-b")))
    assert sorted(kinds) == ["claimed", "defer"]
    with sessions() as db:
        assert db.get(Run, run_id).fence == 1
        assert db.get(RunJob, job_id).fence == 1
        message_id = uuid.UUID(json.loads(body)["message_id"])
        disposition = (
            db.query(MessageDisposition)
            .filter(MessageDisposition.message_id == message_id)
            .one()
        )
        assert disposition.disposition in {"claimed", "deferred"}


def test_pg_independent_claims_share_transport_state_lock(relay_postgres):
    _url, sessions, _installation_id = relay_postgres
    run_id, _job_id, body, subject = _pg_command(sessions)
    with sessions() as state_reader:
        state_reader.query(MessageTransportState).with_for_update(read=True).one()
        with sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            decision = claim_command(
                db,
                body,
                subject=subject,
                worker_id="independent-worker",
                lease_seconds=30,
            )
        assert decision.kind == "claimed"
        assert decision.claim.run_id == run_id
        state_reader.rollback()


@pytest.mark.parametrize("entry_point", ["run", "delivery"])
@pytest.mark.parametrize(
    "active,execution_open,code",
    [
        (False, True, "transport_inactive"),
        (True, False, "execution_closed"),
        (False, False, "transport_inactive"),
    ],
)
def test_pg_closed_transport_refuses_exact_acquisition(
    relay_postgres, entry_point, active, execution_open, code
):
    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    with sessions() as db:
        # The initial observation is stale; only the shared locked read can
        # authorize execution after the concurrent gate writer commits.
        observed = db.get(MessageTransportState, 1)
        assert observed.active and observed.execution_open
        with sessions() as writer:
            state = writer.query(MessageTransportState).with_for_update().one()
            state.active, state.execution_open = active, execution_open
            writer.commit()

        if entry_point == "run":
            decision = claim_run(
                db,
                run_id=run_id,
                generation=1,
                epoch=observed.transport_epoch,
                worker_id="pg-worker",
                lease_seconds=30,
            )
        else:
            prepared = prepare_run_delivery(
                db, body, subject=subject, worker_id="pg-worker", lease_seconds=30
            )
            decision = prepared.decision
            assert prepared.disposition.action == "hold"
            assert prepared.disposition.code == code
            assert prepared.disposition.delay is None
        assert (decision.kind, decision.code, decision.claim) == ("defer", code, None)

    with sessions() as observer:
        run, job = observer.get(Run, run_id), observer.get(RunJob, job_id)
        assert (run.status, run.fence, job.fence) == ("queued", 0, 0)
        assert job.owner_id is None and job.lease_until is None
        disposition = observer.get(
            MessageDisposition, uuid.UUID(json.loads(body)["message_id"])
        )
        if entry_point == "delivery":
            assert (disposition.disposition, disposition.error_code) == (
                "deferred",
                code,
            )
        else:
            assert disposition is None


@pytest.mark.parametrize("prior_duplicate", [False, True])
@pytest.mark.parametrize(
    "gate,code",
    [("active", "transport_inactive"), ("execution_open", "execution_closed")],
)
def test_pg_gate_held_duplicate_preserves_original_owners_settlement(
    relay_postgres, gate, code, prior_duplicate
):
    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    with sessions() as db:
        original = prepare_run_delivery(
            db, body, subject=subject, worker_id="pg-worker", lease_seconds=30
        )
        assert original.decision.kind == "claimed"
        if prior_duplicate:
            duplicate = prepare_run_delivery(
                db, body, subject=subject, worker_id="pg-duplicate", lease_seconds=30
            )
            assert duplicate.disposition.action == "defer"

    with sessions() as operator:
        state = operator.query(MessageTransportState).with_for_update().one()
        setattr(state, gate, False)
        operator.commit()
    with sessions() as db:
        held = prepare_run_delivery(
            db, body, subject=subject, worker_id="pg-duplicate", lease_seconds=30
        )
        assert (held.disposition.action, held.disposition.code) == ("hold", code)
        assert held.decision.claim is None
        job = db.get(RunJob, job_id)
        assert (job.owner_id, job.fence) == ("pg-worker", 1)
    with sessions() as execution:
        assert finalize(
            execution, run_id, original.decision.claim.fence, {"status": "success"}
        )
    with sessions() as db:
        result = settle_run_delivery(db, original.command, original.decision.claim)
        assert (result.action, result.code) == ("ack", "run_terminal")


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("future", "defer"),
        ("closed", "defer"),
        ("terminal", "disposed"),
        ("obsolete", "disposed"),
        ("live", "defer"),
        ("expired", "reconcile_required"),
        ("identity", "disposed"),
    ],
)
def test_pg_command_nonclaims_leave_fence_unchanged(
    relay_postgres, condition, expected
):
    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    with sessions() as db:
        run = db.get(Run, run_id)
        job = db.get(RunJob, job_id)
        if condition == "future":
            job.available_at = datetime.now(UTC) + timedelta(minutes=5)
        elif condition == "closed":
            db.get(MessageTransportState, 1).execution_open = False
        elif condition == "terminal":
            run.status = "completed"
        elif condition == "obsolete":
            job.dispatch_generation = 2
        elif condition in {"live", "expired"}:
            run.status = "running"
            job.owner_id = "another"
            job.lease_until = datetime.now(UTC) + timedelta(
                minutes=5 if condition == "live" else -5
            )
        else:
            db.query(Outbox).filter(
                Outbox.run_id == run_id, Outbox.destination == "command"
            ).one().message_id = uuid.uuid4()
        db.commit()
    with sessions() as db:
        decision = claim_command(
            db, body, subject=subject, worker_id="pg-worker", lease_seconds=30
        )
        assert decision.kind == expected
        assert db.get(RunJob, job_id).fence == 0


@dataclass(repr=False)
class _Credentials:
    user: str
    password: str = field(repr=False)


@dataclass(repr=False)
class _Broker:
    js: object
    stream: str
    subject: str


@pytest.fixture(scope="module")
def relay_postgres(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail("TEST_POSTGRES_URL is required for the JetStream relay lane")

    base_url = make_url(service_url)
    admin = create_engine(base_url, pool_pre_ping=True)
    schema = f"relay_{uuid.uuid4().hex}"
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    query = dict(base_url.query)
    query["options"] = f"-csearch_path={schema}"
    schema_url = base_url.set(query=query)
    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("messaging-relay") / "alembic.ini"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(api_root / "alembic.ini")
    parser.set("alembic", "script_location", str(api_root / "alembic"))
    parser.set(
        "alembic",
        "sqlalchemy.url",
        schema_url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    with ini.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)

    built = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    engine = create_engine(schema_url, pool_size=8, max_overflow=4)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with sessions() as db:
            state = db.get(MessageTransportState, 1)
            assert state is not None
            configured_installation = os.environ.get("TEST_NATS_INSTALLATION_ID")
            if configured_installation is not None:
                state.installation_id = uuid.UUID(configured_installation)
                db.commit()
            installation_id = state.installation_id
        yield (
            schema_url.render_as_string(hide_password=False),
            sessions,
            installation_id,
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


@pytest_asyncio.fixture
async def relay_broker(relay_postgres):
    nats_url = os.environ.get("TEST_NATS_URL")
    auth_path_value = os.environ.get("TEST_NATS_AUTH_FILE")
    if not nats_url or not auth_path_value:
        pytest.fail(
            "TEST_NATS_URL and TEST_NATS_AUTH_FILE are required for relay tests"
        )
    auth_path = Path(auth_path_value)
    if auth_path.stat().st_mode & 0o077:
        pytest.fail("TEST_NATS_AUTH_FILE must have mode 0600")
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    credentials = _Credentials(**auth["admin"])
    client = await nats.connect(
        nats_url,
        user=credentials.user,
        password=credentials.password,
        name="openship-relay-integration-fixture",
        connect_timeout=2,
    )
    js = client.jetstream(timeout=2)
    _url, _sessions, installation_id = relay_postgres
    streams = tuple(
        config.name
        for config in stream_configs(installation_id)
        if config.name is not None
    )
    try:
        report = await ensure_topology(
            apply=True,
            js=js,
            installation_id=installation_id,
        )
        assert report.ready
        subject = f"openship.{installation_id.hex}.v1.event.>"
        broker = _Broker(js, "OS_EVENTS", subject)
        yield broker
    finally:
        for stream in streams:
            try:
                await js.delete_stream(stream)
            except NotFoundError:
                pass
        await client.close()


def _new_conversation(sessions):
    with sessions() as db:
        suffix = uuid.uuid4().hex
        user = User(
            username=f"relay_{suffix[:16]}",
            email=f"relay_{suffix}@example.test",
            password_hash="integration-test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Relay integration")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title="Relay integration",
            next_event_sequence=0,
        )
        db.add(conversation)
        db.commit()
        return conversation.id


def _append_events(sessions, conversation_id, *, count=1, prefix="relay"):
    with sessions() as db:
        events = [
            append_event(
                db,
                conversation_id,
                None,
                f"{prefix}.{sequence}",
                {"content": f"private-{prefix}-{sequence}"},
            )
            for sequence in range(count)
        ]
        ids = [event.id for event in events]
        db.commit()
        return ids


async def _thread_call_with_wakeup_poll(function, /, *args, **kwargs):
    """Keep the loop responsive in sandboxes that miss executor wakeups."""
    task = asyncio.create_task(_native_to_thread(function, *args, **kwargs))
    while not task.done():
        await asyncio.sleep(0.01)
    return await task


@pytest.fixture
def relay_threading(monkeypatch):
    if os.environ.get("OPENSHIP_TEST_RELAY_THREAD_POLL") == "1":
        monkeypatch.setattr(
            relay.asyncio,
            "to_thread",
            _thread_call_with_wakeup_poll,
        )


def _test_client_factory(nats_url, credentials):
    async def connect(*, name, reconnected_cb=None):
        return await nats.connect(
            nats_url,
            user=credentials.user,
            password=credentials.password,
            name=name,
            reconnected_cb=reconnected_cb,
            connect_timeout=2,
        )

    return connect


async def _wait_processed(sessions, event_ids, *, timeout=8):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with sessions() as db:
            processed = {
                row.event_id
                for row in db.query(Outbox)
                .filter(Outbox.event_id.in_(event_ids))
                .filter(Outbox.processed_at.is_not(None))
                .all()
            }
        if set(event_ids) <= processed:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("relay did not confirm all expected outbox rows")


@pytest.mark.asyncio
async def test_startup_catchup_notifications_and_conversation_order(
    relay_postgres, relay_broker, relay_threading, monkeypatch
):
    database_url, sessions, _installation_id = relay_postgres
    conversation_id = _new_conversation(sessions)
    first_event = _append_events(sessions, conversation_id, prefix="before-listen")[0]

    nats_url = os.environ["TEST_NATS_URL"]
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["admin"])
    stop = asyncio.Event()
    listener_ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    subscription = await relay_broker.js.pull_subscribe(
        relay_broker.subject,
        durable=f"relay_{uuid.uuid4().hex[:20]}",
        stream=relay_broker.stream,
        config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, max_ack_pending=4),
    )
    original_connect_listener = relay._connect_listener

    def track_listener(dsn):
        result = original_connect_listener(dsn)
        loop.call_soon_threadsafe(listener_ready.set)
        return result

    monkeypatch.setattr(relay, "_connect_listener", track_listener)
    task = asyncio.create_task(
        relay.run_relay(
            stop,
            session_factory=sessions,
            database_url=database_url,
            nats_client_factory=_test_client_factory(nats_url, credentials),
        )
    )
    probe = None
    try:
        await asyncio.wait_for(listener_ready.wait(), timeout=5)
        await _wait_processed(sessions, [first_event])

        # A second listener observes PostgreSQL coalescing identical notifications
        # from one transaction while the relay receives the same wakeup.
        import psycopg2

        probe = psycopg2.connect(relay._postgres_dsn(database_url), connect_timeout=3)
        probe.autocommit = True
        with probe.cursor() as cursor:
            cursor.execute("LISTEN openship_outbox")
        next_events = _append_events(
            sessions,
            conversation_id,
            count=2,
            prefix="coalesced",
        )
        readable, _, _ = select.select([probe.fileno()], [], [], 3)
        assert readable
        probe.poll()
        assert len(probe.notifies) == 1
        probe.notifies.clear()

        await _wait_processed(sessions, next_events)
        messages = await subscription.fetch(batch=3, timeout=3)
        envelopes = [json.loads(item.data) for item in messages]
        assert [item["sequence"] for item in envelopes] == [0, 1, 2]
        assert all("private-" not in item.data.decode() for item in messages)
        for message in messages:
            await message.ack_sync(timeout=1)

        # Idle waiting does not repeatedly query the outbox for an empty batch.
        calls = 0
        original_due = relay.next_outbox_due_at

        def count_due(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original_due(*args, **kwargs)

        monkeypatch.setattr(relay, "next_outbox_due_at", count_due)
        await asyncio.sleep(0.2)
        idle_query_count = calls
        await asyncio.sleep(1.2)
        assert calls == idle_query_count
    finally:
        if probe is not None:
            probe.close()
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_commit_during_listener_reconnect_is_caught_up(
    relay_postgres, relay_broker, relay_threading, monkeypatch
):
    database_url, sessions, _installation_id = relay_postgres
    conversation_id = _new_conversation(sessions)
    before_reconnect = _append_events(
        sessions, conversation_id, prefix="before-reconnect"
    )[0]
    nats_url = os.environ["TEST_NATS_URL"]
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["admin"])
    disconnected = asyncio.Event()
    listener_ready = asyncio.Event()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_connect_listener = relay._connect_listener
    calls = 0

    def fail_once_then_connect(dsn):
        nonlocal calls
        calls += 1
        if calls == 1:
            loop.call_soon_threadsafe(disconnected.set)
            import psycopg2

            raise psycopg2.OperationalError("simulated listener connection loss")
        result = original_connect_listener(dsn)
        loop.call_soon_threadsafe(listener_ready.set)
        return result

    monkeypatch.setattr(relay, "_connect_listener", fail_once_then_connect)
    task = asyncio.create_task(
        relay.run_relay(
            stop,
            session_factory=sessions,
            database_url=database_url,
            nats_client_factory=_test_client_factory(nats_url, credentials),
        )
    )
    try:
        await asyncio.wait_for(disconnected.wait(), timeout=5)
        during_reconnect = _append_events(
            sessions,
            conversation_id,
            prefix="during-reconnect",
        )[0]
        await asyncio.wait_for(listener_ready.wait(), timeout=5)
        await _wait_processed(sessions, [before_reconnect, during_reconnect])
        assert (
            await relay_broker.js.stream_info(relay_broker.stream)
        ).state.messages == 2
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_nats_reconnect_wakeup_catches_up_without_a_database_notification(
    relay_postgres, relay_broker, relay_threading, monkeypatch
):
    database_url, sessions, _installation_id = relay_postgres
    conversation_id = _new_conversation(sessions)
    nats_url = os.environ["TEST_NATS_URL"]
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["admin"])
    loop = asyncio.get_running_loop()
    listener_ready = asyncio.Event()
    client_ready = asyncio.Event()
    client_reconnected = asyncio.Event()
    clients = []
    stop = asyncio.Event()
    original_connect_listener = relay._connect_listener

    def track_listener(dsn):
        result = original_connect_listener(dsn)
        loop.call_soon_threadsafe(listener_ready.set)
        return result

    async def connect_relay(*, name, reconnected_cb=None):
        async def on_reconnected():
            if reconnected_cb is not None:
                await reconnected_cb()
            client_reconnected.set()

        client = await nats.connect(
            nats_url,
            user=credentials.user,
            password=credentials.password,
            name=name,
            reconnected_cb=on_reconnected,
            connect_timeout=2,
        )
        clients.append(client)
        client_ready.set()
        return client

    def ignore_database_wakeups(_connection, timeout):
        time.sleep(min(timeout, 1.0))
        return False

    monkeypatch.setattr(relay, "_connect_listener", track_listener)
    monkeypatch.setattr(relay, "_wait_for_listener", ignore_database_wakeups)
    task = asyncio.create_task(
        relay.run_relay(
            stop,
            session_factory=sessions,
            database_url=database_url,
            nats_client_factory=connect_relay,
        )
    )
    try:
        await asyncio.wait_for(listener_ready.wait(), timeout=5)
        await asyncio.wait_for(client_ready.wait(), timeout=5)
        await asyncio.sleep(0.2)
        event_id = _append_events(
            sessions,
            conversation_id,
            prefix="during-nats-reconnect",
        )[0]
        # Closing this live client transport makes nats-py observe EOF, run
        # its reconnect loop, and invoke the relay's registered callback.
        clients[0]._transport.close()
        await asyncio.wait_for(client_reconnected.wait(), timeout=5)
        assert clients[0].stats["reconnects"] >= 1
        await _wait_processed(sessions, [event_id])
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_runtime_refuses_to_publish_with_drifted_topology(
    relay_postgres, relay_broker, relay_threading
):
    database_url, sessions, installation_id = relay_postgres
    nats_url = os.environ["TEST_NATS_URL"]
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["admin"])
    expected = stream_configs(installation_id)[2]
    await relay_broker.js.update_stream(
        config=replace(
            expected,
            subjects=[f"openship.{installation_id.hex}.v1.event.wrong"],
        )
    )

    with pytest.raises(TopologyCompatibilityError):
        await relay.run_relay(
            asyncio.Event(),
            session_factory=sessions,
            database_url=database_url,
            nats_client_factory=_test_client_factory(nats_url, credentials),
        )


@pytest.mark.asyncio
async def test_relay_role_can_check_and_publish_but_cannot_create_streams(
    relay_postgres, relay_broker
):
    _database_url, sessions, installation_id = relay_postgres
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["relay"])
    permission_errors = []

    async def on_error(error):
        permission_errors.append(str(error))

    client = await nats.connect(
        os.environ["TEST_NATS_URL"],
        user=credentials.user,
        password=credentials.password,
        error_cb=on_error,
        name="openship-relay-role-integration-test",
        connect_timeout=2,
    )
    js = client.jetstream(timeout=1)
    denied_stream = f"RELAY_DENIED_{uuid.uuid4().hex[:12].upper()}"
    try:
        report = await ensure_topology(
            apply=False,
            js=js,
            installation_id=installation_id,
        )
        assert report.ready

        conversation_id = _new_conversation(sessions)
        _append_events(sessions, conversation_id, prefix="relay-role")
        (record,) = claim_outbox_batch(sessions, conversation_id=conversation_id)
        receipt = await publish_record(js, record)
        assert receipt.stream == "OS_EVENTS"

        with pytest.raises(NatsTimeoutError):
            await js.add_stream(
                config=StreamConfig(
                    name=denied_stream,
                    subjects=[f"openship.denied.{uuid.uuid4().hex}.>"],
                    storage=StorageType.FILE,
                )
            )
        assert any(
            "permissions violation" in error.lower() for error in permission_errors
        )
    finally:
        try:
            await relay_broker.js.delete_stream(denied_stream)
        except NotFoundError:
            pass
        await client.close()


class _LostPubAck:
    def __init__(self, js):
        self.js = js
        self.calls = 0

    async def publish(self, subject, body, *, headers):
        ack = await self.js.publish(subject, body, headers=headers)
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("lost JetStream PubAck")
        return ack


@pytest.mark.asyncio
async def test_lost_puback_retries_stable_identity_without_duplicate_storage(
    relay_postgres, relay_broker, relay_threading
):
    _url, sessions, _installation_id = relay_postgres
    conversation_id = _new_conversation(sessions)
    event_id = _append_events(sessions, conversation_id, prefix="lost-ack")[0]
    publisher = _LostPubAck(relay_broker.js)

    first = await publish_pending_batch(
        publisher, sessions, conversation_id=conversation_id
    )
    assert first.claimed == 1 and first.published == 0
    with sessions() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        message_id = row.message_id
        assert row.processed_at is None
        assert row.last_error == "TimeoutError"
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    second = await publish_pending_batch(
        publisher, sessions, conversation_id=conversation_id
    )
    assert second.claimed == 1 and second.published == 1
    assert (await relay_broker.js.stream_info(relay_broker.stream)).state.messages == 1
    with sessions() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        assert row.processed_at is not None
        assert row.message_id == message_id


@pytest.mark.asyncio
async def test_crash_after_puback_before_processed_flag_reclaims_same_identity(
    relay_postgres, relay_broker
):
    _url, sessions, _installation_id = relay_postgres
    conversation_id = _new_conversation(sessions)
    event_id = _append_events(sessions, conversation_id, prefix="crash-after-ack")[0]
    now = datetime.now(UTC)
    (claimed,) = claim_outbox_batch(
        sessions,
        now=now,
        lease_seconds=5,
        conversation_id=conversation_id,
    )
    first_ack = await publish_record(relay_broker.js, claimed)
    assert first_ack.duplicate is False

    with sessions() as db:
        row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
        assert row.processed_at is None
        assert row.publisher_lease_until is not None

    (reclaimed,) = claim_outbox_batch(
        sessions,
        now=now + timedelta(seconds=6),
        lease_seconds=5,
        conversation_id=conversation_id,
    )
    duplicate_ack = await publish_record(relay_broker.js, reclaimed)
    assert duplicate_ack.duplicate is True
    assert duplicate_ack.message_id == claimed.message_id
    assert _settle_publication(sessions, reclaimed, error=None)
    assert (await relay_broker.js.stream_info(relay_broker.stream)).state.messages == 1


@pytest.mark.asyncio
async def test_broker_full_keeps_outbox_pending_and_schedules_retry(
    relay_postgres, relay_threading
):
    _url, sessions, installation_id = relay_postgres
    nats_url = os.environ["TEST_NATS_URL"]
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())
    credentials = _Credentials(**auth["admin"])
    client = await nats.connect(
        nats_url,
        user=credentials.user,
        password=credentials.password,
        name="openship-relay-full-stream-test",
        connect_timeout=2,
    )
    js = client.jetstream(timeout=2)
    stream = f"OS_RELAY_FULL_{uuid.uuid4().hex[:12].upper()}"
    subject_pattern = f"openship.{installation_id.hex}.v1.>"
    try:
        await js.add_stream(
            config=StreamConfig(
                name=stream,
                subjects=[subject_pattern],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                discard=DiscardPolicy.NEW,
                max_bytes=1,
                max_age=300.0,
                duplicate_window=120.0,
                max_msg_size=16 * 1024,
            )
        )
        conversation_id = _new_conversation(sessions)
        event_id = _append_events(sessions, conversation_id, prefix="full-stream")[0]
        result = await publish_pending_batch(
            js, sessions, conversation_id=conversation_id
        )
        assert result.claimed == 1 and result.published == 0
        with sessions() as db:
            row = db.query(Outbox).filter(Outbox.event_id == event_id).one()
            assert row.processed_at is None
            assert row.last_error is not None
            assert row.next_attempt_at > datetime.now(UTC)
    finally:
        try:
            await js.delete_stream(stream)
        except NotFoundError:
            pass
        await client.close()


async def _wait_condition(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def _runtime_client(*, reconnected_cb=None, allow_reconnect=True, error_cb=None):
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())["runtime"]
    return await nats.connect(
        os.environ["TEST_NATS_URL"],
        user=auth["user"],
        password=auth["password"],
        inbox_prefix=b"_INBOX.runtime",
        connect_timeout=2,
        reconnect_time_wait=0.05,
        reconnected_cb=reconnected_cb,
        allow_reconnect=allow_reconnect,
        error_cb=error_cb,
    )


async def _api_client(**connection_options):
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())["api"]
    return await nats.connect(
        os.environ["TEST_NATS_URL"],
        user=auth["user"],
        password=auth["password"],
        connect_timeout=2,
        **connection_options,
    )


@pytest.mark.asyncio
async def test_api_event_fanouts_replay_and_independently_receive_live_events(
    relay_postgres, relay_broker, monkeypatch
):
    from src.openship.events.fanout import EventFanout

    _url, sessions, installation_id = relay_postgres
    monkeypatch.setattr(
        "src.openship.events.fanout.settings.nats_auth_file",
        Path(os.environ["TEST_NATS_AUTH_FILE"]),
    )
    conversation_id = _new_conversation(sessions)
    with sessions() as db:
        conversation = db.get(Conversation, conversation_id)
        user_id = conversation.user_id

    # This notice is already in JetStream when the process consumers start.
    replay_event_id = _append_events(
        sessions, conversation_id, prefix="api-event-replay"
    )[0]
    replay_result = await publish_pending_batch(
        relay_broker.js, sessions, conversation_id=conversation_id
    )
    assert replay_result.published == 1

    fanouts = [
        EventFanout(
            session_factory=sessions,
            installation_id=installation_id,
            nats_client_factory=_api_client,
        )
        for _ in range(2)
    ]
    streams = []
    next_events = []
    try:
        for fanout in fanouts:
            await fanout.start()
        await _wait_condition(lambda: all(fanout.ready for fanout in fanouts))

        from src.openship.events.service import AuthorizedScope

        scope = AuthorizedScope(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        streams = [
            fanout.subscribe_conversation(scope, after_sequence=-1)
            for fanout in fanouts
        ]
        replayed = await asyncio.gather(*(anext(stream) for stream in streams))
        assert [event.id for event in replayed] == [str(replay_event_id)] * 2
        assert [event.sequence for event in replayed] == [0, 0]

        next_events = [asyncio.create_task(anext(stream)) for stream in streams]
        await _wait_condition(
            lambda: all(conversation_id in fanout._clients for fanout in fanouts)
        )
        live_event_ids = _append_events(
            sessions,
            conversation_id,
            count=2,
            prefix="api-event-live",
        )
        live_result = await publish_pending_batch(
            relay_broker.js, sessions, conversation_id=conversation_id
        )
        assert live_result.published == 2

        for expected_sequence in (1, 2):
            live = await asyncio.gather(
                *(asyncio.wait_for(task, timeout=3) for task in next_events)
            )
            assert [event.sequence for event in live] == [expected_sequence] * 2
            assert [event.id for event in live] == [
                str(live_event_ids[expected_sequence - 1])
            ] * 2
            if expected_sequence == 1:
                next_events = [asyncio.create_task(anext(stream)) for stream in streams]
    finally:
        for task in next_events:
            if not task.done():
                task.cancel()
        await asyncio.gather(*next_events, return_exceptions=True)
        for stream in streams:
            await stream.aclose()
        await asyncio.gather(*(fanout.close() for fanout in fanouts))


@pytest.mark.asyncio
async def test_api_worker_health_rpc_is_bound_and_installation_scoped(
    relay_postgres, relay_broker
):
    _url, _sessions, installation_id = relay_postgres
    runtime = await _runtime_client()
    api = await _api_client()

    async def ready():
        return True

    worker_subscription = await subscribe_worker_health(
        runtime,
        installation_id=installation_id,
        is_ready=ready,
    )
    try:
        result = await check_worker_health(
            api,
            installation_id=installation_id,
            api_instance_id=uuid.uuid4(),
        )
        assert result.ready

        other_installation = uuid.uuid4()
        permission_errors = []

        async def on_error(error):
            permission_errors.append(str(error))

        denied = await _api_client(error_cb=on_error)
        flush_errors = []
        try:
            await denied.publish(
                health_request_subject(other_installation),
                b"{}",
                reply=health_reply_subject(
                    other_installation, uuid.uuid4(), uuid.uuid4()
                ),
            )
            try:
                await denied.flush(timeout=1)
            except NatsError as error:
                flush_errors.append(error)
            await _wait_condition(lambda: permission_errors or flush_errors)
            assert any(
                "permissions violation" in error.lower() for error in permission_errors
            ) or any(isinstance(error, AuthorizationError) for error in flush_errors)
        finally:
            await denied.close()

        runtime_errors = []

        async def on_runtime_error(error):
            runtime_errors.append(str(error))

        denied_runtime = await _runtime_client(error_cb=on_runtime_error)
        runtime_flush_errors = []

        async def ignore_health_request(_message):
            return None

        try:
            await denied_runtime.subscribe(
                health_request_subject(other_installation),
                cb=ignore_health_request,
                pending_msgs_limit=1,
                pending_bytes_limit=16 * 1024,
            )
            try:
                await denied_runtime.flush(timeout=1)
            except NatsError as error:
                runtime_flush_errors.append(error)
            await _wait_condition(lambda: runtime_errors or runtime_flush_errors)
            assert any(
                "permissions violation" in error.lower() for error in runtime_errors
            ) or any(
                isinstance(error, AuthorizationError) for error in runtime_flush_errors
            )
        finally:
            await denied_runtime.close()

        denied_runtime_reply_errors = []

        async def on_runtime_reply_error(error):
            denied_runtime_reply_errors.append(str(error))

        denied_runtime_reply = await _runtime_client(error_cb=on_runtime_reply_error)
        runtime_reply_flush_errors = []
        try:
            await denied_runtime_reply.publish(
                health_reply_subject(other_installation, uuid.uuid4(), uuid.uuid4()),
                b"{}",
            )
            try:
                await denied_runtime_reply.flush(timeout=1)
            except NatsError as error:
                runtime_reply_flush_errors.append(error)
            await _wait_condition(
                lambda: denied_runtime_reply_errors or runtime_reply_flush_errors
            )
            assert any(
                "permissions violation" in error.lower()
                for error in denied_runtime_reply_errors
            ) or any(
                isinstance(error, AuthorizationError)
                for error in runtime_reply_flush_errors
            )
        finally:
            await denied_runtime_reply.close()

        denied_api_reply_errors = []

        async def on_api_reply_error(error):
            denied_api_reply_errors.append(str(error))

        denied_api_reply = await _api_client(error_cb=on_api_reply_error)
        api_reply_flush_errors = []

        async def ignore_health_reply(_message):
            return None

        try:
            await denied_api_reply.subscribe(
                health_reply_subject(other_installation, uuid.uuid4(), uuid.uuid4()),
                cb=ignore_health_reply,
                pending_msgs_limit=1,
                pending_bytes_limit=16 * 1024,
            )
            try:
                await denied_api_reply.flush(timeout=1)
            except NatsError as error:
                api_reply_flush_errors.append(error)
            await _wait_condition(
                lambda: denied_api_reply_errors or api_reply_flush_errors
            )
            assert any(
                "permissions violation" in error.lower()
                for error in denied_api_reply_errors
            ) or any(
                isinstance(error, AuthorizationError)
                for error in api_reply_flush_errors
            )
        finally:
            await denied_api_reply.close()
    finally:
        await worker_subscription.unsubscribe()
        await runtime.close()
        await api.close()


@pytest.mark.asyncio
async def test_runtime_control_subscription_is_limited_to_installation_scope(
    relay_postgres, relay_broker
):
    _url, _sessions, installation_id = relay_postgres
    run_id = uuid.uuid4()
    subject = f"openship.{installation_id.hex}.v1.control.{run_id.hex}"
    delivered = asyncio.Event()
    subjects = []

    async def on_control(message):
        subjects.append(message.subject)
        delivered.set()

    runtime = await _runtime_client()
    subscription = await runtime.subscribe(
        subject,
        cb=on_control,
        pending_msgs_limit=1,
        pending_bytes_limit=16 * 1024,
    )
    try:
        await runtime.flush(timeout=1)
        await relay_broker.js.publish(subject, b"cancel-wake")
        await asyncio.wait_for(delivered.wait(), 2)
        assert subjects == [subject]
    finally:
        await subscription.unsubscribe()
        await runtime.close()

    permission_errors = []

    async def on_error(error):
        permission_errors.append(str(error))

    other_install = uuid.uuid4().hex
    denied = await _runtime_client(error_cb=on_error)
    flush_errors = []
    try:
        await denied.subscribe(
            f"openship.{other_install}.v1.control.{run_id.hex}",
            cb=on_control,
            pending_msgs_limit=1,
            pending_bytes_limit=16 * 1024,
        )
        try:
            await denied.flush(timeout=1)
        except NatsError as error:
            flush_errors.append(error)
        await _wait_condition(lambda: permission_errors or flush_errors, timeout=2)
        assert any(
            "permissions violation" in item.lower() for item in permission_errors
        ) or any(isinstance(error, AuthorizationError) for error in flush_errors)
    finally:
        await denied.close()


@pytest.mark.asyncio
async def test_committed_cancel_outbox_wakes_only_its_current_owner(
    relay_postgres, relay_broker
):
    from src.openship.messaging.consumer import RunConsumer
    from src.openship.runs.service import request_cancel
    from src.openship.runs.worker import Worker

    _url, sessions, _installation_id = relay_postgres
    run_id, _job_id, body, subject = _pg_command(sessions)
    with sessions() as db:
        prepared = prepare_run_delivery(
            db,
            body,
            subject=subject,
            worker_id="control-owner",
            lease_seconds=30,
        )
        assert prepared.decision.kind == "claimed"
        run = db.get(Run, run_id)
        conversation = db.get(Conversation, run.conversation_id)
        user_id = conversation.user_id

    client = await _runtime_client()
    consumer = RunConsumer(client, concurrency=1, session_factory=sessions)
    woken = asyncio.Event()
    signal = type("Signal", (), {"wake": lambda _self: woken.set()})()
    worker = Worker(
        worker_id=prepared.decision.claim.worker_id,
        session_factory=sessions,
        checkpointer=None,
    )
    subscription = None
    try:
        subscription = await consumer._subscribe_control(prepared, signal)
        with sessions() as db:
            cancelled = request_cancel(db, user_id, str(run_id))
            assert cancelled.status == "cancelled"
        result = await publish_pending_batch(
            relay_broker.js,
            sessions,
            limit=16,
            conversation_id=prepared.command.conversation_id,
        )
        assert result.published >= 1
        await asyncio.wait_for(woken.wait(), 2)
        with sessions() as db:
            control = (
                db.query(Outbox).filter_by(destination="control", run_id=run_id).one()
            )
            assert control.processed_at is not None
            assert worker._ownership_lost(db, run_id, prepared.decision.claim.fence)
    finally:
        if subscription is not None:
            await subscription.unsubscribe()
        worker.close()
        await client.close()


def _consumer_worker_factory(sessions, effects, *, block=None):
    import threading

    class SyntheticWorker:
        Session = sessions
        worker_id = "pg-consumer"

        def __init__(self):
            self.thread = threading.get_ident()
            self.closed = False

        def _ownership_lost(self, db, run_id, fence):
            run = db.query(Run).filter_by(id=run_id).populate_existing().one_or_none()
            job = (
                db.query(RunJob)
                .filter_by(run_id=run_id)
                .populate_existing()
                .one_or_none()
            )
            return (
                run is None
                or run.status != "running"
                or run.fence != fence
                or job is None
                or job.fence != fence
                or job.owner_id != self.worker_id
            )

        def process_job(self, claim, *, supervision=None):
            assert threading.get_ident() == self.thread
            assert supervision is not None
            effects.append(claim.run_id)
            if block is not None:
                assert block.wait(10)
            with self.Session() as db:
                assert finalize(db, claim.run_id, claim.fence, {"status": "success"})
            # Deliberately false: committed state alone must authorize ACK.
            return False

        def close(self):
            assert threading.get_ident() == self.thread
            self.closed = True

    return SyntheticWorker


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_run_consumer_committed_ack_and_reconnect_redelivery_without_second_effect(
    relay_postgres, relay_broker, monkeypatch, lost_ack
):
    from nats.aio.msg import Msg

    from src.openship.messaging.consumer import RunConsumer

    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    await relay_broker.js.publish(subject, body)
    stop, reconnected = asyncio.Event(), asyncio.Event()

    async def on_reconnected():
        reconnected.set()

    client = await _runtime_client(reconnected_cb=on_reconnected)
    effects, acknowledgments = [], []
    original_ack = Msg.ack_sync

    async def checked_ack(message, *, timeout):
        # Verify committed state in a different connection before the broker
        # confirmation. Lost first ACK sends nothing; real AckWait redelivers.
        with sessions() as observer:
            row = observer.get(
                MessageDisposition, uuid.UUID(json.loads(body)["message_id"])
            )
            assert row.disposition == "completed"
            assert observer.get(Run, run_id).status == "completed"
        acknowledgments.append(message.metadata.num_delivered)
        if lost_ack and len(acknowledgments) == 1:
            await client.force_reconnect()
            raise NatsTimeoutError
        result = await original_ack(message, timeout=timeout)
        stop.set()
        return result

    monkeypatch.setattr(Msg, "ack_sync", checked_ack)
    consumer = RunConsumer(
        client,
        concurrency=1,
        session_factory=sessions,
        worker_factory=_consumer_worker_factory(sessions, effects),
        fetch_timeout=0.2,
        retry_interval=0.02,
        progress_interval=0.05,
    )
    task = asyncio.create_task(consumer.run(stop))
    try:
        await asyncio.wait_for(task, 40 if lost_ack else 8)
        assert effects == [run_id]
        assert acknowledgments == ([1, 2] if lost_ack else [1])
        if lost_ack:
            assert reconnected.is_set() and client.stats["reconnects"] >= 1
            assert consumer._checked_reconnect == client.stats["reconnects"]
        with sessions() as db:
            assert db.get(RunJob, job_id).fence == 1
        info = await relay_broker.js.consumer_info("OS_RUNS", "run-v1")
        assert info.num_ack_pending == 0
        assert (await relay_broker.js.stream_info("OS_RUNS")).state.messages == 0
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)
        await client.close()


@pytest.mark.asyncio
async def test_run_consumer_broker_transport_loss_keeps_committed_result_and_original_recoverable(
    relay_postgres, relay_broker, monkeypatch
):
    import threading

    from nats.aio.msg import Msg

    from src.openship.messaging.consumer import RunConsumer

    _url, sessions, _installation_id = relay_postgres
    run_id, job_id, body, subject = _pg_command(sessions)
    await relay_broker.js.publish(subject, body)
    stop, release = asyncio.Event(), threading.Event()
    client = await _runtime_client(allow_reconnect=False)
    effects, ack_failures = [], []
    original_ack = Msg.ack_sync

    async def disconnected_ack(message, *, timeout):
        try:
            return await original_ack(message, timeout=timeout)
        except Exception as error:
            ack_failures.append(type(error).__name__)
            stop.set()
            raise

    monkeypatch.setattr(Msg, "ack_sync", disconnected_ack)
    consumer = RunConsumer(
        client,
        concurrency=1,
        session_factory=sessions,
        worker_factory=_consumer_worker_factory(sessions, effects, block=release),
        fetch_timeout=0.2,
        retry_interval=0.02,
        progress_interval=0.02,
    )
    task = asyncio.create_task(consumer.run(stop))
    try:
        await _wait_condition(lambda: effects == [run_id])
        # Lose the complete worker-to-broker transport while synchronous work
        # holds its DB claim. No transport callback is permitted to redo it.
        await client.close()
        release.set()
        await asyncio.wait_for(task, 8)
        assert ack_failures and effects == [run_id]
        with sessions() as db:
            assert db.get(Run, run_id).status == "completed"
            assert db.get(RunJob, job_id).fence == 1
            row = db.get(MessageDisposition, uuid.UUID(json.loads(body)["message_id"]))
            assert row.disposition == "completed"
            replay = prepare_run_delivery(
                db, body, subject=subject, worker_id="recovery", lease_seconds=30
            )
            assert replay.disposition.action == "ack" and replay.decision.claim is None
        info = await relay_broker.js.consumer_info("OS_RUNS", "run-v1")
        assert info.num_ack_pending == 1
        assert (await relay_broker.js.stream_info("OS_RUNS")).state.messages == 1
    finally:
        release.set()
        stop.set()
        await asyncio.wait_for(task, 5)
        await client.close()


def test_worker_checkpointer_setup_serializes_independent_engines(
    relay_postgres, monkeypatch
):
    import threading

    from langgraph.checkpoint.postgres import PostgresSaver

    from src.openship.config import settings
    from src.openship.runs.worker import Worker

    database_url, sessions, _installation_id = relay_postgres
    monkeypatch.setattr(settings, "database_url", database_url)
    entered, release, raced = threading.Event(), threading.Event(), threading.Event()
    original_setup = PostgresSaver.setup
    connections = []
    lock = threading.Lock()

    def guarded_setup(saver):
        with lock:
            connections.append(saver.conn)
            first = len(connections) == 1
        if first:
            entered.set()
            assert release.wait(8)
        else:
            raced.set()
        original_setup(saver)

    monkeypatch.setattr(PostgresSaver, "setup", guarded_setup)

    def build_worker():
        worker = Worker(session_factory=sessions)
        try:
            return worker.checkpointer is not None
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as threads:
        first = threads.submit(build_worker)
        assert entered.wait(5)
        second = threads.submit(build_worker)
        try:
            deadline = time.monotonic() + 5
            waiting = False
            while not raced.is_set() and not waiting and time.monotonic() < deadline:
                with sessions() as db:
                    waiting = bool(
                        db.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE application_name = 'openship-checkpointer-setup' "
                                "AND query LIKE '%pg_try_advisory_lock%' "
                                "AND xact_start IS NULL"
                            )
                        ).scalar_one()
                        == 2
                    )
                if not waiting:
                    raced.wait(0.01)
            assert not raced.is_set(), (
                "both independent savers entered setup concurrently"
            )
            assert waiting, "second worker never attempted the database advisory lock"
        finally:
            release.set()
        assert first.result(timeout=8) and second.result(timeout=8)
        assert len(connections) == 2 and connections[0] is not connections[1]


def test_worker_checkpointer_setup_lock_spans_processes(relay_postgres, tmp_path):
    database_url, sessions, _installation_id = relay_postgres
    release = tmp_path / "release"
    markers = [tmp_path / "first-setup", tmp_path / "second-setup"]
    program = """
import sys
import time
from pathlib import Path
from langgraph.checkpoint.postgres import PostgresSaver
from src.openship.runs.worker import Worker

marker, release = map(Path, sys.argv[1:])
setup = PostgresSaver.setup

def tracked_setup(saver):
    marker.touch()
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("test setup release timed out")
        time.sleep(.01)
    setup(saver)

PostgresSaver.setup = tracked_setup
worker = Worker()
try:
    assert worker.checkpointer is not None
finally:
    worker.close()
"""
    processes = []
    environment = {**os.environ, "DATABASE_URL": database_url}
    try:
        for index, marker in enumerate(markers):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", program, str(marker), str(release)],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
            deadline = time.monotonic() + 8
            if index == 0:
                while not marker.exists() and time.monotonic() < deadline:
                    assert processes[0].poll() is None, (
                        "first worker exited before setup"
                    )
                    time.sleep(0.01)
                assert marker.exists()
            else:
                waiting = False
                while (
                    not marker.exists() and not waiting and time.monotonic() < deadline
                ):
                    assert processes[1].poll() is None, (
                        "second worker exited before lock"
                    )
                    with sessions() as db:
                        waiting = bool(
                            db.execute(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE application_name = 'openship-checkpointer-setup' "
                                    "AND query LIKE '%pg_try_advisory_lock%' "
                                    "AND xact_start IS NULL"
                                )
                            ).scalar_one()
                            == 2
                        )
                    if not waiting:
                        time.sleep(0.01)
                assert waiting and not marker.exists(), (
                    "second process entered setup before the first released its lock"
                )
        release.touch()
        for process in processes:
            _stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
        assert all(marker.exists() for marker in markers)
    finally:
        release.touch()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
