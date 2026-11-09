"""Independent transaction-level tests for durable decision expiry."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.openship.runs.models import Event, RunJob
from src.openship.runs.tasks.models import TaskDecisionRecord, TaskRecord
from src.openship.runs.tasks.state import save_plan
from tests.conftest import TestSessionLocal
from tests.test_task_state import _identity, _plan


def test_preplan_clarification_contract_and_storage_use_revision_zero():
    from src.openship.runs.tasks.contracts import (
        TaskDecision,
        TaskDecisionCommand,
        TaskProgress,
    )

    now = datetime.now(UTC)
    request_id = uuid.uuid4()
    digest = "a" * 64
    decision = TaskDecision(
        request_id=request_id,
        kind="clarification",
        plan_revision=0,
        expected_digest=digest,
        expires_at=now,
    )
    assert TaskProgress(current_revision=0, status="waiting", pending_decision=decision)
    assert (
        TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request_id,
            plan_revision=0,
            expected_digest=digest,
            action="answer",
            answer="details",
        ).plan_revision
        == 0
    )
    with pytest.raises(ValidationError):
        TaskDecision(
            request_id=request_id,
            kind="plan_confirmation",
            plan_revision=0,
            expected_digest=digest,
            expires_at=now,
        )

    db = TestSessionLocal()
    try:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.current_revision = 0
        db.add(
            TaskDecisionRecord(
                task_id=task.id,
                run_id=run.id,
                request_id=request_id,
                kind="clarification",
                plan_revision=0,
                expected_digest=digest,
                actor_id=user.id,
                expires_at=now,
            )
        )
        db.flush()
    finally:
        db.rollback()
        db.close()


def test_expiry_is_bounded_and_rolls_back_with_caller_transaction():
    from src.openship.runs.tasks.decisions import expire_task_decisions

    db = TestSessionLocal()
    try:
        now = datetime.now(UTC)
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        run.status = "waiting_plan"
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        job.owner_id = job.lease_until = None
        task.status = "waiting"
        decision = TaskDecisionRecord(
            task_id=task.id,
            run_id=run.id,
            request_id=uuid.uuid4(),
            kind="plan_confirmation",
            plan_revision=task.current_revision,
            expected_digest=task.digest,
            state="open",
            actor_id=user.id,
            expires_at=now - timedelta(seconds=1),
        )
        db.add(decision)
        db.commit()
        initial_events = db.query(Event).filter_by(run_id=run.id).count()

        assert expire_task_decisions(db, now=now, batch_size=1) == 1
        assert run.status == "timed_out" and run.error_code == "decision_expired"
        assert decision.state == "expired" and task.status == "blocked"
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events + 1
        db.rollback()
        assert run.status == "waiting_plan" and decision.state == "open"
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events
        assert expire_task_decisions(db, now=now, batch_size=1) == 1
        db.commit()
        assert expire_task_decisions(db, now=now, batch_size=1) == 0
    finally:
        db.close()


def test_expiry_does_not_override_cancellation_or_live_lease():
    from src.openship.runs.tasks.decisions import expire_task_decisions

    db = TestSessionLocal()
    try:
        now = datetime.now(UTC)
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.status = "waiting"
        decision = TaskDecisionRecord(
            task_id=task.id,
            run_id=run.id,
            request_id=uuid.uuid4(),
            kind="plan_confirmation",
            plan_revision=task.current_revision,
            expected_digest=task.digest,
            state="open",
            actor_id=user.id,
            expires_at=now - timedelta(seconds=1),
        )
        db.add(decision)
        run.status = "cancelled"
        db.flush()
        assert expire_task_decisions(db, now=now) == 0
        run.status = "waiting_plan"
        db.flush()
        assert expire_task_decisions(db, now=now) == 0
        assert decision.state == "open"
    finally:
        db.rollback()
        db.close()
