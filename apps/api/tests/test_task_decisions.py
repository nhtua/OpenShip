"""Independent transaction-level tests for durable decision expiry."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.openship.runs.models import Event, RunJob
from src.openship.runs.tasks.models import TaskDecisionRecord, TaskRecord
from src.openship.runs.tasks.state import save_plan
from tests.conftest import TestSessionLocal
from tests.test_task_state import _identity, _operation, _plan


def test_park_preserves_completed_work_and_releases_claim(monkeypatch):
    from src.openship.execution.gateway import ToolGateway
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import record_attempt_result

    provider_calls = []
    monkeypatch.setattr(
        ToolGateway, "execute_tool", lambda *a, **kw: provider_calls.append((a, kw))
    )
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user)
        before = record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()
        initial_events = db.query(Event).filter_by(run_id=run.id).count()
        result = park_task_wait(
            db, claim, kind="clarification", prompt="Details?", now=datetime.now(UTC)
        )
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        assert result.attempts == before.attempts
        assert result.verified_outputs == before.verified_outputs
        assert result.counters == before.counters
        assert run.status == "waiting_input" and run.ended_at is None
        assert job.owner_id is None and job.lease_until is None
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events + 1
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id, state="open").count()
            == 1
        )
        db.rollback()
        assert run.status == "running" and job.owner_id == claim.worker_id
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events
        assert provider_calls == []


def test_preplan_wait_binds_pinned_context_digest():
    from src.openship.capabilities.contracts import canonical_digest
    from src.openship.runs.tasks.decisions import park_task_wait
    from tests.test_task_profile_selection import _all_profiles, _catalog, _execution

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        execution = _execution(_catalog(_all_profiles()))
        context = execution.task_context.model_copy(
            update={
                "root_run_id": run.id,
                "project_id": project.id,
                "conversation_id": conversation.id,
                "initiator_id": user.id,
            }
        )
        run.execution_configuration = execution.model_copy(
            update={"task_context": context}
        ).model_dump_json()
        db.commit()
        progress = park_task_wait(
            db, claim, kind="clarification", prompt="Details?", now=datetime.now(UTC)
        )
        assert progress.current_revision == 0
        assert progress.pending_decision.expected_digest == canonical_digest(
            context.model_dump(mode="json")
        )


def test_park_replay_keeps_request_identity():
    from src.openship.runs.tasks.decisions import expire_task_decisions, park_task_wait

    with TestSessionLocal() as db:
        _, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        now = datetime.now(UTC)
        first = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        )
        db.commit()
        second = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        )
        assert first.pending_decision == second.pending_decision
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 1
        expire_task_decisions(db, now=now + timedelta(days=2))
        db.commit()


def test_expiry_cancels_recovery_wait_without_dispatch():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import expire_task_decisions, park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="dispatching", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.commit()
        now = datetime.now(UTC)
        with pytest.raises(TaskStateError):
            park_task_wait(db, claim, kind="clarification", prompt="Details?", now=now)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Outcome unknown",
        )
        result = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="What next?",
            now=now,
            recovery_report=report,
        )
        assert result.pending_decision.recovery_report == report
        assert result.last_disposition == "uncertain" and run.ended_at is None
        assert operation.status == "dispatching"
        assert expire_task_decisions(db, now=now + timedelta(hours=24, seconds=1)) == 1
        assert operation.status == "dispatching" and run.status == "timed_out"


def test_parking_rejects_expired_lease_and_mismatched_recovery():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="running", result_digest=None)
        db.commit()
        now = datetime.now(UTC)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest="a" * 64,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Outcome unknown",
        )
        with pytest.raises(TaskStateError, match="recovery report"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=now,
                recovery_report=report,
            )
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        job.lease_until = now - timedelta(seconds=1)
        db.flush()
        with pytest.raises(TaskStateError, match="lease"):
            park_task_wait(db, claim, kind="configuration", prompt="Next?", now=now)
        assert run.status == "running" and job.owner_id == claim.worker_id
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0


def test_ordinary_wait_rejects_interrupted_dispatched_operation():
    from src.openship.execution.models import ToolAttempt
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        db.add(
            ToolAttempt(
                operation_id=operation.id,
                ordinal=1,
                fence=claim.fence,
                dispatch_started_at=datetime.now(UTC),
                outcome="interrupted",
            )
        )
        db.commit()
        with pytest.raises(TaskStateError, match="unsettled"):
            park_task_wait(
                db,
                claim,
                kind="clarification",
                prompt="Details?",
                now=datetime.now(UTC),
            )


def test_ordinary_wait_rejects_interrupted_without_tool_attempt():
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        _operation(db, run, user, status="interrupted")
        db.commit()
        with pytest.raises(TaskStateError, match="unsettled"):
            park_task_wait(
                db,
                claim,
                kind="clarification",
                prompt="Details?",
                now=datetime.now(UTC),
            )


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {
            "plan_revision": 1,
            "step_id": "inspect",
            "attempt": 2,
            "logical_call_id": "model:1:call:0",
        },
    ],
)
def test_recovery_rejects_missing_or_conflicting_operation_binding(binding):
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="running", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps([binding] if binding else [])
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        with pytest.raises(TaskStateError, match="identity"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=datetime.now(UTC),
                recovery_report=report,
            )


def test_recovery_accepts_matching_existing_attempt():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.models import TaskStepAttempt

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        db.add(
            TaskStepAttempt(
                task_id=task.id,
                run_id=run.id,
                plan_revision=1,
                step_id="inspect",
                ordinal=1,
                status="interrupted",
                operation_id=operation.id,
            )
        )
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        result = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Next?",
            now=datetime.now(UTC),
            recovery_report=report,
        )
        assert result.pending_decision.recovery_report == report


def test_recovery_rejects_conflicting_attempt_even_with_matching_binding():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.models import TaskStepAttempt
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.add(
            TaskStepAttempt(
                task_id=task.id,
                run_id=run.id,
                plan_revision=1,
                step_id="inspect",
                ordinal=2,
                status="interrupted",
                operation_id=operation.id,
            )
        )
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        with pytest.raises(TaskStateError, match="identity"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=datetime.now(UTC),
                recovery_report=report,
            )


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
