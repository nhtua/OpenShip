"""Lifecycle integration for parked task decisions."""

from datetime import UTC, datetime, timedelta

import pytest

from src.openship.messaging.recovery import _RecoveryResource
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.service import request_cancel
from src.openship.runs.tasks.decisions import park_task_wait
from src.openship.runs.tasks.models import TaskDecisionRecord, TaskRecord
from tests.conftest import TestSessionLocal
from tests.test_messaging_command_claim import _body, _command
from tests.test_messaging_consumer import _prepare, _settle
from tests.test_task_state import _identity


def test_exhausted_parked_delivery_settles_without_requeue():
    from src.openship.messaging.exhaustion import record_exhaustion_observation
    from src.openship.messaging.failure import JetStreamSourceIdentity

    with TestSessionLocal() as db:
        run_id, _, row_id = _command(db)
        db.get(Run, run_id).graph_version = 5
        db.commit()
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with TestSessionLocal() as execution:
            park_task_wait(
                execution,
                prepared.decision.claim,
                kind="clarification",
                prompt="Question?",
                now=datetime.now(UTC),
            )
            execution.commit()
        receipt = record_exhaustion_observation(
            db,
            body=body,
            subject=subject,
            source_identity=JetStreamSourceIdentity(
                "OS_RUNS", 99181, datetime.now(UTC)
            ),
            delivery_count=10,
        )
        assert receipt.state == "settling"
        assert db.get(Run, run_id).status == "waiting_input"
        assert (
            db.query(Outbox).filter_by(run_id=run_id, destination="command").count()
            == 1
        )


def test_cutover_preserves_durable_task_wait():
    from src.openship.messaging.migration import _classify_runs
    from src.openship.messaging.models import MessageTransportState

    with TestSessionLocal() as db:
        _, _, _, run, claim = _identity(db)
        park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=datetime.now(UTC)
        )
        db.commit()
        counts, blocked, _, _ = _classify_runs(
            db,
            schema_stage="phase6a",
            state=db.get(MessageTransportState, 1),
            only_run_ids=[run.id],
            lock_rows=True,
        )
        assert blocked == 0
        assert counts == {"waiting_preserved": 1}


def test_worker_never_starts_graph_for_parked_claim(monkeypatch):
    from src.openship.runs import worker

    with TestSessionLocal() as db:
        _, _, _, _, claim = _identity(db)
        park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=datetime.now(UTC)
        )
        db.commit()
    instance = object.__new__(worker.Worker)
    instance.worker_id = "stale-worker"
    instance.Session = TestSessionLocal
    called = []

    def forbidden(*args):
        called.append(True)
        raise AssertionError("parked claim reached graph resolution")

    monkeypatch.setattr(worker, "resolve_graph", forbidden)
    assert instance.process_job(claim) is False
    assert not called


def test_decision_event_rejects_invalid_payload():
    import pytest
    from pydantic import ValidationError

    from src.openship.events.service import append_event

    with TestSessionLocal() as db:
        _, _, conversation, run, _ = _identity(db)
        with pytest.raises(ValidationError):
            append_event(
                db, conversation.id, run.id, "task.decision_resolved", {"state": "open"}
            )


def test_wait_ack_releases_slot_without_terminal_message():
    with TestSessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        db.get(Run, run_id).graph_version = 5
        db.commit()
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with TestSessionLocal() as execution:
            park_task_wait(
                execution,
                prepared.decision.claim,
                kind="clarification",
                prompt="Question?",
                now=datetime.now(UTC),
            )
            execution.commit()
        result = _settle(db, prepared)
        assert (result.action, result.code) == ("ack", "task_wait")
        assert db.get(Run, run_id).ended_at is None
        assert db.get(RunJob, job_id).owner_id is None


def test_stale_delivery_cannot_claim_waiting_task():
    with TestSessionLocal() as db:
        run_id, _, row_id = _command(db)
        db.get(Run, run_id).graph_version = 5
        db.commit()
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with TestSessionLocal() as execution:
            park_task_wait(
                execution,
                prepared.decision.claim,
                kind="clarification",
                prompt="Question?",
                now=datetime.now(UTC),
            )
            execution.commit()
        # A stale, independently identified command must not acquire the parked run.
        from src.openship.runs.queue import claim_run

        result = claim_run(
            db,
            run_id=run_id,
            generation=prepared.command.dispatch_generation,
            epoch=prepared.command.transport_epoch,
            worker_id="stale",
            lease_seconds=30,
        )
        assert result.kind == "disposed"


def test_cancel_supersedes_pending_decision():
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        request = park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=datetime.now(UTC)
        ).pending_decision
        db.commit()
        request_cancel(db, user.id, str(run.id))
        decision = (
            db.query(TaskDecisionRecord).filter_by(request_id=request.request_id).one()
        )
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        assert decision.state == "cancelled"
        assert task.status == "cancelled"
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 0
        )


def test_expiry_runs_without_worker_slot():
    with TestSessionLocal() as db:
        _, _, _, run, claim = _identity(db)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=now
        ).pending_decision
        decision = (
            db.query(TaskDecisionRecord).filter_by(request_id=request.request_id).one()
        )
        decision.expires_at = now - timedelta(seconds=1)
        run_id = run.id
        db.commit()
    _RecoveryResource("maintenance", TestSessionLocal).expire_task_decisions()
    with TestSessionLocal() as db:
        decision = db.query(TaskDecisionRecord).filter_by(run_id=run_id).one()
        assert decision.state == "expired"


def test_task_decision_http_commits_owned_choice(client):
    import uuid
    from types import SimpleNamespace

    from src.openship.auth.routes import require_jwt

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        request = park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=datetime.now(UTC)
        ).pending_decision
        user_id, run_id = user.id, run.id
        db.commit()
    client.app.dependency_overrides[require_jwt] = lambda: SimpleNamespace(id=user_id)
    try:
        response = client.post(
            f"/api/runs/{run_id}/task-decisions",
            json={
                "client_request_id": str(uuid.uuid4()),
                "request_id": str(request.request_id),
                "plan_revision": request.plan_revision,
                "expected_digest": request.expected_digest,
                "action": "answer",
                "answer": "Answer",
            },
        )
        # Unpinned test runs cannot resume; the route must preserve the service's conflict.
        assert response.status_code == 409
    finally:
        client.app.dependency_overrides.pop(require_jwt, None)


def test_http_reject_commits_replays_and_hides_from_other_user(client):
    import uuid
    from types import SimpleNamespace

    from src.openship.auth.routes import require_jwt
    from src.openship.runs.tasks.state import save_plan
    from tests.test_task_state import _plan

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        request = park_task_wait(
            db,
            claim,
            kind="plan_confirmation",
            prompt="Approve?",
            now=datetime.now(UTC),
        ).pending_decision
        user_id, run_id = user.id, run.id
        db.commit()
    payload = {
        "client_request_id": str(uuid.uuid4()),
        "request_id": str(request.request_id),
        "plan_revision": request.plan_revision,
        "expected_digest": request.expected_digest,
        "action": "reject",
    }
    try:
        client.app.dependency_overrides[require_jwt] = lambda: SimpleNamespace(
            id=uuid.uuid4()
        )
        assert (
            client.post(f"/api/runs/{run_id}/task-decisions", json=payload).status_code
            == 404
        )
        client.app.dependency_overrides[require_jwt] = lambda: SimpleNamespace(
            id=user_id
        )
        first = client.post(f"/api/runs/{run_id}/task-decisions", json=payload)
        assert first.status_code == 200
        assert first.json()["status"] == "cancelled"
        assert (
            client.post(f"/api/runs/{run_id}/task-decisions", json=payload).json()
            == first.json()
        )
        with TestSessionLocal() as db:
            assert (
                db.query(TaskDecisionRecord)
                .filter_by(request_id=request.request_id)
                .one()
                .state
                == "rejected"
            )
            assert (
                db.query(Outbox).filter_by(run_id=run_id, destination="command").count()
                == 0
            )
    finally:
        client.app.dependency_overrides.pop(require_jwt, None)


def test_http_confirmation_commits_one_outbox_intent(client, monkeypatch):
    import uuid
    from types import SimpleNamespace

    from src.openship.auth.routes import require_jwt
    from src.openship.config import settings
    from src.openship.runs.tasks.state import save_plan
    from src.openship.sandboxes.profiles import reset_runtime_catalog
    from tests.test_task_decisions import _pinned_execution
    from tests.test_task_state import _plan

    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    try:
        with TestSessionLocal() as db:
            user, project, conversation, run, claim = _identity(db)
            _pinned_execution(run, user, project, conversation)
            db.flush()
            save_plan(db, claim, _plan(), expected_revision=0)
            request = park_task_wait(
                db,
                claim,
                kind="plan_confirmation",
                prompt="Approve?",
                now=datetime.now(UTC),
            ).pending_decision
            user_id, run_id = user.id, run.id
            db.commit()
        payload = {
            "client_request_id": str(uuid.uuid4()),
            "request_id": str(request.request_id),
            "plan_revision": request.plan_revision,
            "expected_digest": request.expected_digest,
            "action": "confirm",
        }
        client.app.dependency_overrides[require_jwt] = lambda: SimpleNamespace(
            id=user_id
        )
        first = client.post(f"/api/runs/{run_id}/task-decisions", json=payload)
        assert first.status_code == 200
        assert (
            client.post(f"/api/runs/{run_id}/task-decisions", json=payload).json()
            == first.json()
        )
        with TestSessionLocal() as db:
            assert db.get(Run, run_id).status == "queued"
            assert (
                db.query(Outbox).filter_by(run_id=run_id, destination="command").count()
                == 1
            )
    finally:
        reset_runtime_catalog()
        client.app.dependency_overrides.pop(require_jwt, None)


def test_incomplete_parking_never_authorizes_delivery_ack():
    with TestSessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        prepared = _prepare(db, body, subject)
        with TestSessionLocal() as execution:
            run = execution.get(Run, run_id)
            job = execution.get(RunJob, job_id)
            run.status = "waiting_input"
            run.fence += 1
            job.fence = run.fence
            job.owner_id = job.lease_until = None
            execution.commit()
        disposition = _settle(db, prepared)
        assert (disposition.action, disposition.code) == (
            "reconcile",
            "task_wait_incomplete",
        )


def test_unclaimed_incomplete_wait_is_not_acknowledged():
    with TestSessionLocal() as db:
        run_id, _, row_id = _command(db)
        db.get(Run, run_id).status = "waiting_input"
        db.commit()
        body, subject = _body(db, row_id)
        result = _prepare(db, body, subject)
        assert result.disposition.action == "reconcile"


@pytest.mark.asyncio
async def test_parked_delivery_returns_execution_pool_capacity():
    import asyncio

    from src.openship.messaging.executor import ExecutionPool

    def park(db, claim):
        park_task_wait(
            db, claim, kind="clarification", prompt="Question?", now=datetime.now(UTC)
        )
        db.commit()

    with TestSessionLocal() as db:
        _, _, _, _, claim = _identity(db)
        db.commit()
    pool = ExecutionPool(1, TestSessionLocal)
    try:
        async with pool.reserve() as slot:
            await slot.run(park, claim)
        async with asyncio.timeout(1):
            async with pool.reserve() as slot:
                assert await slot.run(lambda db: db.is_active)
    finally:
        await pool.close()
