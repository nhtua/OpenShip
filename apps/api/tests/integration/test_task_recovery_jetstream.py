"""Broker recovery for a task's explicit choice: a lost ACK never re-executes.

The v5 task lane runs against PostgreSQL and the disposable JetStream broker,
so both paths a committed recovery choice takes are real: the outbox command
that carries the choice to a worker, and the worker that executes it once. The
breaks this catches are a broker that acknowledges a command the database never
marked processed (a retry must not fund a second effect), and the same command
delivered twice (which must yield exactly one new attempt). Command identity is
the pinned message id, its dispatch generation and the transport epoch - never
a wall clock.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import nats
import pytest
import pytest_asyncio
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import NotFoundError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.messaging import relay
from src.openship.messaging.command import claim_command
from src.openship.messaging.models import (
    MessageDisposition,
    MessageTransportState,
)
from src.openship.messaging.relay import _settle_publication, publish_record
from src.openship.messaging.topology import ensure_topology, stream_configs
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.tasks.models import TaskDecisionRecord
from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.test_task_graph import Lane, _call, _calls, _durable, _journey

pytestmark = [pytest.mark.postgres, pytest.mark.jetstream, pytest.mark.asyncio]

LANE_DATABASE = "phase7a_task_broker_lane"

REPEAT_BEHAVIORS = [
    *_journey()[:3],
    _call(
        "provider-repeat",
        "download_file",
        {"url": "https://observations.test/day.csv"},
    ),
    _journey()[3],
]


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
def task_recovery_database(tmp_path_factory):
    """One disposable PostgreSQL database at the current schema head."""
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail(
            "TEST_POSTGRES_URL is required for the task broker recovery lane",
            pytrace=False,
        )

    lane_url = (
        make_url(bounded_postgres_url(service_url))
        .set(database=LANE_DATABASE)
        .render_as_string(hide_password=False)
    )
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{LANE_DATABASE}"'))
    finally:
        admin.dispose()

    ini = _alembic_ini(tmp_path_factory.mktemp("phase7a-task-broker"), lane_url)
    built = _run_alembic(ini, "upgrade", "head")
    assert built.returncode == 0, built.stderr
    engine = create_engine(lane_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        state = db.get(MessageTransportState, 1)
        assert state is not None
        configured = os.environ.get("TEST_NATS_INSTALLATION_ID")
        if configured is not None:
            state.installation_id = _uuid.UUID(configured)
            db.commit()
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
        finally:
            admin.dispose()


@pytest_asyncio.fixture
async def task_broker(task_recovery_database):
    """The disposable authenticated broker, with this installation's topology.

    The client is per test on purpose: its request subscription lives on the
    test's own event loop, so a client built in an outer fixture would only
    time out once the test runs.
    """
    nats_url = os.environ.get("TEST_NATS_URL")
    auth_path_value = os.environ.get("TEST_NATS_AUTH_FILE")
    if not nats_url or not auth_path_value:
        pytest.fail(
            "TEST_NATS_URL and TEST_NATS_AUTH_FILE are required for the task "
            "broker recovery lane; no localhost or credential fallback"
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
        name="openship-task-recovery-integration-fixture",
        connect_timeout=2,
    )
    js = client.jetstream(timeout=2)
    with task_recovery_database() as db:
        installation_id = db.get(MessageTransportState, 1).installation_id
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
        yield _Broker(
            js=js,
            stream="OS_RUNS",
            subject=f"openship.{installation_id.hex}.v1.command.run.execute",
        )
    finally:
        for stream in streams:
            try:
                await js.delete_stream(stream)
            except NotFoundError:
                pass
        await client.close()


@pytest.fixture
def _recovered_choice(monkeypatch, task_recovery_database):
    """A v5 root whose admitted call died, parked, and got one explicit repeat."""
    sessions = task_recovery_database
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[*REPEAT_BEHAVIORS],
        crash=True,
        sessions=sessions,
    )
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    before = _durable(lane.run_id, sessions)
    assert before["attempts"] == []
    assert len(before["operations"]) == 1
    dead_operation = before["operations"][0]
    assert dead_operation.status == "admitted"
    assert dead_operation.logical_call_id is not None

    lane.rehold()
    parked = lane.invoke()
    assert parked["parked"] == "recovery"
    assert _calls(lane) == ["download_file"], "the diagnosis dispatched nothing"
    request_id, plan_revision, expected_digest, kind = lane.confirm_pending(
        action="repeat_step"
    )
    assert kind == "recovery"
    return lane, sessions, dead_operation, (request_id, plan_revision, expected_digest)


def _command_envelope(sessions, run_id):
    """The one command this database says carries the root's next admission."""
    with sessions() as db:
        state = db.get(MessageTransportState, 1)
        row = db.query(Outbox).filter_by(run_id=run_id, destination="command").one()
        run = db.get(Run, run_id)
        job = db.query(RunJob).filter_by(run_id=run_id).with_for_update().one()
        record = relay._record_envelope(
            row, state, _uuid.uuid4(), event=None, run=run, job=job
        )
        return record, (row.message_id, row.dispatch_generation, row.transport_epoch)


def _prepare_claimable(sessions, run_id, generation):
    """Put this database into the state a restarted worker would see.

    The transport gates are what the real worker checks before it admits a
    delivery: an installation whose transport is not open defers the command
    rather than executing it. The lane pulls the job back onto the clock so
    the claim is about the identity, not about a backoff window.
    """
    with sessions() as db:
        state = db.get(MessageTransportState, 1)
        state.active = True
        state.execution_open = True
        run = db.get(Run, run_id)
        job = db.query(RunJob).filter_by(run_id=run_id).one()
        assert (run.status, job.dispatch_generation) == (
            "queued",
            generation,
        ), (run.status, job.dispatch_generation)
        assert (job.owner_id, job.lease_until) == (None, None)
        now = datetime.now(UTC)
        available = job.available_at
        if available is not None and available.tzinfo is None:
            available = available.replace(tzinfo=UTC)
        if available is None or available > now - timedelta(seconds=5):
            job.available_at = now - timedelta(seconds=5)
        db.commit()


async def _admit_once(sessions, record, worker_id):
    """One exact claim of one command envelope, as the worker does it."""
    with sessions() as db:
        return claim_command(
            db,
            record.body,
            subject=record.subject,
            worker_id=worker_id,
            lease_seconds=600,
        )


async def _drive_graph(lane):
    """Run one graph pass off the test's own loop.

    The step node drives asyncio.run(), which needs a loop of its own, so the
    broker phase of this lane hands the worker's execution back through a
    thread exactly the way the relay lane's harness does.
    """
    return await asyncio.to_thread(lane.invoke)


async def test_commit_before_checkpoint_or_lost_ack_never_reexecutes(
    _recovered_choice, task_broker
):
    """A publish whose ack never arrived is retried under the same identity."""
    lane, sessions, dead_operation, (request_id, _revision, _digest) = _recovered_choice
    record, identity = _command_envelope(sessions, lane.run_id)
    first_ack = await publish_record(task_broker.js, record)
    assert first_ack.duplicate is False
    # The worker died between the publish and its processed flag: this database
    # still says the command is pending, and the broker already holds the copy.
    _settle_publication(
        sessions,
        record,
        error=NatsTimeoutError("lost puback"),
        now=datetime.now(UTC),
    )
    with sessions() as db:
        row = (
            db.query(Outbox).filter_by(run_id=lane.run_id, destination="command").one()
        )
        assert row.processed_at is None, "the checkpoint flag never arrived"
        assert row.message_id == identity[0]
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    retried, retry_identity = _command_envelope(sessions, lane.run_id)
    assert retry_identity == identity, "the retry keeps the command's identity"
    second_ack = await publish_record(task_broker.js, retried)
    assert second_ack.duplicate is True
    assert second_ack.message_id == identity[0]
    assert (await task_broker.js.stream_info(task_broker.stream)).state.messages == 1, (
        "one command, one copy in the stream"
    )
    _settle_publication(sessions, retried, error=None, now=datetime.now(UTC))

    _prepare_claimable(sessions, lane.run_id, identity[1])
    admitted = await _admit_once(sessions, retried, worker_id="recovered-worker")
    assert admitted.kind == "claimed", admitted.code
    competing = await _admit_once(sessions, retried, worker_id="another-worker")
    assert competing.kind != "claimed", "the same command is never claimed twice"

    # The lease that admission took is still held when the worker restarts:
    # the graph's own step re-reads it rather than taking another claim.
    lane.gateway.crash_after_admission = False
    lane.rehold()
    await _drive_graph(lane)

    dispatches = _calls(lane)
    assert dispatches == ["download_file", "download_file", "python_execute"]
    assert dispatches.count("python_execute") == 1, (
        "the step the choice never named ran once, on its own turn"
    )
    records = _durable(lane.run_id, sessions)
    assert {(row.step_id, row.ordinal, row.status) for row in records["attempts"]} == {
        ("fetch", 1, "succeeded"),
        ("analyze", 1, "succeeded"),
    }, "one repeat attempt and one untouched step"
    assert len(records["operations"]) == 3, (
        "the dead call, the repeat, the untouched step"
    )
    assert {row.id for row in records["operations"] if row.status == "admitted"} == {
        dead_operation.id
    }, "the dead call stays unsettled"
    with sessions() as db:
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(
                run_id=lane.run_id,
                request_id=request_id,
                kind="recovery",
                state="confirmed",
            )
            .count()
            == 1
        ), "one recorded choice"
    assert (await task_broker.js.stream_info(task_broker.stream)).state.messages == 1, (
        "the retry re-executed nothing at the broker"
    )


async def test_repeat_choice_redelivery_dispatches_new_attempt_once(
    _recovered_choice, task_broker
):
    """The same choice delivered twice still funds exactly one new attempt."""
    lane, sessions, dead_operation, (request_id, _revision, _digest) = _recovered_choice
    record, identity = _command_envelope(sessions, lane.run_id)

    first_ack = await publish_record(task_broker.js, record)
    assert first_ack.duplicate is False
    _settle_publication(sessions, record, error=None, now=datetime.now(UTC))

    redelivered, again_identity = _command_envelope(sessions, lane.run_id)
    assert again_identity == identity, "redelivery is the same command identity"
    second_ack = await publish_record(task_broker.js, redelivered)
    assert second_ack.duplicate is True
    assert second_ack.message_id == identity[0]
    assert (await task_broker.js.stream_info(task_broker.stream)).state.messages == 1

    _prepare_claimable(sessions, lane.run_id, identity[1])
    claimed = await _admit_once(sessions, record, worker_id="first-worker")
    assert claimed.kind == "claimed", claimed.code
    competing = await _admit_once(sessions, redelivered, worker_id="second-worker")
    assert competing.kind != "claimed"
    with sessions() as db:
        dispositions = (
            db.query(MessageDisposition).filter_by(message_id=identity[0]).all()
        )
        assert len(dispositions) == 1, "one admission proof"
        assert dispositions[0].disposition == "claimed"
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, request_id=request_id, kind="recovery")
            .count()
            == 1
        ), "the same choice was never recorded twice"

    lane.gateway.crash_after_admission = False
    lane.rehold()
    await _drive_graph(lane)

    assert _calls(lane) == ["download_file", "download_file", "python_execute"]
    records = _durable(lane.run_id, sessions)
    assert [(row.step_id, row.ordinal, row.status) for row in records["attempts"]] == [
        ("fetch", 1, "succeeded"),
        ("analyze", 1, "succeeded"),
    ], "exactly one new attempt"
    assert len(records["operations"]) == 3, "three operations, never one per delivery"
    assert {row.id for row in records["operations"] if row.status == "admitted"} == {
        dead_operation.id
    }, "the dead call's identity stays unsettled"
