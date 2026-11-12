"""Clarification replies remain attached until an assessment resolves them."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.openship.chat.models import Message
from src.openship.runs.inbox.admission import submit_message
from src.openship.runs.inbox.contracts import MessageAdmissionRequest
from src.openship.runs.models import Outbox, RunJob
from src.openship.runs.tasks.contracts import TaskDecisionCommand
from src.openship.runs.tasks.decisions import (
    apply_decision,
    expire_task_decisions,
    park_task_wait,
)
from src.openship.runs.tasks.models import (
    TaskDecisionCommandRecord,
    TaskDecisionRecord,
    TaskRecord,
)
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_state import _identity


def test_multiple_replies_keep_request_open():
    from src.openship.runs.tasks.clarification import clarification_replies

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        decision = park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source?",
            now=datetime.now(UTC),
        ).pending_decision
        db.commit()
        original_generation = (
            db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
        )
        requests = [
            MessageAdmissionRequest(content=text, client_request_id=str(uuid.uuid4()))
            for text in ("Not sure", "Use the morning CSV")
        ]
        first = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[0]
        )
        db.commit()
        second = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[1]
        )
        replay = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[1]
        )
        db.commit()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert first.request_id == second.request_id == decision.request_id
        assert replay.message_id == second.message_id
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 2
        assert (
            db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
            == original_generation + 1
        )
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )


def test_reply_during_worker_claim_remains_bound():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        decision = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        db.commit()
        first = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="first", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        run.fence += 1
        run.status = "running"
        job.fence = run.fence
        job.owner_id = "worker"
        job.lease_until = now + timedelta(minutes=5)
        db.commit()
        second = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="second", client_request_id=str(uuid.uuid4())
            ),
        )
        assert first.request_id == second.request_id == decision.request_id
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_fresh_legacy_answer_cannot_bypass_assessment():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        decision = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=decision.request_id,
            plan_revision=decision.plan_revision,
            expected_digest=decision.expected_digest,
            action="answer",
            answer="morning CSV",
        )
        with pytest.raises(HTTPException) as refused:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert refused.value.status_code == 409
        assert "message" in str(refused.value.detail).lower()
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_historical_answer_replay_remains_readable():
    from src.openship.runs.tasks.decisions import _project

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        record = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="answer",
            answer="morning CSV",
        )
        projection = _project(db, task)
        record.state = "answered"
        db.add(
            TaskDecisionCommandRecord(
                task_id=task.id,
                run_id=run.id,
                decision_id=record.id,
                client_request_id=command.client_request_id,
                request_id=command.request_id,
                action=command.action,
                plan_revision=command.plan_revision,
                expected_digest=command.expected_digest,
                answer=command.answer,
                result_json=projection.model_dump_json(),
            )
        )
        db.commit()
        replay = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert replay == projection
        assert record.state == "answered"


def test_typed_yes_does_not_confirm_plan():
    from src.openship.runs.tasks.state import save_plan
    from tests.test_task_state import _plan

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        park_task_wait(
            db,
            claim,
            kind="plan_confirmation",
            prompt="Approve?",
            now=datetime.now(UTC),
        )
        db.commit()
        reply = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="yes", client_request_id=str(uuid.uuid4())
            ),
        )
        assert reply.disposition == "queued"
        assert run.status == "waiting_plan"
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_expired_clarification_does_not_claim_new_message():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        park_task_wait(db, claim, kind="clarification", prompt="Which source?", now=now)
        decision = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        decision.expires_at = now - timedelta(seconds=1)
        db.commit()
        reply = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        assert reply.disposition == "queued"
        assert reply.request_id is None


def test_primary_message_uniqueness_survives_clarification_support():
    with TestSessionLocal() as db:
        _, _, conversation, run, _ = _identity(db)
        db.add(
            Message(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content="one",
            )
        )
        db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Message(
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content="duplicate",
                )
            )
            db.flush()


@pytest.mark.parametrize("state", ["queued", "running"])
def test_expiry_fences_clarification_assessment_in_flight(state):
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        park_task_wait(db, claim, kind="clarification", prompt="Which source?", now=now)
        db.commit()
        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        if state == "running":
            run.status = "running"
            job.owner_id = "worker"
            job.lease_until = now + timedelta(days=3)
        fence = run.fence
        db.commit()
        assert expire_task_decisions(db, now=now + timedelta(days=2)) >= 1
        assert run.status == "timed_out"
        assert run.fence > fence
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state
            == "expired"
        )
