"""MaxDeliver advisory validation and durable live-owner observations."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from nats.errors import TimeoutError as NatsTimeoutError

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.messaging.command import claim_command, parse_run_command
from src.openship.messaging.exhaustion import (
    InvalidMaxDeliverAdvisory,
    MessageExhaustion,
    UnboundExhaustionSource,
    _call_with_resource,
    _call_without_resource,
    claim_exhaustion_settlement,
    due_exhaustion_observation_ids,
    mark_exhaustion_resolved,
    parse_max_deliver_advisory,
    record_exhaustion_observation,
    record_live_owner_observation,
    record_missing_source_observation,
    refresh_live_owner_observation,
)
from src.openship.messaging.exhaustion_supervisor import ExhaustionSupervisor
from src.openship.messaging.failure import (
    JetStreamSourceIdentity,
    failure_record_exists,
)
from src.openship.messaging.models import MessageTransportState
from src.openship.messaging.outbox import enqueue_run
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.workspace.models import Project

_OBSERVATION_IDS: list[uuid.UUID] = []
_ADVISORY_SUBJECT = "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_RUNS.run-v1"


@pytest.fixture(autouse=True)
def cleanup_observations():
    yield
    if not _OBSERVATION_IDS:
        return
    with SessionLocal() as db:
        db.query(MessageExhaustion).filter(
            MessageExhaustion.id.in_(_OBSERVATION_IDS)
        ).delete(synchronize_session=False)
        db.commit()
    _OBSERVATION_IDS.clear()


def _advisory_body(**changes):
    value = {
        "type": "io.nats.jetstream.advisory.v1.max_deliver",
        "id": "nuid-123456789",
        "timestamp": datetime.now(UTC).isoformat(),
        "stream": "OS_RUNS",
        "consumer": "run-v1",
        "stream_seq": 42,
        "deliveries": 10,
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode()


def test_max_deliver_advisory_requires_exact_subject_and_bounded_schema():
    parsed = parse_max_deliver_advisory(_ADVISORY_SUBJECT, _advisory_body())
    assert parsed.source_sequence == 42
    assert parsed.delivery_count == 10

    with pytest.raises(InvalidMaxDeliverAdvisory):
        parse_max_deliver_advisory(
            "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_EVENTS.run-v1",
            _advisory_body(),
        )
    with pytest.raises(InvalidMaxDeliverAdvisory):
        parse_max_deliver_advisory(
            _ADVISORY_SUBJECT,
            _advisory_body(stream_seq=0),
        )
    with pytest.raises(InvalidMaxDeliverAdvisory):
        parse_max_deliver_advisory(
            _ADVISORY_SUBJECT,
            _advisory_body(extra="not in the v1 schema"),
        )


def _create_claimed_run():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        state.active = True
        state.execution_open = True
        user = User(
            username=f"exhaustion_{uuid.uuid4().hex[:12]}",
            email=f"exhaustion_{uuid.uuid4().hex}@example.test",
            password_hash="test",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Exhaustion test")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id, project_id=project.id, title="Exhaustion test"
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
        outbox_id = enqueue_run(db, run.id, 1, epoch=state.transport_epoch)
        db.commit()

        row = db.get(Outbox, outbox_id)
        command_body = {
            "schema_version": 1,
            "message_id": str(row.message_id),
            "kind": "run.execute",
            "installation_id": str(state.installation_id),
            "transport_epoch": str(row.transport_epoch),
            "correlation_id": str(run.id),
            "causation_id": str(run.id),
            "source_service": "openship",
            "created_at": row.created_at.replace(tzinfo=UTC).isoformat()
            if row.created_at.tzinfo is None
            else row.created_at.astimezone(UTC).isoformat(),
            "subject": row.subject,
            "project_id": str(project.id),
            "conversation_id": str(conversation.id),
            "run_id": str(run.id),
            "dispatch_generation": 1,
        }
        body = json.dumps(command_body).encode()
        subject = row.subject
        decision = claim_command(
            db,
            body,
            subject=subject,
            worker_id="live-owner",
            lease_seconds=60,
        )
        assert decision.kind == "claimed"
        command = parse_run_command(
            body, subject=subject, installation_id=state.installation_id
        )
        return state.installation_id, body, command


def test_live_owner_observation_is_durable_and_delivery_count_is_diagnostic():
    installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 8821, datetime.now(UTC))

    with SessionLocal() as db:
        first = record_live_owner_observation(
            db,
            command,
            body=body,
            source_identity=source,
            delivery_count=10,
            checked_at=datetime.now(UTC),
        )
    assert first.state == "watching_live_owner"
    _OBSERVATION_IDS.append(first.id)

    with SessionLocal() as db:
        again = record_live_owner_observation(
            db,
            command,
            body=body,
            source_identity=source,
            delivery_count=100,
            checked_at=datetime.now(UTC),
        )
        persisted = db.get(MessageExhaustion, first.id)
        assert again.id == first.id
        assert persisted.installation_id == installation_id
        assert persisted.source_transport_epoch == command.transport_epoch
        assert persisted.source_sequence == source.sequence
        assert persisted.state == "watching_live_owner"
        assert persisted.delivery_count == 100
        assert persisted.message_id == command.message_id
        assert persisted.run_id == command.run_id
        assert persisted.dispatch_generation == command.dispatch_generation
        assert persisted.owner_id == "live-owner"
        assert persisted.owner_fence > 0
        assert persisted.lease_until is not None
        assert persisted.next_check_at is not None
        assert persisted.payload_digest == command.body_digest


def test_capacity_scan_records_unknown_delivery_count_and_operator_provenance():
    _installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 8822, datetime.now(UTC))

    with SessionLocal() as db:
        receipt = record_exhaustion_observation(
            db,
            body=body,
            subject=command.subject,
            source_identity=source,
            delivery_count=0,
            discovery_method="capacity_scan",
            recovery_operator="oncall@example.test",
        )
        persisted = db.get(MessageExhaustion, receipt.id)

    assert receipt.state == "watching_live_owner"
    assert persisted.delivery_count == 0
    assert persisted.discovery_method == "capacity_scan"
    assert persisted.recovery_operator == "oncall@example.test"
    _OBSERVATION_IDS.append(receipt.id)

    with SessionLocal() as db:
        updated = record_exhaustion_observation(
            db,
            body=body,
            subject=command.subject,
            source_identity=source,
            delivery_count=10,
            advisory_sequence=source.sequence,
        )
        persisted = db.get(MessageExhaustion, receipt.id)

    assert updated.id == receipt.id
    assert persisted.delivery_count == 10
    assert persisted.discovery_method == "capacity_scan"
    assert persisted.recovery_operator == "oncall@example.test"


def test_capacity_scan_requires_unknown_count_and_operator_identity():
    _installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 8823, datetime.now(UTC))

    with SessionLocal() as db:
        assert (
            record_exhaustion_observation(
                db,
                body=body,
                subject=command.subject,
                source_identity=source,
                delivery_count=0,
            )
            is None
        )

    with SessionLocal() as db:
        assert (
            record_exhaustion_observation(
                db,
                body=body,
                subject=command.subject,
                source_identity=source,
                delivery_count=0,
                discovery_method="capacity_scan",
            )
            is None
        )


@pytest.mark.asyncio
async def test_capacity_recovery_records_operator_and_keeps_live_owner_source():
    _installation_id, body, command = _create_claimed_run()
    source_time = datetime.now(UTC)
    raw_message = SimpleNamespace(
        seq=8824,
        time=source_time,
        subject=command.subject,
        data=body,
    )

    class Client:
        def jetstream(self, *, timeout):
            assert timeout == 2.0
            return SimpleNamespace()

    supervisor = ExhaustionSupervisor(
        Client(),
        installation_id=command.installation_id,
        session_factory=SessionLocal,
    )

    async def run_sync(function, *args, **kwargs):
        with SessionLocal() as db:
            return function(db, *args, **kwargs)

    supervisor._run_sync = run_sync
    result = await supervisor.reconcile_capacity_candidate(
        raw_message, operator="oncall@example.test"
    )

    assert result == "watching_live_owner"
    with SessionLocal() as db:
        observation = (
            db.query(MessageExhaustion).filter_by(source_sequence=raw_message.seq).one()
        )
        assert observation.discovery_method == "capacity_scan"
        assert observation.delivery_count == 0
        assert observation.recovery_operator == "oncall@example.test"
        assert observation.state == "watching_live_owner"
        _OBSERVATION_IDS.append(observation.id)


def _record_observation():
    _installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity(
        "OS_RUNS", uuid.uuid4().int % (2**63 - 1) + 1, datetime.now(UTC)
    )
    with SessionLocal() as db:
        receipt = record_live_owner_observation(
            db,
            command,
            body=body,
            source_identity=source,
            delivery_count=10,
        )
    assert receipt is not None
    _OBSERVATION_IDS.append(receipt.id)
    return receipt.id, command


def test_live_owner_recovery_waits_until_committed_terminal_outcome():
    observation_id, command = _record_observation()
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "completed"
        job.owner_id = None
        job.lease_until = None
        db.commit()

    with SessionLocal() as db:
        receipt = refresh_live_owner_observation(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        assert receipt.state == "settling"
        persisted = db.get(MessageExhaustion, observation_id)
        assert persisted.resolved_at is None
        assert persisted.next_check_at is None


def test_live_owner_recovery_tracks_a_newer_fenced_owner():
    observation_id, command = _record_observation()
    with SessionLocal() as db:
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        job.owner_id = "replacement-owner"
        job.fence += 1
        job.lease_until = datetime.now(UTC) + timedelta(minutes=1)
        run = db.get(Run, command.run_id)
        run.fence = job.fence
        db.commit()

    with SessionLocal() as db:
        receipt = refresh_live_owner_observation(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        persisted = db.get(MessageExhaustion, observation_id)
        assert receipt.state == "watching_live_owner"
        assert persisted.owner_id == "replacement-owner"
        assert persisted.owner_fence == 2
        assert persisted.next_check_at is not None


def test_uncertain_provider_dispatch_blocks_automatic_generation_replay():
    observation_id, command = _record_observation()
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "queued"
        run.provider_started_at = datetime.now(UTC)
        job.owner_id = None
        job.lease_until = None
        db.commit()

    with SessionLocal() as db:
        receipt = refresh_live_owner_observation(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        assert receipt.state == "blocked"
        assert db.get(MessageExhaustion, observation_id).error_code == (
            "dispatch_uncertain"
        )
        assert job.dispatch_generation == command.dispatch_generation


def test_settlement_claim_is_leased_and_restart_resumable():
    observation_id, command = _record_observation()
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "completed"
        job.owner_id = None
        job.lease_until = None
        db.commit()

    with SessionLocal() as db:
        receipt = refresh_live_owner_observation(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        assert receipt.state == "settling"
        assert observation_id in due_exhaustion_observation_ids(
            db, checked_at=datetime.now(UTC) + timedelta(seconds=15)
        )

    with SessionLocal() as db:
        claim = claim_exhaustion_settlement(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        assert claim is not None
        assert claim.source_sequence > 0
        assert claim.payload_digest is not None
        assert claim.installation_id

    with SessionLocal() as db:
        assert (
            claim_exhaustion_settlement(
                db,
                observation_id,
                checked_at=datetime.now(UTC) + timedelta(seconds=15),
            )
            is None
        )

    with SessionLocal() as db:
        assert mark_exhaustion_resolved(
            db,
            observation_id,
            resolved_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        assert db.get(MessageExhaustion, observation_id).state == "resolved"
        assert observation_id not in due_exhaustion_observation_ids(
            db, checked_at=datetime.now(UTC) + timedelta(minutes=1)
        )


@pytest.mark.parametrize(
    ("terminal_state", "error_code"),
    [("resolved", None), ("blocked", "source_binding_mismatch")],
)
def test_replayed_missing_source_advisory_preserves_terminal_observation(
    terminal_state, error_code
):
    source_sequence = 99104 if terminal_state == "resolved" else 99105
    with SessionLocal() as db:
        created = record_missing_source_observation(
            db,
            source_sequence=source_sequence,
            delivery_count=10,
        )
    assert created is not None
    _OBSERVATION_IDS.append(created.id)

    resolved_at = datetime.now(UTC) if terminal_state == "resolved" else None
    with SessionLocal() as db:
        observation = db.get(MessageExhaustion, created.id)
        observation.state = terminal_state
        observation.error_code = error_code
        observation.resolved_at = resolved_at
        db.commit()

    with SessionLocal() as db:
        replayed = record_missing_source_observation(
            db,
            source_sequence=source_sequence,
            delivery_count=11,
        )
        persisted = db.get(MessageExhaustion, created.id)
        assert replayed.state == terminal_state
        assert persisted.state == terminal_state
        assert persisted.error_code == error_code
        if resolved_at is not None:
            assert persisted.resolved_at.replace(tzinfo=UTC) == resolved_at
        assert persisted.delivery_count == 11


def test_database_adapters_always_release_open_transactions():
    class ThreadOwnedResource:
        transaction_open = False

        def rollback(self):
            self.transaction_open = False

    resource = ThreadOwnedResource()

    def database_operation(db):
        db.transaction_open = True
        return "database-result"

    def relay_operation(result):
        return result

    assert (
        _call_with_resource(resource, database_operation, (), {}) == "database-result"
    )
    assert not resource.transaction_open
    assert (
        _call_without_resource(resource, relay_operation, ("relay-result",), {})
        == "relay-result"
    )
    assert not resource.transaction_open


@pytest.mark.asyncio
async def test_observation_database_failure_retries_without_quarantining_source():
    installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 99103, datetime.now(UTC))
    raw_message = SimpleNamespace(
        seq=source.sequence,
        time=source.timestamp,
        subject=command.subject,
        data=body,
    )
    nak_codes = []
    handoffs = []
    checked_functions = []
    observation_outcome = None

    class JetStream:
        async def get_msg(self, stream, *, seq):
            assert stream == "OS_RUNS" and seq == source.sequence
            return raw_message

    class Client:
        def jetstream(self, *, timeout):
            assert timeout == 2.0
            return JetStream()

    supervisor = ExhaustionSupervisor(
        Client(), installation_id=installation_id, session_factory=SessionLocal
    )

    async def run_sync(function, *args, **kwargs):
        checked_functions.append(function)
        if function is failure_record_exists:
            return False
        assert function is record_exhaustion_observation
        return observation_outcome

    async def no_failure_handoff(_source):
        return None

    async def record_nak(_message, code):
        nak_codes.append(code)

    async def record_unbound_handoff(*_args):
        handoffs.append(True)

    supervisor._run_sync = run_sync
    supervisor._load_failure_handoff = no_failure_handoff
    supervisor._nak_advisory = record_nak
    supervisor._handoff_unbound_source = record_unbound_handoff

    advisory_message = SimpleNamespace(
        subject=_ADVISORY_SUBJECT,
        data=_advisory_body(stream_seq=source.sequence),
    )
    await supervisor._handle_advisory(advisory_message)

    assert checked_functions == [failure_record_exists, record_exhaustion_observation]
    assert nak_codes == ["observation_unavailable"]
    assert handoffs == []

    observation_outcome = UnboundExhaustionSource()
    checked_functions.clear()
    nak_codes.clear()
    await supervisor._handle_advisory(advisory_message)
    assert checked_functions == [failure_record_exists, record_exhaustion_observation]
    assert nak_codes == []
    assert handoffs == [True]


@pytest.mark.asyncio
async def test_supervisor_commits_settlement_before_exact_delete_and_advisory_ack():
    installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 99106, datetime.now(UTC))
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "completed"
        job.owner_id = None
        job.lease_until = None
        db.commit()

    raw_message = SimpleNamespace(
        seq=source.sequence,
        time=source.timestamp,
        subject=command.subject,
        data=body,
    )
    events = []

    class JetStream:
        deleted = False

        async def get_msg(self, stream, *, seq):
            assert stream == "OS_RUNS" and seq == source.sequence
            assert not self.deleted
            return raw_message

        async def delete_msg(self, stream, sequence):
            assert stream == "OS_RUNS" and sequence == source.sequence
            with SessionLocal() as db:
                observation = (
                    db.query(MessageExhaustion)
                    .filter_by(source_sequence=source.sequence)
                    .one()
                )
                assert observation.state == "settling"
                assert observation.payload_digest == command.body_digest
            self.deleted = True
            events.append("source_deleted")
            return True

    class Client:
        def __init__(self):
            self.js = JetStream()

        def jetstream(self, *, timeout):
            assert timeout == 2.0
            return self.js

    supervisor = ExhaustionSupervisor(
        Client(), installation_id=installation_id, session_factory=SessionLocal
    )

    async def run_sync(function, *args, **kwargs):
        with SessionLocal() as db:
            return _call_with_resource(db, function, args, kwargs)

    supervisor._run_sync = run_sync

    class AdvisoryMessage:
        subject = _ADVISORY_SUBJECT
        data = _advisory_body(stream_seq=source.sequence)

        async def ack_sync(self, *, timeout):
            assert timeout == 2.0
            assert supervisor.js.deleted
            with SessionLocal() as db:
                observation = (
                    db.query(MessageExhaustion)
                    .filter_by(source_sequence=source.sequence)
                    .one()
                )
                assert observation.state == "resolved"
            events.append("advisory_acked")

        async def nak(self, *, delay):
            pytest.fail(f"unexpected advisory NAK with delay {delay}")

    await supervisor._handle_advisory(AdvisoryMessage())

    with SessionLocal() as db:
        observation = (
            db.query(MessageExhaustion).filter_by(source_sequence=source.sequence).one()
        )
        _OBSERVATION_IDS.append(observation.id)
        assert observation.state == "resolved"
    assert events == ["source_deleted", "advisory_acked"]


@pytest.mark.asyncio
async def test_supervisor_binds_durable_before_signaling_ready(monkeypatch):
    from src.openship.messaging import executor, topology

    bindings = []
    unsubscribed = []

    class Subscription:
        async def fetch(self, *, batch, timeout):
            assert batch == 1 and timeout == 1.0
            await asyncio.sleep(0.01)
            raise NatsTimeoutError

        async def unsubscribe(self):
            unsubscribed.append(True)

    class JetStream:
        async def pull_subscribe_bind(self, **kwargs):
            bindings.append(kwargs)
            return Subscription()

    class Client:
        is_connected = True

        def __init__(self):
            self.stats = {"reconnects": 0}
            self.js = JetStream()

        def jetstream(self, *, timeout):
            assert timeout == 2.0
            return self.js

        async def flush(self, *, timeout):
            assert timeout == 2.0

    async def ready_topology(**kwargs):
        assert kwargs["apply"] is False
        return SimpleNamespace(ready=True)

    class NoopPool:
        def __init__(self, _size, _session_factory):
            pass

        async def close(self):
            return None

    monkeypatch.setattr(topology, "ensure_topology", ready_topology)
    monkeypatch.setattr(executor, "ExecutionPool", NoopPool)
    supervisor = ExhaustionSupervisor(
        Client(),
        installation_id=uuid.uuid4(),
        session_factory=SessionLocal,
        poll_interval=60,
    )

    async def no_pending_observations():
        return None

    supervisor._recover_observations = no_pending_observations
    stop, started = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(supervisor.run(stop, started=started))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert bindings == [
            {
                "stream": "OS_ADVISORIES",
                "durable": "exhaustion-v1",
                "pending_msgs_limit": 1,
                "pending_bytes_limit": 4096,
            }
        ]
        assert not task.done()
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)
    assert unsubscribed == [True]


def test_expired_unstarted_live_owner_uses_existing_safe_requeue():
    observation_id, command = _record_observation()
    with SessionLocal() as db:
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        job.lease_until = datetime.now(UTC) - timedelta(seconds=5)
        db.commit()

    with SessionLocal() as db:
        receipt = refresh_live_owner_observation(
            db,
            observation_id,
            checked_at=datetime.now(UTC) + timedelta(seconds=15),
        )
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        assert receipt.state == "settling"
        assert run.status == "queued"
        assert job.dispatch_generation == command.dispatch_generation + 1
        assert job.owner_id is None and job.lease_until is None
        replacement = (
            db.query(Outbox)
            .filter_by(
                run_id=command.run_id, dispatch_generation=job.dispatch_generation
            )
            .one()
        )
        assert replacement.destination == "command"
        assert db.get(MessageExhaustion, observation_id).state == "settling"


def test_exhaustion_delivery_count_does_not_override_committed_terminal_state():
    _, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 99101, datetime.now(UTC))
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "completed"
        job.owner_id = None
        job.lease_until = None
        db.commit()

    with SessionLocal() as db:
        receipt = record_exhaustion_observation(
            db,
            body=body,
            subject=command.subject,
            source_identity=source,
            delivery_count=10,
            advisory_sequence=source.sequence,
        )
    assert receipt is not None
    assert receipt.state == "settling"
    _OBSERVATION_IDS.append(receipt.id)


def test_unclaimed_queued_generation_is_atomically_requeued_after_exhaustion():
    installation_id, body, command = _create_claimed_run()
    source = JetStreamSourceIdentity("OS_RUNS", 99102, datetime.now(UTC))
    with SessionLocal() as db:
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        run.status = "queued"
        job.owner_id = None
        job.lease_until = None
        db.commit()

    with SessionLocal() as db:
        receipt = record_exhaustion_observation(
            db,
            body=body,
            subject=command.subject,
            source_identity=source,
            delivery_count=10,
            advisory_sequence=source.sequence,
        )
        assert receipt is not None
        assert receipt.state == "settling"
        run = db.get(Run, command.run_id)
        job = db.query(RunJob).filter_by(run_id=command.run_id).one()
        assert job.dispatch_generation == command.dispatch_generation + 1
        replacement = (
            db.query(Outbox)
            .filter_by(
                run_id=command.run_id, dispatch_generation=job.dispatch_generation
            )
            .one()
        )
        assert replacement.transport_epoch == command.transport_epoch
        assert replacement.message_id == uuid.uuid5(
            installation_id,
            f"run.execute:{command.run_id}:{job.dispatch_generation}:{command.transport_epoch}",
        )
        assert run.status == "queued"
    _OBSERVATION_IDS.append(receipt.id)
