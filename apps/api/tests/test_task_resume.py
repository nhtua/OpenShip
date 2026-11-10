"""A/B resume for task-graph v5 roots: no silent bypass, no inherited approval."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.openship.runs.models import Run
from src.openship.runs.resume import resume_task
from src.openship.runs.schemas import ResumeChatPythonRequest
from src.openship.runs.tasks.contracts import (
    TaskCallBinding,
    TaskDecisionCommand,
)
from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
from src.openship.runs.tasks.inference import evidence_only_assessment
from src.openship.runs.tasks.models import (
    TaskDecisionRecord,
    TaskRecord,
    TaskStepAttempt,
)
from src.openship.runs.tasks.recovery import prepare_task_recovery
from src.openship.runs.tasks.state import (
    read_progress,
    record_attempt_result,
    save_plan,
)
from src.openship.sandboxes.models import WorkflowSandbox
from tests.conftest import TestSessionLocal
from tests.test_task_recovery import (
    _bind,
    _identity,
    _operation,
    _pin_recovery_execution,
    _two_step_plan,
)

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")


@pytest.fixture(autouse=True)
def _reset_profile_catalog(monkeypatch):
    """A decision resolves its pinned profile against a mounted installation."""
    from src.openship.config import settings
    from src.openship.sandboxes.profiles import reset_runtime_catalog

    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    yield
    reset_runtime_catalog()


def _uncertain_root(db):
    """One v5 root with a settled step and an admitted call that never settled."""
    user, project, conversation, run, claim = _identity(db)
    _pin_recovery_execution(run, user, project, conversation)
    db.flush()
    save_plan(db, claim, _two_step_plan(), expected_revision=0)
    completed = _operation(db, run, user, status="succeeded")
    completed.logical_call_id = "model:1:call:0"
    record_attempt_result(
        db, claim, step_id="inspect", attempt=1, operation_id=completed.id
    )
    dead = _operation(db, run, user, status="admitted", result_digest=None)
    dead.logical_call_id = "model:1:call:1"
    task = db.query(TaskRecord).filter_by(run_id=run.id).one()
    _bind(
        task,
        TaskCallBinding(
            plan_revision=1,
            step_id="render",
            attempt=1,
            logical_call_id=dead.logical_call_id,
        ),
    )
    db.commit()
    now = datetime.now(UTC)
    report = prepare_task_recovery(
        db,
        run_id=run.id,
        assessment=evidence_only_assessment(read_progress(db, run.id), ()),
        now=now,
    )
    request = park_task_wait(
        db,
        claim,
        kind="recovery",
        prompt="A call may have dispatched without an outcome. Choose.",
        now=now,
        recovery_report=report,
    ).pending_decision
    db.commit()
    return user, project, conversation, run, request


def test_a_cannot_bypass_recovery_and_retains_root_budget(monkeypatch):
    """A re-admits the same root and never answers or re-funds it."""
    with TestSessionLocal() as db:
        user, _project, conversation, run, request = _uncertain_root(db)
        before = read_progress(db, run.id)
        root_rows = db.query(Run).filter_by(conversation_id=conversation.id).count()

        with pytest.raises(HTTPException) as refused:
            resume_task(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=ResumeChatPythonRequest(
                    mode="last_step", client_request_id=uuid.uuid4()
                ),
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "recovery_choice_required"
        # Nothing was admitted and nothing was decided: the ask stays open.
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=run.id, kind="recovery", state="open")
            .count()
            == 1
        )
        assert (
            db.query(Run).filter_by(conversation_id=conversation.id).count()
            == root_rows
        )
        assert run.status == "waiting_input"

        # With the choice recorded explicitly, A re-admits that same root: the
        # plan, the attempts and every counter stay exactly as committed.
        applied = apply_decision(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=TaskDecisionCommand(
                client_request_id=uuid.uuid4(),
                request_id=request.request_id,
                plan_revision=request.plan_revision,
                expected_digest=request.expected_digest,
                action="repeat_step",
            ),
            now=datetime.now(UTC),
        )
        db.commit()
        assert applied.pending_decision is None
        counters_after_choice = read_progress(db, run.id).counters
        assert run.status == "queued"
        root = resume_task(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=ResumeChatPythonRequest(
                mode="last_step", client_request_id=uuid.uuid4()
            ),
        )
        assert root.id == run.id
        assert root.status == "queued"
        db.commit()
        resumed = read_progress(db, run.id)
        assert resumed.current_revision == before.current_revision
        assert resumed.verified_steps == before.verified_steps
        assert resumed.attempts == before.attempts
        assert resumed.counters == counters_after_choice
        assert resumed.counters == before.counters
        assert resumed.skipped_steps == ()
        # The root keeps its own budget: the one recorded call is still the one
        # recorded call, and no attempt was added just because A re-admitted.
        assert resumed.counters.tool_calls == before.counters.tool_calls == 1
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == len(
            before.attempts
        )
        assert (
            db.query(Run).filter_by(conversation_id=conversation.id).count()
            == root_rows
        )
        # A never becomes a recovery choice: the ledger holds exactly the one
        # decision command the user made.
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id, state="open").count()
            == 0
        )


def test_b_captures_current_mode_and_requires_copilot_gate(monkeypatch):
    """B is a new root in the account's mode; approval is never inherited."""
    with TestSessionLocal() as db:
        user, _project, conversation, run, _request = _uncertain_root(db)
        user.agent_mode = "copilot"
        user.agent_mode_revision = 3
        db.flush()
        root_rows = db.query(Run).filter_by(conversation_id=conversation.id).count()

        new_run = resume_task(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=ResumeChatPythonRequest(
                mode="from_beginning", client_request_id=uuid.uuid4()
            ),
        )
        db.commit()
        assert new_run.id != run.id
        assert new_run.status == "queued"
        pinned = json.loads(new_run.execution_configuration)
        assert pinned["task_context"]["mode"] == "copilot"
        assert pinned["task_context"]["root_run_id"] == str(new_run.id)
        assert pinned["task_context"]["graph_version"] == 5
        # The new root carries no plan, no progress and no decision: its own
        # Copilot gate opens before any step can run.
        assert db.query(TaskRecord).filter_by(run_id=new_run.id).count() == 0
        assert db.query(TaskDecisionRecord).filter_by(run_id=new_run.id).count() == 0
        assert db.query(TaskStepAttempt).filter_by(run_id=new_run.id).count() == 0
        assert db.query(WorkflowSandbox).filter_by(root_run_id=new_run.id).count() == 0
        # The prior root keeps its own open recovery: B grants nothing to it.
        assert run.status == "waiting_input"
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=run.id, kind="recovery", state="open")
            .count()
            == 1
        )
        assert (
            db.query(Run).filter_by(conversation_id=conversation.id).count()
            == root_rows + 1
        )
