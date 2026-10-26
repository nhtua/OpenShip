"""A broker command can acquire only its persisted, due run."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.messaging.command import (
    InvalidCommandEnvelope,
    claim_command,
    parse_run_command,
)
from src.openship.messaging.models import MessageDisposition, MessageTransportState
from src.openship.messaging.outbox import enqueue_run
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import claim_run
from src.openship.workspace.models import Project


def _command(db):
    state = db.get(MessageTransportState, 1)
    state.execution_open = True
    user = User(
        username=f"claim_{uuid.uuid4().hex[:12]}",
        email=f"claim_{uuid.uuid4().hex}@example.test",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Claim test")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="Claim test"
    )
    db.add(conversation)
    db.flush()
    run = Run(
        conversation_id=conversation.id, project_id=project.id, status="queued", fence=0
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
    db.commit()
    return run.id, job.id, row_id


def _body(db, row_id):
    row = db.get(Outbox, row_id)
    state = db.get(MessageTransportState, 1)
    body = {
        "schema_version": 1,
        "message_id": str(row.message_id),
        "kind": "run.execute",
        "installation_id": str(state.installation_id),
        "transport_epoch": str(row.transport_epoch),
        "correlation_id": str(row.run_id),
        "causation_id": str(row.run_id),
        "source_service": "openship",
        "created_at": row.created_at.replace(tzinfo=UTC).isoformat()
        if row.created_at.tzinfo is None
        else row.created_at.astimezone(UTC).isoformat(),
        "subject": row.subject,
        "project_id": str(db.get(Run, row.run_id).project_id),
        "conversation_id": str(row.conversation_id),
        "run_id": str(row.run_id),
        "dispatch_generation": row.dispatch_generation,
    }
    return json.dumps(body).encode(), row.subject


def test_exact_due_claim_increments_fence_once_and_does_not_steal_another_run():
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        other_run_id, other_job_id, _ = _command(db)
        body, subject = _body(db, row_id)
        decision = claim_command(
            db, body, subject=subject, worker_id="worker-a", lease_seconds=30
        )
        assert decision.kind == "claimed"
        assert decision.claim.run_id == run_id
        assert decision.claim.fence == 1
        assert db.get(Run, run_id).status == "running"
        assert db.get(RunJob, job_id).owner_id == "worker-a"
        assert db.get(Run, other_run_id).status == "queued"
        assert db.get(RunJob, other_job_id).fence == 0
        assert (
            claim_run(
                db,
                run_id=run_id,
                generation=1,
                epoch=db.get(MessageTransportState, 1).transport_epoch,
                worker_id="worker-b",
                lease_seconds=30,
            ).kind
            == "defer"
        )


@pytest.mark.parametrize(
    "mutation",
    ["message_id", "dispatch_generation", "transport_epoch", "subject", "destination"],
)
def test_persisted_identity_mismatch_never_claims(mutation):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        row = db.get(Outbox, row_id)
        if mutation == "message_id":
            row.message_id = uuid.uuid4()
        elif mutation == "dispatch_generation":
            row.dispatch_generation = 2
        elif mutation == "transport_epoch":
            row.transport_epoch = uuid.uuid4()
        elif mutation == "subject":
            row.subject = "openship.wrong.v1.command.run.execute"
        else:
            row.destination = "event"
        db.commit()
        assert (
            claim_command(
                db, body, subject=subject, worker_id="worker", lease_seconds=30
            ).kind
            == "disposed"
        )
        assert db.get(Run, run_id).status == "queued"
        assert db.get(RunJob, job_id).fence == 0


@pytest.mark.parametrize(
    "status,expected", [("completed", "disposed"), ("waiting_connector", "disposed")]
)
def test_non_executable_run_is_not_claimed(status, expected):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        db.get(Run, run_id).status = status
        db.commit()
        body, subject = _body(db, row_id)
        assert (
            claim_command(
                db, body, subject=subject, worker_id="worker", lease_seconds=30
            ).kind
            == expected
        )
        assert db.get(RunJob, job_id).fence == 0


def test_obsolete_generation_disposes_without_claim():
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        db.get(RunJob, job_id).dispatch_generation = 2
        db.commit()
        body, subject = _body(db, row_id)
        assert (
            claim_command(
                db, body, subject=subject, worker_id="worker", lease_seconds=30
            ).kind
            == "disposed"
        )
        assert db.get(Run, run_id).status == "queued"


@pytest.mark.parametrize(
    "condition", ["future", "gate", "live", "expired", "ownerless_live"]
)
def test_unavailable_run_never_changes_owner_or_fence(condition):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        job = db.get(RunJob, job_id)
        if condition == "future":
            job.available_at = datetime.now(UTC) + timedelta(minutes=5)
        elif condition == "gate":
            db.get(MessageTransportState, 1).execution_open = False
        elif condition == "ownerless_live":
            job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
        else:
            db.get(Run, run_id).status = "running"
            job.owner_id = "other"
            job.lease_until = datetime.now(UTC) + timedelta(
                minutes=5 if condition == "live" else -5
            )
        db.commit()
        before = (job.owner_id, job.lease_until, job.fence)
        body, subject = _body(db, row_id)
        decision = claim_command(
            db, body, subject=subject, worker_id="worker", lease_seconds=30
        )
        assert decision.kind == (
            "reconcile_required" if condition == "expired" else "defer"
        )
        db.refresh(job)
        assert (job.owner_id, job.lease_until, job.fence) == before


@pytest.mark.parametrize(
    "change",
    [
        "malformed",
        "deeply_nested",
        "oversize",
        "kind",
        "installation",
        "subject",
        "uuid",
        "generation",
        "schema",
    ],
)
def test_invalid_envelope_has_safe_error(change):
    with SessionLocal() as db:
        _, _, row_id = _command(db)
        body, subject = _body(db, row_id)
        record = json.loads(body)
        if change == "malformed":
            body = b'{"untrusted": "secret-hint"'
        elif change == "deeply_nested":
            body = b"[" * 3000 + b"0" + b"]" * 3000
        elif change == "oversize":
            body = body + b" " * 17000
        else:
            if change == "kind":
                record["kind"] = "delete.everything"
            elif change == "installation":
                record["installation_id"] = str(uuid.uuid4())
            elif change == "subject":
                subject = "openship.wrong.v1.command.run.execute"
            elif change == "uuid":
                record["run_id"] = "secret-hint"
            elif change == "schema":
                record["schema_version"] = 2
            else:
                record["dispatch_generation"] = -1
            body = json.dumps(record).encode()
        with pytest.raises(InvalidCommandEnvelope) as exc:
            parse_run_command(
                body,
                subject=subject,
                installation_id=db.get(MessageTransportState, 1).installation_id,
            )
        assert "secret-hint" not in str(exc.value)


def test_json_decoder_recursion_error_is_a_safe_envelope_rejection(monkeypatch):
    installation_id = uuid.uuid4()

    def exhausted_decoder(*_args, **_kwargs):
        raise RecursionError("secret-hint decoder detail")

    with monkeypatch.context() as patch:
        patch.setattr("src.openship.messaging.command.json.loads", exhausted_decoder)
        with pytest.raises(InvalidCommandEnvelope) as exc:
            parse_run_command(
                b"[]",
                subject=f"openship.{installation_id.hex}.v1.command.run.execute",
                installation_id=installation_id,
            )
    assert str(exc.value) == "invalid_run_command"


@pytest.mark.parametrize(
    "field",
    [
        "conversation_id",
        "project_id",
        "correlation_id",
        "causation_id",
        "source_service",
        "created_at",
    ],
)
def test_identity_bearing_body_field_must_match_persisted_command(field):
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        record = json.loads(body)
        if field == "source_service":
            record[field] = "external-value"
        elif field == "created_at":
            record[field] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        else:
            record[field] = str(uuid.uuid4())
        changed = json.dumps(record, indent=2).encode()
        if field == "source_service":
            with pytest.raises(InvalidCommandEnvelope):
                claim_command(
                    db, changed, subject=subject, worker_id="worker", lease_seconds=30
                )
        else:
            assert (
                claim_command(
                    db, changed, subject=subject, worker_id="worker", lease_seconds=30
                ).kind
                == "disposed"
            )
        assert db.get(Run, run_id).status == "queued"
        assert db.get(RunJob, job_id).fence == 0


def test_changed_current_epoch_disposes_without_claim():
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        record = json.loads(body)
        record["transport_epoch"] = str(uuid.uuid4())
        assert (
            claim_command(
                db,
                json.dumps(record).encode(),
                subject=subject,
                worker_id="worker",
                lease_seconds=30,
            ).kind
            == "disposed"
        )
        assert db.get(Run, run_id).status == "queued"
        assert db.get(RunJob, job_id).fence == 0


def test_same_message_id_with_different_valid_body_digest_is_rejected():
    with SessionLocal() as db:
        run_id, job_id, row_id = _command(db)
        body, subject = _body(db, row_id)
        first = claim_command(
            db, body, subject=subject, worker_id="worker", lease_seconds=30
        )
        assert first.kind == "claimed"
        saved = db.get(MessageDisposition, db.get(Outbox, row_id).message_id)
        assert saved.payload_digest == hashlib.sha256(body).hexdigest()
        record = json.loads(body)
        changed = json.dumps(record, indent=2).encode()
        assert (
            claim_command(
                db, changed, subject=subject, worker_id="other", lease_seconds=30
            ).kind
            == "disposed"
        )
        assert db.get(RunJob, job_id).owner_id == "worker"
        assert db.get(Run, run_id).fence == 1
