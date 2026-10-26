"""Delivery ACKs require durable database outcomes, never Worker booleans."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from src.openship.database.session import SessionLocal
from src.openship.messaging.models import MessageDisposition, MessageTransportState
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


@pytest.mark.parametrize("state", ["live", "future", "gate", "expired", "obsolete"])
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
        elif state == "gate":
            db.get(MessageTransportState, 1).execution_open = False
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
