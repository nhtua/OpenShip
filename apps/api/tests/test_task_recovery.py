"""Recovery-grade task reads: committed truth only, uncertainty preserved.

These cases seed durable rows with the same helpers the state suite uses and
assert the reconciliation read against those rows, not against any in-memory
plan or model answer.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import Artifact
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import claim_next
from src.openship.runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskAssessment,
    TaskAttempt,
    TaskCallBinding,
    TaskCounters,
    TaskDecisionCommand,
    TaskPlan,
    TaskRecoveryReport,
    TaskStep,
)
from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
from src.openship.runs.tasks.inference import evidence_only_assessment
from src.openship.runs.tasks.models import (
    TaskDecisionCommandRecord,
    TaskDecisionRecord,
    TaskRecord,
    TaskStepAttempt,
)
from src.openship.runs.tasks.recovery import (
    _EVIDENCE_ONLY_EXPLANATION,
    capture_recovery_evidence,
    prepare_task_recovery,
    reconcile_task_projection,
)
from src.openship.runs.tasks.state import (
    TaskStateError,
    read_progress,
    record_attempt_result,
    record_task_call,
    save_plan,
)
from src.openship.sandboxes.models import WorkflowSandbox
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_graph import (
    Lane,
    _call,
    _calls,
    _durable,
    _journey,
    _run_state,
)
from tests.test_task_state import CAPABILITY, _artifact, _identity, _operation, _plan


def _two_step_plan() -> TaskPlan:
    inspect = _plan().steps[0]
    render = TaskStep(
        id="render",
        intent="Render the inspected chart",
        depends_on=("inspect",),
        capability=CAPABILITY,
        profile_id="logs-data",
        expected_outputs=("render.png",),
        completion_checks=("render exists",),
    )
    return TaskPlan(
        revision=1,
        intent="Inspect and render the supplied source.",
        steps=(inspect, render),
        available_capabilities=(CAPABILITY,),
        available_profiles=("logs-data",),
        status="validated",
    )


def test_reconcile_reads_committed_identity_not_model_memory():
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()

        operation = _operation(db, run, user)
        artifact = _artifact(db, run, operation)
        db.flush()

        # The only attempt the projection may report is the committed row.
        record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        row = db.query(TaskStepAttempt).filter_by(run_id=run.id).one()

        task.verified_steps_json = json.dumps(["inspect"])
        task.skipped_steps_json = "[]"
        task.verified_outputs_json = json.dumps(
            [
                ArtifactRef(
                    id=artifact.id,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    media_type=artifact.media_type,
                ).model_dump(mode="json")
            ]
        )
        task.counters_json = TaskCounters(
            tool_calls=5,
            model_turns=2,
            generation_attempts=3,
            output_tokens=120,
        ).model_dump_json()
        task.status = "completed"
        task.last_disposition = "none"
        db.commit()

        before_attempts = db.query(TaskStepAttempt).filter_by(run_id=run.id).count()
        before_task = (
            task.updated_at,
            task.status,
            task.counters_json,
            task.verified_outputs_json,
        )

        progress = reconcile_task_projection(db, run_id=run.id)

        expected_attempt = TaskAttempt(
            step_id=row.step_id,
            attempt=row.ordinal,
            status="succeeded",
            operation_id=operation.id,
            input_digest=operation.arguments_digest,
            result_digest=operation.result_digest,
            evidence_refs=(
                f"operation:{operation.request_digest}",
                f"artifact:{artifact.sha256}",
            ),
        )
        assert progress.current_revision == 1
        assert progress.status == "completed"
        assert progress.step_ids == ("inspect",)
        assert progress.attempts == (expected_attempt,)
        assert progress.attempts[0].operation_id == row.operation_id
        assert progress.attempts[0].input_digest == row.input_digest
        assert progress.attempts[0].result_digest == row.result_digest
        assert progress.verified_steps == ("inspect",)
        assert progress.skipped_steps == ()
        assert progress.verified_outputs == (
            ArtifactRef(
                id=artifact.id,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                media_type=artifact.media_type,
            ),
        )
        assert progress.counters == TaskCounters(
            tool_calls=5,
            model_turns=2,
            generation_attempts=3,
            output_tokens=120,
        )
        assert progress.last_disposition == "none"
        assert progress.pending_decision is None

        # A read is side-effect free: no new rows and no rewritten task row.
        assert (
            db.query(TaskStepAttempt).filter_by(run_id=run.id).count()
            == before_attempts
        )
        assert (
            task.updated_at,
            task.status,
            task.counters_json,
            task.verified_outputs_json,
        ) == before_task
        assert not db.new and not db.dirty


def test_reconcile_preserves_interrupted_attempt_without_inventing_an_operation():
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _two_step_plan()
        save_plan(db, claim, plan, expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()

        # Admitted but unsettled: the call may have dispatched, so the read
        # must keep it uncertain rather than treating the step as absent.
        operation = _operation(db, run, user, status="dispatching", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        bindings = (
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id="model:1:call:0",
            ),
            TaskCallBinding(
                plan_revision=1,
                step_id="render",
                attempt=1,
                logical_call_id="model:1:call:1",
            ),
        )
        task.call_bindings_json = json.dumps(
            [binding.model_dump(mode="json") for binding in bindings], sort_keys=True
        )
        task.status = "waiting"
        task.last_disposition = "uncertain"

        request_id = uuid.uuid4()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="The admitted call has no committed outcome.",
        )
        db.add(
            TaskDecisionRecord(
                task_id=task.id,
                run_id=run.id,
                request_id=request_id,
                kind="recovery",
                plan_revision=1,
                expected_digest=report.digest,
                state="open",
                request_json=json.dumps(
                    {"recovery_report": report.model_dump(mode="json")}
                ),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()

        progress = reconcile_task_projection(db, run_id=run.id)

        assert progress.current_revision == 1
        assert progress.status == "waiting"
        assert progress.last_disposition == "uncertain"
        assert progress.step_ids == ("inspect", "render")
        assert progress.verified_steps == () and progress.skipped_steps == ()
        assert progress.call_bindings == bindings
        assert progress.pending_decision.request_id == request_id
        assert progress.pending_decision.recovery_report == report
        assert progress.attempts == (
            TaskAttempt(
                step_id="inspect",
                attempt=1,
                status="uncertain",
                operation_id=operation.id,
                input_digest=operation.arguments_digest,
            ),
            TaskAttempt(
                step_id="render",
                attempt=1,
                status="uncertain",
                operation_id=None,
                input_digest=None,
            ),
        )
        # No identity is invented for a binding the run never admitted.
        assert all(
            attempt.operation_id in {operation.id, None}
            for attempt in progress.attempts
        )
        # The baseline projection has no settled attempt to show; only the
        # recovery read is allowed to report the possible dispatch.
        assert read_progress(db, run.id).attempts == ()
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == 0
        assert (operation.status, operation.result_digest) == ("dispatching", None)
        assert not db.new and not db.dirty


@pytest.fixture(autouse=True)
def _reset_profile_catalog(monkeypatch):
    from src.openship.config import settings
    from src.openship.sandboxes.profiles import reset_runtime_catalog

    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    yield
    reset_runtime_catalog()


def _pin_recovery_execution(run, user, project, conversation) -> None:
    _pinned_execution(run, user, project, conversation)
    execution = ExecutionConfigV5.model_validate_json(run.execution_configuration)
    run.execution_configuration = execution.model_copy(
        update={"allowed_capabilities": (CAPABILITY,)}
    ).model_dump_json()


def _bind(task: TaskRecord, *bindings: TaskCallBinding) -> None:
    task.call_bindings_json = json.dumps(
        [binding.model_dump(mode="json") for binding in bindings], sort_keys=True
    )


def test_recovery_report_binds_one_unsettled_operation_and_evidence():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        plan = _two_step_plan()
        save_plan(db, claim, plan, expected_revision=0)

        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        artifact = _artifact(db, run, operation)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        binding = TaskCallBinding(
            plan_revision=1,
            step_id="inspect",
            attempt=1,
            logical_call_id=operation.logical_call_id,
        )
        _bind(task, binding)
        db.commit()

        operation_evidence = f"operation:{operation.request_digest}"
        artifact_evidence = f"artifact:{artifact.sha256}"
        assessment = TaskAssessment(
            classification="blocked",
            explanation="The admitted call has no committed outcome.",
            evidence_refs=(operation_evidence, artifact_evidence),
            failure="uncertain",
            step_id="inspect",
            unknowns=("The command may have run.",),
            risks=("Repeating may duplicate the side effect.",),
            recommended_recovery_action="repeat_step",
        )

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=assessment, now=datetime.now(UTC)
        )

        assert report.plan_revision == 1
        assert report.plan_digest == plan.digest
        assert (report.step_id, report.attempt) == ("inspect", 1)
        assert report.operation_id == operation.id
        assert report.evidence_refs == (operation_evidence, artifact_evidence)
        # The recommendation is carried as a label, never selected for the user.
        assert report.recommended_action == "repeat_step"
        assert report.eligible_actions == ("abort", "repeat_step")
        assert report.blockers == (
            "a later step depends on the unresolved step's output",
        )
        assert report.explanation == assessment.explanation
        assert report.unknowns == assessment.unknowns
        assert report.risks == assessment.risks

        # Producing the report mutates nothing; parking persists it later.
        assert not db.new and not db.dirty
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == 0


def test_recovery_report_marks_repeat_ineligible_until_quiescent():
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="dispatching", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=None, now=datetime.now(UTC)
        )

        assert "abort" in report.eligible_actions
        assert "repeat_step" not in report.eligible_actions
        assert any(
            "the earlier command may still be running" in blocker
            for blocker in report.blockers
        )
        # A non-quiescent operation is evidence, not a probe: it is untouched.
        assert (operation.status, operation.result_digest) == ("dispatching", None)
        assert not db.new and not db.dirty
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0


def test_recovery_report_without_assessment_is_evidence_only():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        artifact = _artifact(db, run, operation)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=None, now=datetime.now(UTC)
        )

        assert report.recommended_action is None
        assert report.unknowns == () and report.risks == ()
        assert "only durable evidence is shown" in report.explanation
        assert "no inference" in report.explanation
        assert report.evidence_refs == (
            f"operation:{operation.request_digest}",
            f"artifact:{artifact.sha256}",
        )
        assert report.eligible_actions == ("abort", "repeat_step", "continue_next")
        assert report.blockers == ()
        assert not db.new and not db.dirty
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0


def test_recovery_report_refuses_changed_evidence():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        artifact = _artifact(db, run, operation)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        assessment = TaskAssessment(
            classification="blocked",
            explanation="The admitted call has no committed outcome.",
            evidence_refs=(
                f"operation:{operation.request_digest}",
                f"artifact:{artifact.sha256}",
            ),
            failure="uncertain",
            step_id="inspect",
            recommended_recovery_action="repeat_step",
        )
        # The operation's request identity moves after the diagnosis was made.
        operation.request_digest = "a" * 64
        db.flush()

        with pytest.raises(TaskStateError) as error:
            prepare_task_recovery(
                db, run_id=run.id, assessment=assessment, now=datetime.now(UTC)
            )

        assert error.value.code == "recovery_superseded"
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == 0


def test_reconcile_does_not_duplicate_a_settled_binding():
    """A binding whose attempt row settled is reported once, from that row."""
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user)
        _artifact(db, run, operation)
        operation.logical_call_id = "model:1:call:0"
        db.flush()

        binding = TaskCallBinding(
            plan_revision=1,
            step_id="inspect",
            attempt=1,
            logical_call_id=operation.logical_call_id,
        )
        record_task_call(db, claim, binding=binding)
        record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()

        progress = reconcile_task_projection(db, run_id=run.id)

        assert progress.step_ids == ("inspect",)
        assert progress.call_bindings == (binding,)
        assert len(progress.attempts) == 1
        attempt = progress.attempts[0]
        assert (attempt.step_id, attempt.attempt) == ("inspect", 1)
        assert attempt.status == "succeeded"
        assert attempt.operation_id == operation.id
        assert not any(
            candidate.status == "uncertain" for candidate in progress.attempts
        )


def test_recovery_report_refuses_when_nothing_is_unsettled():
    """No unsettled operation and no bound call: the reader may not diagnose."""
    with TestSessionLocal() as db:
        _, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()

        with pytest.raises(TaskStateError) as error:
            prepare_task_recovery(
                db, run_id=run.id, assessment=None, now=datetime.now(UTC)
            )

        assert error.value.code == "recovery_conflict"
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert not db.new and not db.dirty


def test_recovery_report_refuses_ambiguous_uncertainty():
    """Two unsettled commands: the builder may not guess which one parked."""
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        admitted = _operation(db, run, user, status="admitted", result_digest=None)
        admitted.logical_call_id = "model:1:call:0"
        running = _operation(db, run, user, status="running", result_digest=None)
        running.logical_call_id = "model:1:call:1"
        db.commit()

        with pytest.raises(TaskStateError) as error:
            prepare_task_recovery(
                db, run_id=run.id, assessment=None, now=datetime.now(UTC)
            )

        assert error.value.code == "recovery_conflict"
        assert not db.new and not db.dirty


def test_recovery_report_refuses_operation_outside_task_scope():
    """An operation belonging to another principal cannot bind a diagnosis."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(
            db,
            run,
            user,
            status="admitted",
            result_digest=None,
            principal_id=uuid.uuid4(),
        )
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        with pytest.raises(TaskStateError) as error:
            prepare_task_recovery(
                db, run_id=run.id, assessment=None, now=datetime.now(UTC)
            )

        assert error.value.code == "recovery_conflict"
        assert not db.new and not db.dirty


def test_recovery_report_binding_only_offers_abort_and_parks():
    """A bound call with no admitted operation is real uncertainty to park."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id="model:1:call:0",
            ),
        )
        db.commit()

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=None, now=datetime.now(UTC)
        )
        # No operation identity is invented; the only honest offer is abort.
        assert report.operation_id is None
        assert report.evidence_refs == ()
        assert report.eligible_actions == ("abort",)
        assert any("no admitted operation" in blocker for blocker in report.blockers)
        assert not db.new and not db.dirty

        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="The bound call may have dispatched. Choose.",
            now=datetime.now(UTC),
            recovery_report=report,
        ).pending_decision
        db.commit()
        assert request.recovery_report == report
        stored = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert stored.expected_digest == report.digest
        # No operation digest is stored for a report that has no operation.
        assert "recovery_operation_digest" not in json.loads(stored.request_json)
        assert run.status == "waiting_input"

        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="repeat_step",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=command,
                now=datetime.now(UTC),
            )
        assert error.value.status_code == 409
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_recovery_report_repeat_ineligible_while_a_dispatch_exists():
    """A recorded dispatch makes the earlier attempt settled for re-attempting."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        now = datetime.now(UTC)
        db.add(
            ToolAttempt(
                operation_id=operation.id,
                ordinal=1,
                fence=claim.fence,
                dispatch_started_at=now,
                outcome="started",
            )
        )
        db.commit()

        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        assert "repeat_step" not in report.eligible_actions
        assert "the earlier command was already dispatched" in report.blockers
        # Continuation stays offered: the blocker names repeat, not every action.
        assert "continue_next" in report.eligible_actions
        assert not db.new and not db.dirty


def test_recovery_report_repeat_ineligible_until_sandbox_ttl_clears():
    """The provider TTL, not a probe, is what keeps repeat switched off."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="interrupted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        now = datetime.now(UTC)
        db.add(
            WorkflowSandbox(
                root_run_id=run.id,
                project_id=run.project_id,
                conversation_id=run.conversation_id,
                provider_id="test-provider",
                requirements_key="l" * 64,
                status="active",
                sandbox_id="sandbox-1",
                expires_at=now + timedelta(hours=1),
                command_uncertain_until=now + timedelta(hours=1),
                command_operation_id=operation.id,
                fence=1,
            )
        )
        db.commit()

        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        assert "repeat_step" not in report.eligible_actions
        assert "the sandbox may still be running the earlier command" in report.blockers
        # Evidence reading only: the pointer and its TTL are left untouched.
        sandbox_row = (
            db.query(WorkflowSandbox).filter_by(project_id=run.project_id).one()
        )
        assert sandbox_row.status == "active"
        assert sandbox_row.command_operation_id == operation.id
        assert not db.new and not db.dirty


def test_recovery_report_repeat_ineligible_when_capability_drifted():
    """A drifted operation pin cannot be repeated under the current plan pin."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.capability_version = "9.9.9"
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=None, now=datetime.now(UTC)
        )
        assert "repeat_step" not in report.eligible_actions
        assert (
            "the operation's capability does not match the affected step"
            in report.blockers
        )
        assert not db.new and not db.dirty


def test_recovery_report_repeat_ineligible_when_budget_exhausted():
    """An exhausted tool-call budget keeps repeat off the offer."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        execution = ExecutionConfigV5.model_validate_json(run.execution_configuration)
        task.counters_json = json.dumps(
            {"tool_calls": execution.loop_limits.max_tool_calls}
        )
        db.commit()

        report = prepare_task_recovery(
            db, run_id=run.id, assessment=None, now=datetime.now(UTC)
        )
        assert "repeat_step" not in report.eligible_actions
        assert "the tool-call budget is exhausted" in report.blockers
        assert not db.new and not db.dirty


def test_recovery_report_refuses_changed_evidence_behind_a_snapshot():
    """A supplied snapshot must still match every durable identity."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        snapshot = capture_recovery_evidence(db, run_id=run.id)
        operation.status = "interrupted"
        db.flush()

        with pytest.raises(TaskStateError) as error:
            prepare_task_recovery(
                db,
                run_id=run.id,
                assessment=None,
                now=datetime.now(UTC),
                snapshot=snapshot,
            )
        assert error.value.code == "recovery_superseded"
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert not db.new and not db.dirty


def test_recovery_report_accepts_evidence_only_diagnosis():
    """The mandated fallback diagnosis is accepted, not refused as moved."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        artifact = _artifact(db, run, operation)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        task.last_disposition = "uncertain"
        db.commit()

        progress = reconcile_task_projection(db, run_id=run.id)
        assessment = evidence_only_assessment(progress, ())
        report = prepare_task_recovery(
            db, run_id=run.id, assessment=assessment, now=datetime.now(UTC)
        )

        assert report.explanation == assessment.explanation
        assert report.unknowns == assessment.unknowns
        assert report.risks == assessment.risks
        # A diagnosis the run never inferred recommends nothing.
        assert report.recommended_action is None
        # The report evidence is still the freshly read operation identity.
        assert report.evidence_refs == (
            f"operation:{operation.request_digest}",
            f"artifact:{artifact.sha256}",
        )
        assert not db.new and not db.dirty


def test_recovery_choice_is_accepted_when_blockers_name_another_action():
    """A blocker for continue must not veto the offered repeat choice."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        plan = _two_step_plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        now = datetime.now(UTC)
        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        # The quiescent repeat stays offered next to the dependency blocker.
        assert report.eligible_actions == ("abort", "repeat_step")
        assert report.blockers == (
            "a later step depends on the unresolved step's output",
        )

        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Repeat the inspected step?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="repeat_step",
        )
        result = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        db.commit()
        assert result.pending_decision is None
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state
            == "confirmed"
        )
        assert run.status == "queued"
        stored = db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).one()
        assert stored.action == "repeat_step"


@pytest.mark.parametrize("mode", ["auto", "copilot"])
def test_uncertain_auto_and_copilot_diagnose_before_user_choice(mode, monkeypatch):
    """A crashed dispatch parks one diagnosis request in both modes, new and alike."""
    lane = Lane(monkeypatch, mode=mode, behaviors=_journey(), crash=True)
    if mode == "copilot":
        assert lane.invoke()["parked"] == "plan_confirmation"
        assert len(lane.gateway.calls) == 0
        lane.confirm_pending()
        lane.resume_queue("copilot-worker")
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    # The admit survived as the only operation; the worker then died. The crashed
    # admit's own gateway call is recorded in the ledger, so the honest measure of
    # what the diagnosis added is the delta from the count at the crash.
    dispatches_at_crash = len(lane.gateway.calls)
    assert dispatches_at_crash == 1

    lane.rehold()
    resumed = lane.invoke()
    assert resumed["parked"] == "recovery"
    # The diagnosis added no dispatch: not even a probe of the uncertain command.
    dispatches_before_user_recovery_choice = (
        len(lane.gateway.calls) - dispatches_at_crash
    )
    assert dispatches_before_user_recovery_choice == 0

    durable = _durable(lane.run_id)
    pending = durable["progress"].pending_decision
    assert pending is not None
    assert pending.kind == "recovery"
    assert pending.plan_revision >= 1
    report = pending.recovery_report
    assert report is not None
    assert report.plan_revision == pending.plan_revision
    assert report.digest == pending.expected_digest
    assert "abort" in report.eligible_actions
    open_requests = [row for row in durable["decisions"] if row.state == "open"]
    assert len(open_requests) == 1
    assert open_requests[0].kind == "recovery"
    assert durable["attempts"] == []
    assert len(durable["operations"]) == 1
    assert _run_state(lane.run_id)["status"] == "waiting_input"


def test_no_inference_budget_shows_evidence_only_diagnosis():
    """No diagnosis to reuse and none to infer: the labeled report still parks.

    This is the shape the graph reaches when its diagnosis stage runs out of
    inference: it falls back to what the durable rows prove, labels that, and
    recommends nothing. The snapshot path proves the same identities the
    diagnosis would have read are still the ones being parked.
    """
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _two_step_plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        snapshot = capture_recovery_evidence(db, run_id=run.id)
        report = prepare_task_recovery(
            db,
            run_id=run.id,
            assessment=None,
            now=datetime.now(UTC),
            snapshot=snapshot,
        )
        # The report says what it is: only durable evidence, no inference.
        assert report.explanation == _EVIDENCE_ONLY_EXPLANATION
        assert report.unknowns == ()
        assert report.risks == ()
        assert report.recommended_action is None
        # It still cites the operation identity the rows prove.
        assert report.evidence_refs == (f"operation:{operation.request_digest}",)
        assert report.eligible_actions == ("abort", "repeat_step")

        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="A call may have dispatched without an outcome. Choose.",
            now=datetime.now(UTC),
            recovery_report=report,
        ).pending_decision
        db.commit()
        assert request.recovery_report == report
        assert request.expected_digest == report.digest
        assert run.status == "waiting_input"


def test_recommendation_and_bare_yes_cannot_choose_recovery():
    """A diagnosis recommendation is a label; answering a recovery ask chooses nothing."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _two_step_plan(), expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=operation.logical_call_id,
            ),
        )
        db.commit()

        assessment = TaskAssessment(
            classification="task",
            explanation="The admitted call has no committed outcome.",
            failure="uncertain",
            step_id="inspect",
            recommended_recovery_action="repeat_step",
        )
        report = prepare_task_recovery(
            db, run_id=run.id, assessment=assessment, now=datetime.now(UTC)
        )
        # The recommendation is carried as a label, never applied for the user.
        assert report.recommended_action == "repeat_step"

        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="A call may have dispatched without an outcome. Choose.",
            now=datetime.now(UTC),
            recovery_report=report,
        ).pending_decision
        db.commit()

        bare_yes = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="answer",
            answer="yes",
        )
        with pytest.raises(HTTPException) as refused:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=bare_yes,
                now=datetime.now(UTC),
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "decision_action_unavailable"
        # A refused choice consumes nothing: the request stays open and bound.
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )
        assert db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).count() == 0
        assert run.status == "waiting_input"
        assert not db.new and not db.dirty


def _take_back_lease(db, run_id: uuid.UUID, worker_id: str = "recovery-worker"):
    """Hand this run back through the queue, parking other queued lanes first."""
    stale = [
        job.run_id
        for job in db.query(RunJob)
        .join(Run, Run.id == RunJob.run_id)
        .filter(Run.status == "queued", Run.id != run_id)
        .all()
    ]
    if stale:
        db.query(RunJob).filter(RunJob.run_id.in_(stale)).update(
            {
                RunJob.owner_id: "prior-lane",
                RunJob.lease_until: datetime.now(UTC) + timedelta(minutes=5),
            },
            synchronize_session=False,
        )
    return claim_next(db, worker_id, lease_seconds=600)


def test_repeat_choice_records_the_new_attempt_identity():
    """The chosen repeat is a fresh attempt: it never reuses the dead call's identity."""
    with TestSessionLocal() as db:
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
        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        assert report.step_id == "render"
        assert report.attempt == 1
        assert report.eligible_actions == ("abort", "repeat_step", "continue_next")

        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Repeat the render step?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()
        before = read_progress(db, run.id).counters
        identity = apply_decision(
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
            now=now,
        )
        db.commit()

        # A resumed worker takes the run back through the queue: the choice
        # re-queued it, and the new attempt is recorded under that lease.
        taken = _take_back_lease(db, run.id)
        assert taken is not None and taken.run_id == run.id
        replay = _operation(db, run, user, status="succeeded")
        replay.logical_call_id = "model:1:call:2"
        after = record_attempt_result(
            db, taken, step_id="render", attempt=2, operation_id=replay.id
        ).counters
        db.commit()

        repeat_attempt = (
            db.query(TaskStepAttempt).filter_by(run_id=run.id, step_id="render").one()
        )
        completed_attempt = (
            db.query(TaskStepAttempt).filter_by(run_id=run.id, step_id="inspect").one()
        )
        assert repeat_attempt.operation_id != completed_attempt.operation_id
        assert repeat_attempt.operation_id != dead.id
        assert repeat_attempt.ordinal == 2
        # The completed step keeps exactly one attempt: it was not replayed.
        assert (
            db.query(TaskStepAttempt)
            .filter_by(run_id=run.id, step_id="inspect")
            .count()
            == 1
        )
        # Counters only ever move forward, so a recovery cannot fund a fresh run.
        assert after.tool_calls >= before.tool_calls
        assert after.model_turns >= before.model_turns
        assert sum(after.repairs_by_step.values()) >= sum(
            before.repairs_by_step.values()
        )
        assert identity.pending_decision is None


def test_abort_retains_partial_outputs_and_never_claims_undo():
    """Aborting stops the task and keeps what already committed."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _two_step_plan(), expected_revision=0)
        completed = _operation(db, run, user, status="succeeded")
        completed.logical_call_id = "model:1:call:0"
        _artifact(db, run, completed)
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
        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Stop the run?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()
        apply_decision(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=TaskDecisionCommand(
                client_request_id=uuid.uuid4(),
                request_id=request.request_id,
                plan_revision=request.plan_revision,
                expected_digest=request.expected_digest,
                action="abort",
            ),
            now=now,
        )
        db.commit()

        assert run.status == "cancelled"
        assert run.error_code == "recovery_aborted"
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state
            == "rejected"
        )
        # Nothing is erased and nothing claims the effects were reversed: the
        # committed call, its attempt and its artifact all stay on the books.
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == 1
        assert db.query(Artifact).filter_by(originating_run_id=run.id).count() == 1
        assert db.query(ToolOperation).filter_by(run_id=run.id).count() == 2
        kept = db.query(TaskStepAttempt).filter_by(run_id=run.id).one()
        assert kept.operation_id == completed.id
        assert read_progress(db, run.id).skipped_steps == ()


def test_continue_marks_skip_and_requires_dependency_inputs():
    """A leaf step may be skipped, never as a verified success; a needed one may not."""
    with TestSessionLocal() as db:
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
        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        # Nothing downstream needs the leaf's output, so continuing is offered.
        assert report.step_id == "render"
        assert "continue_next" in report.eligible_actions
        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Skip render and finish?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()
        result = apply_decision(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=TaskDecisionCommand(
                client_request_id=uuid.uuid4(),
                request_id=request.request_id,
                plan_revision=request.plan_revision,
                expected_digest=request.expected_digest,
                action="continue_next",
            ),
            now=now,
        )
        db.commit()
        assert result.pending_decision is None
        assert result.skipped_steps == ("render",)
        assert "render" not in result.verified_steps
        assert run.status == "queued"

    # A step whose output a later step consumes cannot be skipped silently.
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _two_step_plan(), expected_revision=0)
        dead = _operation(db, run, user, status="admitted", result_digest=None)
        dead.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=dead.logical_call_id,
            ),
        )
        db.commit()

        now = datetime.now(UTC)
        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        assert report.step_id == "inspect"
        assert "continue_next" not in report.eligible_actions
        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Repeat or stop?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()
        with pytest.raises(HTTPException) as refused:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=TaskDecisionCommand(
                    client_request_id=uuid.uuid4(),
                    request_id=request.request_id,
                    plan_revision=request.plan_revision,
                    expected_digest=request.expected_digest,
                    action="continue_next",
                ),
                now=now,
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "recovery_ineligible"
        # Refused: the ask stays open and nothing was consumed.
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )
        assert db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).count() == 0
        assert run.status == "waiting_input"


def test_uncertain_command_blocks_repeat_until_quiescent():
    """A dispatch that may still be running offers abort only, until it settles."""
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pin_recovery_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _two_step_plan(), expected_revision=0)
        dead = _operation(db, run, user, status="dispatching", result_digest=None)
        dead.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        _bind(
            task,
            TaskCallBinding(
                plan_revision=1,
                step_id="inspect",
                attempt=1,
                logical_call_id=dead.logical_call_id,
            ),
        )
        now = datetime.now(UTC)
        db.add(
            WorkflowSandbox(
                root_run_id=run.id,
                project_id=run.project_id,
                conversation_id=run.conversation_id,
                provider_id="test-provider",
                requirements_key="l" * 64,
                status="active",
                sandbox_id="sandbox-1",
                expires_at=now + timedelta(hours=1),
                command_uncertain_until=now + timedelta(hours=1),
                command_operation_id=dead.id,
                fence=1,
            )
        )
        db.commit()

        report = prepare_task_recovery(db, run_id=run.id, assessment=None, now=now)
        assert report.eligible_actions == ("abort",)
        assert "the sandbox may still be running the earlier command" in report.blockers
        request = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="The call may still be running. Abort?",
            now=now,
            recovery_report=report,
        ).pending_decision
        db.commit()

        for action in ("repeat_step", "continue_next"):
            with pytest.raises(HTTPException) as refused:
                apply_decision(
                    db,
                    principal_id=user.id,
                    run_id=run.id,
                    command=TaskDecisionCommand(
                        client_request_id=uuid.uuid4(),
                        request_id=request.request_id,
                        plan_revision=request.plan_revision,
                        expected_digest=request.expected_digest,
                        action=action,
                    ),
                    now=now,
                )
            assert refused.value.status_code == 409
            assert refused.value.detail["error"]["code"] == "recovery_ineligible"
        # The offer, not a probe, is what kept them out: the ask stays open.
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )
        assert db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).count() == 0
        sandbox_row = (
            db.query(WorkflowSandbox).filter_by(project_id=run.project_id).one()
        )
        assert sandbox_row.command_operation_id == dead.id
        assert sandbox_row.command_uncertain_until is not None
        assert run.status == "waiting_input"


def test_repeat_creates_new_attempt_without_replaying_completed_steps(monkeypatch):
    """The chosen repeat re-runs only its own step, through the fixed graph."""
    # The crashed admit's own request is behaviour 3; the repeat turn's request
    # is the next one, then the untouched step's, because the parked diagnosis
    # asked the provider nothing at all.
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[
            *_journey()[:3],
            _call(
                "provider-repeat",
                "download_file",
                {"url": "https://observations.test/day.csv"},
            ),
            _journey()[3],
        ],
        crash=True,
    )
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    # The admit is committed and the worker died: nothing settled, and the
    # uncertainty is owned by the durable ask the next worker run presents.
    before = _durable(lane.run_id)
    assert before["attempts"] == []
    assert len(before["operations"]) == 1
    dead_operation = before["operations"][0]
    assert dead_operation.status == "admitted"
    assert dead_operation.logical_call_id is not None
    assert before["progress"].pending_decision is None

    lane.rehold()
    parked = lane.invoke()
    assert parked["parked"] == "recovery"
    pending = _durable(lane.run_id)["progress"].pending_decision
    assert pending is not None and pending.kind == "recovery"
    assert pending.recovery_report is not None
    assert pending.recovery_report.step_id == "fetch"
    assert pending.recovery_report.attempt == 1
    # The diagnosis asked and dispatched nothing: the sequence never advanced.
    assert lane.transport.requests == 3
    dispatches_at_choice = _calls(lane)
    assert dispatches_at_choice == ["download_file"]

    lane.confirm_pending(action="repeat_step")
    lane.resume_queue("recovery-worker")
    # The worker that died is gone; the resumed one is healthy, so the chosen
    # repeat may settle this time.
    lane.gateway.crash_after_admission = False
    lane.rehold()
    lane.invoke()

    dispatches = _calls(lane)
    # Only the named step ran again: the step the choice never mentioned ran
    # exactly once, on its own turn.
    step_the_choice_never_touched_dispatches = dispatches.count("python_execute")
    assert step_the_choice_never_touched_dispatches == 1
    assert dispatches.count("download_file") == 2
    assert dispatches[len(dispatches_at_choice) :] == [
        "download_file",
        "python_execute",
    ]
    with lane.sessions() as db:
        attempts = db.query(TaskStepAttempt).filter_by(run_id=lane.run_id).all()
        operations = db.query(ToolOperation).filter_by(run_id=lane.run_id).all()
        task_row = db.query(TaskRecord).filter_by(run_id=lane.run_id).one()
    assert {(row.step_id, row.ordinal, row.status) for row in attempts} == {
        ("fetch", 1, "succeeded"),
        ("analyze", 1, "succeeded"),
    }
    repeat_attempt = next(row for row in attempts if row.step_id == "fetch")
    still_unsettled = {row.id for row in operations if row.status == "admitted"}
    # The repeat carries its own committed call, never the dead one's identity.
    assert repeat_attempt.operation_id != dead_operation.id
    assert repeat_attempt.operation_id not in still_unsettled
    assert still_unsettled == {dead_operation.id}
    assert task_row.status == "completed"
