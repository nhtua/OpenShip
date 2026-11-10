"""Recovery-grade task reads: committed truth only, uncertainty preserved.

These cases seed durable rows with the same helpers the state suite uses and
assert the reconciliation read against those rows, not against any in-memory
plan or model answer.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.runs.tasks.contracts import (
    TaskAttempt,
    TaskCallBinding,
    TaskCounters,
    TaskPlan,
    TaskRecoveryReport,
    TaskStep,
)
from src.openship.runs.tasks.models import (
    TaskDecisionRecord,
    TaskRecord,
    TaskStepAttempt,
)
from src.openship.runs.tasks.recovery import reconcile_task_projection
from src.openship.runs.tasks.state import (
    read_progress,
    record_attempt_result,
    record_task_call,
    save_plan,
)
from tests.conftest import TestSessionLocal
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
        assert db.query(TaskStepAttempt).filter_by(run_id=run.id).count() == before_attempts
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
