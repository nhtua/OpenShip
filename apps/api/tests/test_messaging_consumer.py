"""Delivery ACKs require durable database outcomes, never Worker booleans."""

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from nats.errors import TimeoutError as NatsTimeoutError
from sqlalchemy.exc import OperationalError

from src.openship.database.session import SessionLocal
from src.openship.messaging.models import (
    MessageDisposition,
    MessageFailure,
    MessageTransportState,
)
from src.openship.messaging.outbox import enqueue_run
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import finalize
from tests.test_messaging_command_claim import _body, _command


def _prepare(db, body, subject):
    from src.openship.messaging.delivery import prepare_run_delivery

    return prepare_run_delivery(
        db, body, subject=subject, worker_id="delivery-worker", lease_seconds=30
    )


def _settle(db, prepared):
    from src.openship.messaging.delivery import settle_run_delivery

    return settle_run_delivery(db, prepared.command, prepared.decision.claim)


@pytest.mark.parametrize("worker_result,terminal", [(True, False), (False, True)])
def test_only_committed_terminal_state_authorizes_ack(worker_result, terminal):
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        assert prepared.decision.kind == "claimed"
        assert prepared.disposition is None

        def process_job(_claim):
            if terminal:
                with SessionLocal() as execution:
                    assert finalize(
                        execution, run_id, _claim.fence, {"status": "success"}
                    )
            return worker_result

        process_job(prepared.decision.claim)
        result = _settle(db, prepared)
        assert result.action == ("ack" if terminal else "defer")
        if terminal:
            replay = _prepare(db, body, subject)
            assert replay.disposition.action == "ack"
            assert replay.decision.claim is None
            assert db.get(Run, run_id).fence == 1
        row = db.get(MessageDisposition, prepared.command.message_id)
        assert row.payload_digest == prepared.command.body_digest
        assert row.handler_version == 1


@pytest.mark.parametrize(
    "status",
    ["completed", "failed", "cancelled", "timed_out", "stale", "waiting_connector"],
)
def test_preparation_commits_non_executable_noop_before_ack(status):
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        db.get(Run, run_id).status = status
        db.commit()
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        assert prepared.disposition.action == "ack"
        with SessionLocal() as observer:
            row = observer.get(MessageDisposition, prepared.command.message_id)
            assert row.disposition == "disposed"


@pytest.mark.parametrize("state", ["live", "future", "expired", "obsolete"])
def test_preparation_maps_recoverable_and_obsolete_states(state):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        job = db.get(RunJob, job_id)
        if state in {"live", "expired"}:
            job.owner_id = "other"
            job.lease_until = datetime.now(UTC) + timedelta(
                minutes=5 if state == "live" else -5
            )
            db.get(Run, run_id).status = "running"
        elif state == "future":
            job.available_at = datetime.now(UTC) + timedelta(minutes=5)
        else:
            job.dispatch_generation += 1
        db.commit()
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        expected = (
            "reconcile"
            if state == "expired"
            else "ack"
            if state == "obsolete"
            else "defer"
        )
        assert prepared.disposition.action == expected
        assert prepared.decision.claim is None
        if expected == "defer":
            assert prepared.disposition.delay >= 1


@pytest.mark.parametrize(
    "active,execution_open,code",
    [
        (False, True, "transport_inactive"),
        (True, False, "execution_closed"),
        (False, False, "transport_inactive"),
    ],
)
def test_closed_transport_holds_delivery_until_reopen(active, execution_open, code):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        state = db.get(MessageTransportState, 1)
        state.active, state.execution_open = active, execution_open
        db.commit()

        for _ in range(2):
            prepared = _prepare(db, body, subject)
            assert prepared.decision.kind == "defer"
            assert prepared.decision.code == code
            assert prepared.decision.claim is None
            assert prepared.disposition.action == "hold"
            assert prepared.disposition.code == code
            assert prepared.disposition.delay is None
            with SessionLocal() as observer:
                run, job = observer.get(Run, run_id), observer.get(RunJob, job_id)
                assert (run.status, run.fence, job.fence) == ("queued", 0, 0)
                assert job.owner_id is None and job.lease_until is None
                row = observer.get(MessageDisposition, prepared.command.message_id)
                assert row.disposition == "deferred"
                assert row.error_code == code

        state.active, state.execution_open = True, True
        db.commit()
        prepared = _prepare(db, body, subject)
        assert prepared.disposition is None
        assert prepared.decision.kind == "claimed"
        assert prepared.decision.claim.fence == 1


@pytest.mark.parametrize("prior_duplicate", [False, True])
@pytest.mark.parametrize(
    "gate,code",
    [("active", "transport_inactive"), ("execution_open", "execution_closed")],
)
def test_gate_held_duplicate_preserves_original_owners_settlement(
    gate, code, prior_duplicate
):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        original = _prepare(db, body, subject)
        assert original.decision.kind == "claimed"
        if prior_duplicate:
            assert _prepare(db, body, subject).disposition.action == "defer"
        with SessionLocal() as operator:
            operator.get(
                MessageDisposition, original.command.message_id
            ).handler_version = 2
            setattr(operator.get(MessageTransportState, 1), gate, False)
            operator.commit()

        held = _prepare(db, body, subject)
        assert (held.disposition.action, held.disposition.code) == ("hold", code)
        assert held.decision.claim is None
        job = db.get(RunJob, job_id)
        assert (job.owner_id, job.fence) == ("delivery-worker", 1)
        with SessionLocal() as execution:
            assert finalize(
                execution, run_id, original.decision.claim.fence, {"status": "success"}
            )

        result = _settle(db, original)
        assert (result.action, result.code) == ("ack", "run_terminal")
        row = db.get(MessageDisposition, original.command.message_id)
        assert row.payload_digest == original.command.body_digest
        assert row.handler_version == 2


@pytest.mark.parametrize("gate", ["active", "execution_open"])
def test_unadmitted_gate_hold_cannot_authorize_settlement(gate):
    from src.openship.messaging.delivery import settle_run_delivery
    from src.openship.runs.queue import Claim

    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        setattr(db.get(MessageTransportState, 1), gate, False)
        db.commit()
        held = _prepare(db, body, subject)
        assert held.disposition.action == "hold"
        # Even an independently terminal run and an unproven claim cannot
        # turn a never-admitted delivery into permission to ACK.
        with SessionLocal() as execution:
            execution.get(Run, run_id).status = "completed"
            execution.commit()
        unproven = Claim(run_id, 0, datetime.now(UTC), "delivery-worker")
        result = settle_run_delivery(db, held.command, unproven)
        assert (result.action, result.code) == ("reconcile", "admission_unproven")


@pytest.mark.parametrize("mutation", ["malformed", "identity", "digest"])
def test_invalid_delivery_is_content_free_quarantine_and_preserves_digest(mutation):
    with SessionLocal() as db:
        _, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        if mutation == "digest":
            first = _prepare(db, body, subject)
            body = json.dumps(json.loads(body), indent=2).encode()
        elif mutation == "identity":
            record = json.loads(body)
            record["causation_id"] = record["message_id"]
            body = json.dumps(record).encode()
        else:
            body = b'{"private-value"'
        result = _prepare(db, body, subject)
        assert result.disposition.action == "quarantine"
        assert len(result.disposition.code) <= 80
        assert "private-value" not in result.disposition.code
        assert db.get(RunJob, job_id).fence == (1 if mutation == "digest" else 0)
        if mutation == "digest":
            assert (
                db.get(MessageDisposition, first.command.message_id).payload_digest
                == first.command.body_digest
            )


@pytest.mark.parametrize(
    "outcome",
    [
        "terminal",
        "wait",
        "replacement",
        "missing_replacement",
        "missing_job",
        "deleted",
        "missing_ledger",
        "expired",
        "stale",
    ],
)
def test_settlement_requires_complete_durable_state(outcome):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with SessionLocal() as execution:
            run, job = execution.get(Run, run_id), execution.get(RunJob, job_id)
            if outcome == "terminal":
                finalize(
                    execution,
                    run_id,
                    prepared.decision.claim.fence,
                    {"status": "success"},
                )
            elif outcome == "wait":
                run.status = "waiting_connector"
                run.fence += 1
                job.fence = run.fence
                job.owner_id = None
                job.lease_until = None
            elif outcome in {"replacement", "missing_replacement"}:
                run.status = "queued"
                job.dispatch_generation += 1
                job.owner_id = None
                job.lease_until = None
                if outcome == "replacement":
                    enqueue_run(
                        execution,
                        run_id,
                        job.dispatch_generation,
                        epoch=prepared.command.transport_epoch,
                    )
            elif outcome == "missing_job":
                execution.delete(job)
            elif outcome == "deleted":
                execution.delete(job)
                execution.query(Outbox).filter_by(run_id=run_id).delete()
                execution.delete(run)
            elif outcome == "missing_ledger":
                execution.delete(
                    execution.get(MessageDisposition, prepared.command.message_id)
                )
                run.status = "completed"
            elif outcome == "expired":
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            else:
                run.fence += 1
                job.fence = run.fence
                job.owner_id = "new-owner"
            execution.commit()
        result = _settle(db, prepared)
        assert result.action == (
            "ack"
            if outcome in {"terminal", "wait", "replacement", "deleted"}
            else "reconcile"
            if outcome
            in {
                "expired",
                "missing_job",
                "missing_ledger",
                "missing_replacement",
                "stale",
            }
            else "defer"
        )
        if result.action == "ack":
            with SessionLocal() as observer:
                row = observer.get(MessageDisposition, prepared.command.message_id)
                assert row.disposition == "completed"
                assert row.payload_digest == prepared.command.body_digest
                assert row.handler_version == 1


def test_uncommitted_terminal_state_cannot_authorize_ack():
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        db.get(Run, run_id).status = "completed"
        assert _settle(db, prepared).action == "defer"
        assert db.get(Run, run_id).status == "running"


@pytest.mark.parametrize("phase", ["prepare", "settle"])
def test_db_commit_failure_remains_recoverable(phase, monkeypatch):
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with SessionLocal() as execution:
            finalize(
                execution, run_id, prepared.decision.claim.fence, {"status": "success"}
            )

        def fail_commit():
            raise OperationalError("commit", {}, Exception("private-db-error"))

        monkeypatch.setattr(db, "commit", fail_commit)
        result = (
            _prepare(db, body, subject).disposition
            if phase == "prepare"
            else _settle(db, prepared)
        )
        assert result.action == "defer"
        assert result.code == "database_unavailable"


def test_settlement_retry_is_idempotent_and_preserves_handler_version():
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with SessionLocal() as execution:
            execution.get(
                MessageDisposition, prepared.command.message_id
            ).handler_version = 2
            execution.commit()
            finalize(
                execution, run_id, prepared.decision.claim.fence, {"status": "success"}
            )
        assert _settle(db, prepared).action == "ack"
        assert _settle(db, prepared).action == "ack"
        row = db.get(MessageDisposition, prepared.command.message_id)
        assert row.handler_version == 2
        assert row.payload_digest == prepared.command.body_digest


def test_missing_transport_state_is_recoverable():
    with SessionLocal() as db:
        _, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        state = db.get(MessageTransportState, 1)
        db.delete(state)
        db.commit()
        try:
            prepared = _prepare(db, body, subject)
            assert prepared.disposition.action == "defer"
            assert prepared.disposition.code == "transport_missing"
            assert prepared.command is None
        finally:
            from sqlalchemy.orm import make_transient

            make_transient(state)
            db.add(state)
            db.commit()


@pytest.mark.parametrize("deferred_by", ["future", "inactive"])
def test_never_admitted_deferred_ledger_with_null_error_cannot_settle(deferred_by):
    from src.openship.messaging.delivery import settle_run_delivery
    from src.openship.runs.queue import Claim

    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        if deferred_by == "future":
            db.get(RunJob, job_id).available_at = datetime.now(UTC) + timedelta(days=1)
        else:
            db.get(MessageTransportState, 1).active = False
        db.commit()
        prepared = _prepare(db, body, subject)
        assert prepared.decision.claim is None
        row = db.get(MessageDisposition, prepared.command.message_id)
        assert row.disposition == "deferred"
        # A NULL error code is not proof that this command acquired a lease.
        row.error_code = None
        db.get(Run, run_id).status = "completed"
        db.commit()
        result = settle_run_delivery(
            db, prepared.command, Claim(run_id, 0, datetime.now(UTC), "delivery-worker")
        )
        assert (result.action, result.code) == ("reconcile", "admission_unproven")


@pytest.mark.parametrize("duplicate_after", ["live", "terminal", "obsolete"])
def test_duplicate_decision_preserves_admitted_owners_settlement(duplicate_after):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        original = _prepare(db, body, subject)
        if duplicate_after == "terminal":
            assert finalize(
                db, run_id, original.decision.claim.fence, {"status": "success"}
            )
        elif duplicate_after == "obsolete":
            job = db.get(RunJob, job_id)
            db.get(Run, run_id).status = "queued"
            job.owner_id = None
            job.lease_until = None
            job.dispatch_generation += 1
            enqueue_run(
                db,
                run_id,
                job.dispatch_generation,
                epoch=original.command.transport_epoch,
            )
            db.commit()
        duplicate = _prepare(db, body, subject)
        assert duplicate.decision.claim is None
        assert (
            db.get(MessageDisposition, original.command.message_id).disposition
            == "claimed"
        )
        if duplicate_after == "live":
            assert finalize(
                db, run_id, original.decision.claim.fence, {"status": "success"}
            )
        assert _settle(db, original).action == "ack"


# Real sessions/acquisition/settlement; controlled broker and synthetic effect.
class _RunMessage:
    def __init__(self, body, subject, stop, *, metadata=None, num_delivered=1):
        self.data, self.subject, self.stop = body, subject, stop
        self.progress, self.acks, self.naks = 0, 0, []
        self.on_ack = lambda: None
        self.ack_failure = False
        self.metadata = metadata or SimpleNamespace(
            stream="OS_RUNS",
            sequence=SimpleNamespace(stream=uuid.uuid4().int % (2**63 - 1) + 1),
            timestamp=datetime.now(UTC),
            num_delivered=num_delivered,
        )

    async def in_progress(self):
        self.progress += 1

    async def ack_sync(self, *, timeout):
        assert 0 < timeout <= 5
        self.on_ack()
        self.acks += 1
        self.stop.set()
        if self.ack_failure:
            raise NatsTimeoutError

    async def nak(self, *, delay):
        self.naks.append(delay)
        self.stop.set()


class _RunBroker:
    def __init__(self, messages):
        self.is_connected = True
        self.stats = {"reconnects": 0}
        self.messages = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.bindings, self.fetches, self.unsubscribed = [], [], []
        self.control_subscriptions = []
        self.rpc_order = []
        self.published = []
        self.on_fetch = lambda: None
        self.topology_checks = 0
        self.consumer = None

    def jetstream(self, *, timeout):
        return self

    async def pull_subscribe_bind(self, **kwargs):
        self.rpc_order.append("pull_subscribe")
        self.bindings.append(kwargs)
        broker = self

        class Subscription:
            async def fetch(self, *, batch, timeout):
                assert batch == 1 and 0 < timeout <= 5
                assert (
                    len(broker.consumer._pool._available) < broker.consumer.concurrency
                )
                broker.fetches.append(self)
                broker.on_fetch()
                try:
                    return [await asyncio.wait_for(broker.messages.get(), timeout)]
                except TimeoutError:
                    raise NatsTimeoutError from None

            async def unsubscribe(self):
                broker.unsubscribed.append(self)

        return Subscription()

    async def subscribe(self, subject, *, cb, **kwargs):
        self.rpc_order.append("subscribe")
        subscription = (subject, cb, kwargs)
        self.control_subscriptions.append(subscription)
        broker = self

        class ControlSubscription:
            async def unsubscribe(self):
                broker.control_subscriptions.remove(subscription)

        return ControlSubscription()

    async def flush(self, *, timeout):
        assert timeout > 0
        self.rpc_order.append("flush")

    async def publish(self, subject, payload, *, reply="", headers=None):
        self.published.append((subject, payload, reply, headers))


class _InlineSlot:
    def __init__(self):
        self.resource = SimpleNamespace(
            db=SessionLocal(), worker=SimpleNamespace(worker_id="inline-test")
        )

    async def run(self, function, *args, **kwargs):
        return function(self.resource, *args, **kwargs)


class _NoWorkPool:
    """Reservation-only pool for lifecycle tests that never execute work."""

    def __init__(self, size):
        self._slots = [object() for _ in range(size)]
        self._available = list(self._slots)

    @asynccontextmanager
    async def reserve(self):
        slot = self._available.pop()
        try:
            yield slot
        finally:
            self._available.append(slot)

    async def close(self):
        return None


def _loop_fixture(
    monkeypatch,
    *,
    concurrency=1,
    worker_result=False,
    terminal=True,
    empty=False,
    factory_hook=lambda: None,
    handlers=None,
    failure_publisher=None,
    owner_check_hook=lambda: None,
    execution_hook=lambda _supervision: None,
):
    from src.openship.messaging import consumer

    async def idle_recovery(stop):
        await stop.wait()

    stop = asyncio.Event()
    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        body, subject = _body(db, row_id)
    message = _RunMessage(body, subject, stop)
    broker = _RunBroker([] if empty else [message])
    resources, effects, calls = [], [], []
    owner_checks = []

    class SyntheticWorker:
        Session = SessionLocal

        def __init__(self):
            self.thread = threading.get_ident()
            self.worker_id = "consumer-test"
            self.checkpointer = object()
            self.closed = False
            resources.append(self)
            factory_hook()

        def process_job(self, claim, *, supervision=None):
            assert threading.get_ident() == self.thread
            if supervision is not None:
                execution_hook(supervision)
            effects.append(claim.run_id)
            calls.append("effect")
            if terminal:
                with self.Session() as db:
                    assert finalize(
                        db, claim.run_id, claim.fence, {"status": "success"}
                    )
            return worker_result

        def close(self):
            assert threading.get_ident() == self.thread
            self.closed = True

        def _ownership_lost(self, db, run_id, _fence):
            assert db.query(Run).filter_by(id=run_id).one_or_none() is not None
            owner_check_hook()
            owner_checks.append(threading.get_ident())
            return False

    async def topology(**kwargs):
        assert kwargs["apply"] is False and kwargs["js"] is broker
        broker.topology_checks += 1
        return SimpleNamespace(ready=True)

    monkeypatch.setattr(consumer, "ensure_topology", topology)

    class IdleExhaustionSupervisor:
        def __init__(self, _client, **_kwargs):
            pass

        async def run(self, stop, *, started=None):
            if started is not None:
                started.set()
            await stop.wait()

    monkeypatch.setattr(consumer, "ExhaustionSupervisor", IdleExhaustionSupervisor)
    instance = consumer.RunConsumer(
        broker,
        concurrency=concurrency,
        session_factory=SessionLocal,
        worker_factory=SyntheticWorker,
        progress_interval=0.01,
        fetch_timeout=0.03,
        retry_interval=0.01,
        handlers=handlers,
        failure_publisher=failure_publisher,
        recovery_supervisor=SimpleNamespace(run=idle_recovery),
    )
    broker.consumer = instance
    return SimpleNamespace(
        consumer=instance,
        broker=broker,
        stop=stop,
        message=message,
        resources=resources,
        effects=effects,
        run_id=run_id,
        calls=calls,
        owner_checks=owner_checks,
    )


async def _until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_result,terminal", [(False, True), (True, False)])
async def test_loop_acks_committed_state_independently_of_worker_bool(
    monkeypatch, worker_result, terminal
):
    f = _loop_fixture(monkeypatch, worker_result=worker_result, terminal=terminal)

    def checked_ack():
        with SessionLocal() as db:
            row = db.get(
                MessageDisposition, uuid.UUID(json.loads(f.message.data)["message_id"])
            )
            assert row.disposition == "completed"
            assert db.get(Run, f.run_id).status == "completed"
        f.calls.append("ack")

    f.message.on_ack = checked_ack
    await asyncio.wait_for(f.consumer.run(f.stop), 4)
    assert f.effects == [f.run_id]
    assert f.message.acks == int(terminal)
    assert f.message.naks == ([] if terminal else [5.0])
    assert f.calls == (["effect", "ack"] if terminal else ["effect"])
    assert f.message.progress >= 1
    assert all(resource.closed for resource in f.resources)
    assert len(f.broker.unsubscribed) == 1


@pytest.mark.asyncio
async def test_loop_binds_distinct_bounded_subscriptions_and_reserves_before_fetch(
    monkeypatch,
):
    f = _loop_fixture(monkeypatch, concurrency=2, empty=True)

    async def fetch_allowed():
        return True

    f.consumer._installation_id = uuid.uuid4()
    f.consumer._fetch_allowed = fetch_allowed
    f.consumer._pool = _NoWorkPool(2)
    f.consumer._metadata = _NoWorkPool(1)
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await _until(lambda: len(f.broker.fetches) >= 2)
        assert len(set(f.broker.fetches)) == 2
        assert (
            f.broker.bindings
            == [
                {
                    "stream": "OS_RUNS",
                    "consumer": "run-v1",
                    "pending_msgs_limit": 1,
                    "pending_bytes_limit": 16384,
                },
            ]
            * 2
        )
        assert f.resources == []
    finally:
        f.stop.set()
        await asyncio.wait_for(task, 5)
    assert len(f.broker.unsubscribed) == 2


@pytest.mark.asyncio
async def test_run_intake_waits_for_exhaustion_observer_binding(monkeypatch):
    from src.openship.messaging import consumer

    f = _loop_fixture(monkeypatch, empty=True)
    observer_starting = asyncio.Event()
    allow_binding = asyncio.Event()

    class DelayedExhaustionSupervisor:
        def __init__(self, _client, **_kwargs):
            pass

        async def run(self, stop, *, started=None):
            observer_starting.set()
            await allow_binding.wait()
            if started is not None:
                started.set()
            await stop.wait()

    monkeypatch.setattr(consumer, "ExhaustionSupervisor", DelayedExhaustionSupervisor)

    async def fetch_allowed():
        return True

    f.consumer._installation_id = uuid.uuid4()
    f.consumer._fetch_allowed = fetch_allowed
    f.consumer._pool = _NoWorkPool(f.consumer.concurrency)
    f.consumer._metadata = _NoWorkPool(1)
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await asyncio.wait_for(observer_starting.wait(), 2)
        await asyncio.sleep(0.03)
        assert f.broker.bindings == []
        assert f.broker.control_subscriptions == []

        allow_binding.set()
        await _until(lambda: len(f.broker.fetches) >= 1)
        assert f.consumer._exhaustion_task is not None
        assert not f.consumer._exhaustion_task.done()
    finally:
        allow_binding.set()
        f.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_slow_slot_setup_keeps_immediate_periodic_progress_and_other_capacity(
    monkeypatch,
):
    started, release = threading.Event(), threading.Event()

    def setup():
        started.set()
        assert release.wait(4)

    f = _loop_fixture(monkeypatch, concurrency=2, factory_hook=setup)
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await _until(started.is_set)
        await _until(lambda: f.message.progress >= 3)
        assert f.message.acks == 0
        assert len(set(f.broker.fetches)) == 2
        from src.openship.messaging.executor import ExecutionPool

        background = ExecutionPool(1, lambda: SimpleNamespace(close=lambda: None))
        try:
            async with background.reserve() as slot:
                assert await slot.run(lambda _: "independent") == "independent"
        finally:
            await background.close()
    finally:
        release.set()
        await asyncio.wait_for(task, 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["active", "execution_open"])
async def test_fetched_gate_hold_progresses_without_claim_or_nak_and_retries_same_message(
    monkeypatch, gate
):
    f = _loop_fixture(monkeypatch)

    def close_gate():
        with SessionLocal() as db:
            setattr(db.get(MessageTransportState, 1), gate, False)
            db.commit()
        f.broker.on_fetch = lambda: None

    f.broker.on_fetch = close_gate
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await _until(lambda: f.message.progress >= 3)
        assert f.effects == [] and f.message.naks == [] and f.message.acks == 0
        assert len(f.broker.fetches) == 1
        with SessionLocal() as db:
            assert db.get(Run, f.run_id).fence == 0
            setattr(db.get(MessageTransportState, 1), gate, True)
            db.commit()
        await asyncio.wait_for(task, 4)
        assert f.effects == [f.run_id] and f.message.acks == 1
        assert len(f.broker.fetches) == 1
    finally:
        f.stop.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["active", "execution_open", "disconnected"])
async def test_loop_stops_new_pulls_until_transport_reopens(monkeypatch, gate):
    f = _loop_fixture(monkeypatch)
    if gate == "disconnected":
        f.broker.is_connected = False
    else:
        with SessionLocal() as db:
            setattr(db.get(MessageTransportState, 1), gate, False)
            db.commit()
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await asyncio.sleep(0.06)
        assert f.broker.fetches == []
        if gate == "disconnected":
            f.broker.is_connected = True
            f.broker.stats["reconnects"] += 1
        else:
            with SessionLocal() as db:
                setattr(db.get(MessageTransportState, 1), gate, True)
                db.commit()
        await asyncio.wait_for(task, 4)
        assert f.message.acks == 1
    finally:
        f.stop.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publisher_result",
    ["unconfirmed", "confirmed_without_marker", "confirmed_and_settled"],
)
async def test_quarantine_source_ack_requires_confirmed_failure_publication(
    monkeypatch, publisher_result
):
    from src.openship.messaging import consumer
    from src.openship.messaging.delivery import DeliveryDisposition, PreparedDelivery

    published = []

    async def publish(handoff):
        with SessionLocal() as db:
            failure = db.get(MessageFailure, handoff.failure_id)
            outbox = db.get(Outbox, handoff.outbox_id)
            assert failure is not None and outbox is not None
            assert outbox.processed_at is None
        published.append(handoff)
        if publisher_result == "confirmed_and_settled":
            with SessionLocal() as db:
                outbox = db.get(Outbox, handoff.outbox_id)
                outbox.processed_at = datetime.now(UTC)
                db.commit()
        return publisher_result != "unconfirmed"

    f = _loop_fixture(monkeypatch, failure_publisher=publish)
    with SessionLocal() as db:
        f.consumer._installation_id = db.get(MessageTransportState, 1).installation_id

    async def run_metadata(function, *args, **kwargs):
        return function(*args, **kwargs)

    f.consumer._run_metadata = run_metadata
    monkeypatch.setattr(
        consumer,
        "prepare_run_delivery",
        lambda *_a, **_kw: PreparedDelivery(
            None, None, DeliveryDisposition("quarantine", "invalid_run_command")
        ),
    )
    slot = _InlineSlot()
    try:
        await f.consumer._handle(slot, f.message, f.stop)
        if publisher_result == "confirmed_and_settled":
            assert f.message.acks == 1
            assert f.message.naks == []
        else:
            assert f.message.acks == 0 and f.message.naks == [1.0]
        assert f.effects == []
        assert len(published) == 1
    finally:
        f.stop.set()
        slot.resource.db.close()
        if published:
            with SessionLocal() as db:
                failure_ids = [handoff.failure_id for handoff in published]
                db.query(Outbox).filter(Outbox.failure_id.in_(failure_ids)).delete(
                    synchronize_session=False
                )
                db.query(MessageFailure).filter(
                    MessageFailure.id.in_(failure_ids)
                ).delete(synchronize_session=False)
                db.commit()


@pytest.mark.asyncio
async def test_lost_ack_and_reconnect_redelivery_never_repeats_effect(monkeypatch):
    f = _loop_fixture(monkeypatch)
    first = f.message
    first.stop = asyncio.Event()
    first.ack_failure = True
    replay = _RunMessage(first.data, first.subject, f.stop, metadata=first.metadata)
    f.broker.messages.put_nowait(replay)

    def reconnect_after_commit():
        with SessionLocal() as db:
            assert db.get(Run, f.run_id).status == "completed"
        f.broker.stats["reconnects"] += 1

    first.on_ack = reconnect_after_commit
    await asyncio.wait_for(f.consumer.run(f.stop), 4)
    assert f.effects == [f.run_id]
    assert first.acks == replay.acks == 1
    assert f.broker.topology_checks == 2
    assert f.resources[0].closed


@pytest.mark.asyncio
async def test_two_slots_execute_independently_while_one_worker_setup_blocks(
    monkeypatch,
):
    entered, release = threading.Event(), threading.Event()
    once = threading.Lock()

    def block_first_setup():
        if once.acquire(blocking=False):
            entered.set()
            assert release.wait(4)

    f = _loop_fixture(monkeypatch, concurrency=2, factory_hook=block_first_setup)
    f.message.stop = asyncio.Event()
    with SessionLocal() as db:
        second_run, _, row_id = _command(db)
        body, subject = _body(db, row_id)
    second = _RunMessage(body, subject, asyncio.Event())
    f.broker.messages.put_nowait(second)
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await _until(entered.is_set)
        await _until(lambda: sum(message.acks for message in [f.message, second]) == 1)
        assert not release.is_set()
        assert sum(message.acks for message in [f.message, second]) == 1
        assert len(f.resources) == 2
        assert f.resources[0].thread != f.resources[1].thread
        assert f.resources[0].checkpointer is not f.resources[1].checkpointer
        release.set()
        await _until(lambda: sum(message.acks for message in [f.message, second]) == 2)
        assert set(f.effects) == {f.run_id, second_run}
    finally:
        release.set()
        f.stop.set()
        await asyncio.wait_for(task, 4)


@pytest.mark.asyncio
async def test_topology_drift_rejects_run_binding_and_fetch(monkeypatch):
    from src.openship.messaging import consumer
    from src.openship.messaging.topology import TopologyCompatibilityError

    f = _loop_fixture(monkeypatch)

    async def drift(**_kwargs):
        return SimpleNamespace(ready=False)

    monkeypatch.setattr(consumer, "ensure_topology", drift)
    with pytest.raises(TopologyCompatibilityError):
        await f.consumer.run(f.stop)
    assert f.broker.bindings == [] and f.broker.fetches == [] and f.effects == []


@pytest.mark.asyncio
async def test_default_consumer_fails_closed_if_worker_checkpoint_setup_is_unavailable(
    monkeypatch,
):
    from src.openship.messaging import consumer

    f = _loop_fixture(monkeypatch)
    closed = []
    worker_options = {}

    class BrokenWorker:
        Session = SessionLocal
        checkpointer = None
        worker_id = "no-checkpointer"

        def __init__(self, **kwargs):
            worker_options.update(kwargs)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(consumer, "Worker", BrokenWorker)
    replacement = consumer.RunConsumer(
        f.broker,
        concurrency=1,
        session_factory=SessionLocal,
        fetch_timeout=0.03,
        progress_interval=0.01,
        recovery_supervisor=f.consumer._recovery,
    )
    f.broker.consumer = replacement
    with pytest.raises(RuntimeError, match="checkpointer"):
        await asyncio.wait_for(replacement.run(f.stop), 4)
    assert f.message.acks == 0 and f.message.naks == [] and closed == [True]
    assert worker_options["refresh_availability_during_run"] is False


@pytest.mark.asyncio
async def test_ordinary_future_deferral_uses_bounded_delayed_nak_without_claim(
    monkeypatch,
):
    f = _loop_fixture(monkeypatch)
    with SessionLocal() as db:
        job = db.query(RunJob).filter_by(run_id=f.run_id).one()
        job.available_at = datetime.now(UTC) + timedelta(days=1)
        db.commit()
    await asyncio.wait_for(f.consumer.run(f.stop), 4)
    assert f.message.naks == [60.0] and f.message.acks == 0 and f.effects == []
    with SessionLocal() as db:
        assert db.get(Run, f.run_id).fence == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery_count,expected_delay",
    [(1, 1.0), (2, 5.0), (3, 15.0), (4, 30.0), (5, 60.0), (20, 60.0)],
)
async def test_failure_handoff_nak_uses_bounded_redelivery_backoff(
    monkeypatch, delivery_count, expected_delay
):
    f = _loop_fixture(monkeypatch, empty=True)
    f.message.metadata.num_delivered = delivery_count

    await f.consumer._retry_delivery(f.message, "invalid_run_command")

    assert f.message.naks == [expected_delay]


@pytest.mark.asyncio
async def test_unconfirmed_handler_releases_uncommitted_database_transaction(
    monkeypatch,
):
    from src.openship.messaging import consumer
    from src.openship.messaging.delivery import DeliveryDisposition, PreparedDelivery

    observed = []

    def uncertain_handler(db, _prepared, _disposition, _source, _body):
        db.get(Run, f.run_id).status = "failed"
        db.flush()
        observed.append(db)

    f = _loop_fixture(monkeypatch, handlers={"reconcile": uncertain_handler})
    monkeypatch.setattr(
        consumer,
        "prepare_run_delivery",
        lambda *_args, **_kwargs: PreparedDelivery(
            None, None, DeliveryDisposition("reconcile", "durable_state_incomplete")
        ),
    )
    slot = _InlineSlot()
    try:
        await f.consumer._handle(slot, f.message, f.stop)
        assert observed
        assert not observed[0].in_transaction()
        with SessionLocal() as observer:
            assert observer.get(Run, f.run_id).status == "queued"
        assert f.message.acks == 0 and f.message.naks == [1.0]
    finally:
        f.stop.set()
        slot.resource.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["ownership_expired", "ownership_moved"])
async def test_live_owner_reconcile_is_delayed_without_failure_ack(monkeypatch, code):
    from src.openship.messaging import consumer
    from src.openship.messaging.delivery import DeliveryDisposition, PreparedDelivery

    called = []

    def handler(*_args):
        called.append(True)
        raise AssertionError("live-owner states cannot use generic failure handoff")

    f = _loop_fixture(monkeypatch, handlers={"reconcile": handler})
    monkeypatch.setattr(
        consumer,
        "prepare_run_delivery",
        lambda *_args, **_kwargs: PreparedDelivery(
            None, None, DeliveryDisposition("reconcile", code)
        ),
    )
    slot = _InlineSlot()
    try:
        await f.consumer._handle(slot, f.message, f.stop)
        assert f.message.acks == 0 and f.message.naks == [1.0]
        assert called == []
    finally:
        f.stop.set()
        slot.resource.db.close()


@pytest.mark.asyncio
async def test_consumer_runs_recovery_while_broker_fetches_are_unavailable(
    monkeypatch,
):
    f = _loop_fixture(monkeypatch, empty=True)
    recovery_started = asyncio.Event()
    f.broker.is_connected = False

    class Recovery:
        async def run(self, stop):
            recovery_started.set()
            await stop.wait()

    f.consumer._recovery = Recovery()
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await asyncio.wait_for(recovery_started.wait(), 1)
        assert not f.broker.fetches
    finally:
        f.stop.set()
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_consumer_health_rpc_is_ready_before_run_pulls_and_is_cleaned_up(
    monkeypatch,
):
    f = _loop_fixture(monkeypatch, empty=True)
    with SessionLocal() as db:
        f.consumer._installation_id = db.get(MessageTransportState, 1).installation_id
    intake_open = True

    async def test_gate():
        return intake_open

    async def idle_consumer(_subscription, stop, started):
        started.set()
        await stop.wait()

    class NoopPool:
        async def close(self):
            return None

    f.consumer._fetch_allowed = test_gate
    f.consumer._consume = idle_consumer
    f.consumer._pool = NoopPool()
    f.consumer._metadata = NoopPool()
    task = asyncio.create_task(f.consumer.run(f.stop))
    try:
        await _until(lambda: f.consumer._intake_ready)
        installation_id = f.consumer._installation_id
        health_subject = f"openship.{installation_id.hex}.v1.rpc.worker.health"
        assert f.broker.rpc_order[:3] == ["subscribe", "flush", "pull_subscribe"]
        subject, callback, options = next(
            item for item in f.broker.control_subscriptions if item[0] == health_subject
        )
        assert subject == health_subject
        assert options == {
            "pending_msgs_limit": 1,
            "pending_bytes_limit": 16 * 1024,
        }

        api_instance_id, request_id = uuid.uuid4(), uuid.uuid4()
        reply_subject = (
            f"openship.{installation_id.hex}.v1.reply."
            f"{api_instance_id.hex}.{request_id.hex}"
        )
        request = {
            "schema_version": 1,
            "kind": "worker.health",
            "installation_id": str(installation_id),
            "api_instance_id": str(api_instance_id),
            "request_id": str(request_id),
            "source_service": "api",
        }
        await callback(
            SimpleNamespace(
                subject=health_subject,
                data=json.dumps(request).encode(),
                reply=reply_subject,
            )
        )
        assert len(f.broker.published) == 1
        assert f.broker.published[0][0] == reply_subject
        assert json.loads(f.broker.published[0][1]) == {
            "schema_version": 1,
            "kind": "worker.health.result",
            "installation_id": str(installation_id),
            "api_instance_id": str(api_instance_id),
            "request_id": str(request_id),
            "source_service": "worker",
            "ready": True,
        }

        with SessionLocal() as db:
            db.get(MessageTransportState, 1).execution_open = False
            db.commit()
        intake_open = False
        await callback(
            SimpleNamespace(
                subject=health_subject,
                data=json.dumps(request).encode(),
                reply=reply_subject,
            )
        )
        assert len(f.broker.published) == 1
    finally:
        f.stop.set()
        await asyncio.wait_for(task, 5)
    assert not f.broker.control_subscriptions


@pytest.mark.asyncio
async def test_worker_health_requires_bound_run_intake(monkeypatch):
    f = _loop_fixture(monkeypatch, empty=True)

    async def fetch_allowed():
        return True

    f.consumer._fetch_allowed = fetch_allowed
    recovery_task = asyncio.create_task(asyncio.Event().wait())
    exhaustion_task = asyncio.create_task(asyncio.Event().wait())
    f.consumer._recovery_task = recovery_task
    f.consumer._exhaustion_task = exhaustion_task
    try:
        assert not await f.consumer._health_ready(f.stop)
    finally:
        recovery_task.cancel()
        exhaustion_task.cancel()
        await asyncio.gather(recovery_task, exhaustion_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_health_requires_live_exhaustion_observer(monkeypatch):
    f = _loop_fixture(monkeypatch, empty=True)
    intake_task = asyncio.create_task(asyncio.Event().wait())
    recovery_task = asyncio.create_task(asyncio.Event().wait())
    f.consumer._consumer_tasks = (intake_task,)
    f.consumer._recovery_task = recovery_task
    f.consumer._exhaustion_task = asyncio.create_task(asyncio.sleep(0))
    f.consumer._intake_ready = True
    try:
        await f.consumer._exhaustion_task
        assert not f.consumer._health_runtime_ready(f.stop)
    finally:
        intake_task.cancel()
        recovery_task.cancel()
        await asyncio.gather(intake_task, recovery_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_health_rechecks_intake_after_gate_check(monkeypatch):
    f = _loop_fixture(monkeypatch, empty=True)
    intake_task = asyncio.create_task(asyncio.Event().wait())
    recovery_task = asyncio.create_task(asyncio.Event().wait())
    exhaustion_task = asyncio.create_task(asyncio.Event().wait())
    f.consumer._consumer_tasks = (intake_task,)
    f.consumer._recovery_task = recovery_task
    f.consumer._exhaustion_task = exhaustion_task
    f.consumer._intake_ready = True

    async def gate_check_during_shutdown():
        f.consumer._intake_ready = False
        f.consumer._consumer_tasks = ()
        return True

    f.consumer._fetch_allowed = gate_check_during_shutdown
    try:
        assert not await f.consumer._health_ready(f.stop)
    finally:
        intake_task.cancel()
        recovery_task.cancel()
        exhaustion_task.cancel()
        await asyncio.gather(
            intake_task, recovery_task, exhaustion_task, return_exceptions=True
        )


@pytest.mark.asyncio
async def test_run_control_subscription_is_ready_before_owner_check_and_only_wakes(
    monkeypatch,
):
    from src.openship.messaging import consumer

    order = []
    callbacks = []
    f = _loop_fixture(monkeypatch)
    with SessionLocal() as db:
        prepared = consumer.prepare_run_delivery(
            db,
            f.message.data,
            subject=f.message.subject,
            worker_id="consumer-test",
            lease_seconds=30,
        )
    assert prepared.decision.kind == "claimed"

    async def subscribe(subject, *, cb, pending_msgs_limit, pending_bytes_limit):
        order.append("subscribe")
        assert subject == (
            f"openship.{json.loads(f.message.data)['installation_id'].replace('-', '')}"
            f".v1.control.{f.run_id.hex}"
        )
        assert pending_msgs_limit == 1 and pending_bytes_limit == 16 * 1024
        callbacks.append(cb)

        class Subscription:
            async def unsubscribe(self):
                order.append("unsubscribe")

        return Subscription()

    async def flush(*, timeout):
        assert timeout > 0
        order.append("flush")
        # This stale notice can wake a check but cannot directly stop the new owner.
        await callbacks[0](SimpleNamespace(data=b"stale-control"))

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)
    monkeypatch.setattr(f.broker, "flush", flush, raising=False)
    wakeups = []
    signal = SimpleNamespace(wake=lambda: wakeups.append(True))

    subscription = await f.consumer._subscribe_control(prepared, signal)
    await subscription.unsubscribe()

    assert order == ["subscribe", "flush", "unsubscribe"]
    assert wakeups == [True]


@pytest.mark.asyncio
async def test_run_control_subscription_is_removed_when_flush_fails(monkeypatch):
    from src.openship.messaging import consumer

    f = _loop_fixture(monkeypatch)
    with SessionLocal() as db:
        prepared = consumer.prepare_run_delivery(
            db,
            f.message.data,
            subject=f.message.subject,
            worker_id="consumer-test",
            lease_seconds=30,
        )
    assert prepared.decision.kind == "claimed"
    unsubscribed = []

    async def subscribe(_subject, *, cb, **kwargs):
        class Subscription:
            async def unsubscribe(self):
                unsubscribed.append(True)

        return Subscription()

    async def fail_flush(*, timeout):
        raise TimeoutError("flush not confirmed")

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)
    monkeypatch.setattr(f.broker, "flush", fail_flush, raising=False)

    with pytest.raises(TimeoutError, match="flush not confirmed"):
        await f.consumer._subscribe_control(
            prepared, SimpleNamespace(wake=lambda: None)
        )

    assert unsubscribed == [True]


@pytest.mark.asyncio
async def test_run_control_subscription_is_removed_when_flush_is_cancelled(monkeypatch):
    from src.openship.messaging import consumer

    f = _loop_fixture(monkeypatch)
    with SessionLocal() as db:
        prepared = consumer.prepare_run_delivery(
            db,
            f.message.data,
            subject=f.message.subject,
            worker_id="consumer-test",
            lease_seconds=30,
        )
    assert prepared.decision.kind == "claimed"

    unsubscribed = []

    async def subscribe(_subject, *, cb, **kwargs):
        class Subscription:
            async def unsubscribe(self):
                unsubscribed.append(True)

        return Subscription()

    async def cancel_flush(*, timeout):
        raise asyncio.CancelledError

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)
    monkeypatch.setattr(f.broker, "flush", cancel_flush, raising=False)

    with pytest.raises(asyncio.CancelledError):
        await f.consumer._subscribe_control(
            prepared, SimpleNamespace(wake=lambda: None)
        )

    assert unsubscribed == [True]


@pytest.mark.asyncio
async def test_run_execution_cancellation_interrupts_supervision_and_unsubscribes(
    monkeypatch,
):
    from src.openship.messaging import consumer

    f = _loop_fixture(monkeypatch)
    with SessionLocal() as db:
        prepared = consumer.prepare_run_delivery(
            db,
            f.message.data,
            subject=f.message.subject,
            worker_id="consumer-test",
            lease_seconds=30,
        )
    assert prepared.decision.kind == "claimed"
    unsubscribed = []

    async def subscribe(_subject, *, cb, **kwargs):
        class Subscription:
            async def unsubscribe(self):
                unsubscribed.append(True)

        return Subscription()

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)

    class CancelledSlot:
        async def run(self, _call, *_args, stop=None, **_kwargs):
            assert stop is not None
            stop()
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await f.consumer._execute_controlled(CancelledSlot(), prepared)

    assert unsubscribed == [True]


@pytest.mark.asyncio
async def test_run_handle_checks_authoritative_owner_after_control_subscription(
    monkeypatch,
):
    order = []
    callbacks = []

    def execution_hook(supervision):
        order.append("execute")
        assert not supervision.stop() and not supervision.lease_lost()
        assert supervision.wait_for_wakeup(0)

    f = _loop_fixture(
        monkeypatch,
        owner_check_hook=lambda: order.append("owner_check"),
        execution_hook=execution_hook,
    )

    async def subscribe(_subject, *, cb, pending_msgs_limit, pending_bytes_limit):
        order.append("subscribe")
        assert pending_msgs_limit == 1 and pending_bytes_limit == 16 * 1024
        callbacks.append(cb)

        class Subscription:
            async def unsubscribe(self):
                order.append("unsubscribe")

        return Subscription()

    async def flush(*, timeout):
        assert timeout > 0
        order.append("flush")
        await callbacks[0](SimpleNamespace(data=b"stale-control"))

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)
    monkeypatch.setattr(f.broker, "flush", flush, raising=False)
    f.message.on_ack = lambda: order.append("ack")

    resource = f.consumer._pool._slots[0]._factory()
    process_job = resource.worker.process_job

    def check_no_open_claim_transaction(claim, *, supervision):
        assert not resource.db.in_transaction()
        return process_job(claim, supervision=supervision)

    resource.worker.process_job = check_no_open_claim_transaction

    class InlineSlot:
        async def run(self, call, *args, stop=None, **kwargs):
            return call(resource, *args, **kwargs)

    try:
        await f.consumer._handle(InlineSlot(), f.message, f.stop)
    finally:
        resource.close()

    assert order == [
        "subscribe",
        "flush",
        "owner_check",
        "execute",
        "unsubscribe",
        "ack",
    ]


@pytest.mark.asyncio
async def test_closed_nats_connection_does_not_hide_committed_run_settlement(
    monkeypatch,
):
    from nats.errors import ConnectionClosedError

    f = _loop_fixture(monkeypatch)
    unsubscribed = []

    async def subscribe(_subject, *, cb, **kwargs):
        class Subscription:
            async def unsubscribe(self):
                unsubscribed.append(True)
                raise ConnectionClosedError

        return Subscription()

    monkeypatch.setattr(f.broker, "subscribe", subscribe, raising=False)
    monkeypatch.setattr(f.broker, "flush", lambda **_kwargs: asyncio.sleep(0))

    resource = f.consumer._pool._slots[0]._factory()

    class InlineSlot:
        async def run(self, call, *args, stop=None, **kwargs):
            return call(resource, *args, **kwargs)

    try:
        await f.consumer._handle(InlineSlot(), f.message, f.stop)
    finally:
        resource.close()

    assert unsubscribed == [True]
    assert f.message.acks == 1
    assert f.effects == [f.run_id]
