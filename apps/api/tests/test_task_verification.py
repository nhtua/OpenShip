"""Verification proves what the records say ran, not what an answer claims.

Every case seeds real durable records - a v5 chat root with a saved plan, a
committed ``ToolOperation``, published artifacts whose bytes really are in the
local store, and the step attempt that binds them - and then asks the
verification authority for a verdict. The store matters here because the
independent check is that the bytes still stored prove the identity the record
claims; a case that faked the digest would be a case about hashing, not about
verification.

Two deferrals are recorded honestly:

* Choosing to skip a step is Task 15's recovery decision. Those cases write the
  durable skip state a resolved ``continue_next`` leaves behind and then ask
  what verification and the switcher make of it, which is the policy this
  module owns.
* Recording an attempt's outcome is Task 6's. Those cases use it as the record
  verification reads, never as a verdict.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.config import settings
from src.openship.execution.models import ToolOperation
from src.openship.runs.tasks.contracts import (
    TaskAssessment,
    TaskPlan,
    TaskStep,
    VerificationCheck,
    VerificationReport,
)
from src.openship.runs.tasks.models import TaskRecord
from src.openship.runs.tasks.routing import select_action
from src.openship.runs.tasks.state import (
    TaskStateError,
    read_progress,
    record_attempt_result,
    record_verification,
    save_plan,
)
from src.openship.runs.tasks.verification import (
    VerificationError,
    verify_step,
    verify_task,
)
from tests.conftest import TestSessionLocal
from tests.test_task_state import _identity

CHART = "chart.png"
AVERAGES = "averages.json"
OBSERVATIONS = "observations.csv"
CHART_BYTES = b"\x89PNG\r\n\x1a\nfake-chart-bytes"
AVERAGE_BYTES = b'{"averages": {"mean": 3.5}}'

CAPABILITY = CapabilityRef(
    id="python_execute", version="1.0.0", manifest_digest="b" * 64
)
OPERATION_EVIDENCE = "operation:" + "e" * 64


def _plan() -> TaskPlan:
    """The two-step plan this lane verifies: fetch, then chart and average."""
    return TaskPlan(
        revision=1,
        intent="Chart the supplied observations and report their averages.",
        steps=(
            TaskStep(
                id="fetch",
                intent="Download the supplied observations",
                capability=CAPABILITY,
                expected_outputs=(OBSERVATIONS,),
                completion_checks=("observations staged",),
            ),
            TaskStep(
                id="render",
                intent="Chart the observations and report their averages",
                depends_on=("fetch",),
                capability=CAPABILITY,
                expected_outputs=(CHART, AVERAGES),
                completion_checks=("chart and averages both exist",),
            ),
        ),
        available_capabilities=(CAPABILITY,),
        status="validated",
    )


def _assessment(
    *,
    evidence_refs: tuple[str, ...] = (),
    failure: str = "none",
    step_id: str | None = None,
) -> TaskAssessment:
    """The model's reading of the step: actionable, and citing these refs."""
    return TaskAssessment(
        classification="task",
        explanation="The step reported its chart and averages.",
        evidence_refs=evidence_refs,
        missing_information=(),
        failure=failure,
        step_id=step_id,
    )


def _store(monkeypatch, tmp_path) -> LocalArtifactStore:
    """The installation's artifact volume for this case, with its identity set."""
    storage_id = uuid.uuid4()
    root = tmp_path / "artifact-volume"
    monkeypatch.setattr(settings, "artifact_root", root)
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    store = LocalArtifactStore(root, storage_id)
    store.initialize()
    return store


def _operation(db, run, user_id: uuid.UUID) -> ToolOperation:
    """One committed python step: exit 0, with its own result identity."""
    operation = ToolOperation(
        run_id=run.id,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        principal_id=user_id,
        logical_call_id="model:0:call:0",
        capability_id=CAPABILITY.id,
        capability_version=CAPABILITY.version,
        manifest_digest=CAPABILITY.manifest_digest,
        arguments_json='{"source": "pass"}',
        arguments_digest="a" * 64,
        request_digest="e" * 64,
        input_artifact_refs="[]",
        configuration_revision="1",
        execution_bundle_digest="f" * 64,
        status="succeeded",
        result_json=json.dumps({"exit_code": 0, "stdout": "chart written"}),
        result_digest="c" * 64,
        output_artifact_ids="[]",
    )
    db.add(operation)
    db.flush()
    return operation


def _publish(
    db,
    store: LocalArtifactStore,
    run,
    operation: ToolOperation,
    *,
    name: str,
    media_type: str,
    data: bytes,
) -> Artifact:
    """Real published bytes for one declared output, staged by the store."""
    artifact_id = uuid.uuid4()
    reference = ArtifactRef(
        id=artifact_id,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )
    staged = store.stage(
        artifact_id,
        [data],
        reference,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
    )
    store.publish_file(staged, f"{artifact_id}/content")
    row = Artifact(
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        originating_run_id=run.id,
        originating_operation_id=operation.id,
        kind="analysis_output",
        media_type=media_type,
        display_name=name,
        size_bytes=len(data),
        sha256=reference.sha256,
        relative_storage_key=f"{artifact_id}/content",
        status="ready",
        source="tool",
    )
    db.add(row)
    db.flush()
    recorded = json.loads(operation.output_artifact_ids or "[]")
    operation.output_artifact_ids = json.dumps([*recorded, str(row.id)])
    db.flush()
    return row


def _attempt(db, claim, step_id: str, operation: ToolOperation) -> None:
    """Bind one committed attempt to that operation (Task 6's transition)."""
    record_attempt_result(
        db,
        claim,
        step_id=step_id,
        attempt=1,
        operation_id=operation.id,
    )


def _tamper(store: LocalArtifactStore, storage_key: str) -> None:
    """Put different bytes behind an unchanged record.

    Nothing here touches the durable record: exactly what a verdict must refuse
    when the bytes on the volume stop proving the identity that record claims.
    """
    path = store.root
    for part in storage_key.split("/"):
        path = path / part
    path.write_bytes(b"some other bytes entirely")


def _skip(task: TaskRecord, step_id: str) -> None:
    """The durable state a resolved skip leaves (Task 15 decides the skip)."""
    task.skipped_steps_json = json.dumps([step_id])


def test_verified_step_records_only_honest_evidence(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        user, _project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user.id)
        chart = _publish(
            db,
            store,
            run,
            operation,
            name=CHART,
            media_type="image/png",
            data=CHART_BYTES,
        )
        averages = _publish(
            db,
            store,
            run,
            operation,
            name=AVERAGES,
            media_type="application/json",
            data=AVERAGE_BYTES,
        )
        _attempt(db, claim, "render", operation)
        chart_sha = chart.sha256
        averages_sha = averages.sha256
        run_id = run.id
        db.commit()

    with TestSessionLocal() as db:
        report = verify_step(
            db,
            run_id=run_id,
            step_id="render",
            assessment=_assessment(
                evidence_refs=(
                    "operation:" + "e" * 64,
                    f"artifact:{chart_sha}",
                    f"artifact:{averages_sha}",
                )
            ),
        )
        assert report.passed is True
        assert report.missing == ()
        assert report.step_id == "render"
        assert all(check.passed and check.evidence_ref for check in report.checks)
        assert report.evidence_refs  # a passing verdict names its evidence

        progress = record_verification(
            db, claim, step_id="render", report=report
        )
        assert progress.verified_steps == ("render",)
        assert progress.status == "verification_pending"
        assert len(progress.verified_outputs) == 2
        # A verified step cannot be recorded twice with a different verdict,
        # and a step already verified is not repaired.
        progress = read_progress(db, run_id)
        action = select_action(
            progress,
            None,
            policy_allows=True,
            budget_allows=True,
            mode="auto",
        )
        # render is verified, so the only step left owed evidence-shaped work
        # is the dependency; no dispatch happens here either way.
        assert action.kind == "verify"
        assert action.step_id == "fetch"

        render_action = select_action(
            progress,
            _assessment(failure="known", step_id="render"),
            policy_allows=True,
            budget_allows=True,
            mode="auto",
        )
        assert render_action.kind == "stop"
        assert render_action.step_id is None


def test_zero_exit_without_chart_is_not_completion(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        user, _project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user.id)
        _publish(
            db,
            store,
            run,
            operation,
            name=AVERAGES,
            media_type="application/json",
            data=AVERAGE_BYTES,
        )
        _attempt(db, claim, "render", operation)
        run_id = run.id
        db.commit()

    with TestSessionLocal() as db:
        report = verify_step(
            db, run_id=run_id, step_id="render", assessment=_assessment()
        )
        assert report.passed is False
        assert CHART in report.missing
        assert any(
            check.name == f"declared output {CHART}"
            for check in report.checks
            if not check.passed
        )
        assert all(
            check.passed
            for check in report.checks
            if check.name.startswith("declared output")
            and check.name != f"declared output {CHART}"
        )
        # A zero exit and one published artifact prove nothing about the chart,
        # and the answer's own claim never enters the report.
        task_report = verify_task(db, run_id=run_id, assessment=_assessment())
        assert task_report.passed is False
        assert {CHART, OBSERVATIONS} <= set(task_report.missing)
        assert read_progress(db, run_id).verified_steps == ()

        # Asking to verify a run this lane never planned is refused, not guessed.
        with pytest.raises(VerificationError, match="no step to verify"):
            verify_step(
                db,
                run_id=uuid.uuid4(),
                step_id="render",
                assessment=_assessment(),
            )


def test_wrong_averages_fail_independent_check(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        user, _project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user.id)
        _publish(
            db,
            store,
            run,
            operation,
            name=CHART,
            media_type="image/png",
            data=CHART_BYTES,
        )
        averages = _publish(
            db,
            store,
            run,
            operation,
            name=AVERAGES,
            media_type="application/json",
            data=AVERAGE_BYTES,
        )
        _attempt(db, claim, "render", operation)
        claimed = averages.sha256
        averages_key = averages.relative_storage_key
        run_id = run.id
        db.commit()

    # The record keeps claiming the identity it published; the volume does not
    # hold those bytes any more. That disagreement is the independent check.
    _tamper(store, averages_key)
    with TestSessionLocal() as db:
        report = verify_step(
            db,
            run_id=run_id,
            step_id="render",
            assessment=_assessment(evidence_refs=(f"artifact:{claimed}",)),
        )
        assert report.passed is False
        assert AVERAGES in report.missing
        assert any(
            check.name == f"stored bytes of {AVERAGES}"
            for check in report.checks
            if not check.passed
        )
        assert f"artifact:{claimed}" not in report.evidence_refs
        assert read_progress(db, run_id).verified_steps == ()
        # A verdict that says "done" cannot be recorded over the disagreement
        # the records and the volume show.
        with pytest.raises(TaskStateError, match="stored bytes"):
            record_verification(
                db,
                claim,
                step_id="render",
                report=VerificationReport(
                    step_id="render",
                    passed=True,
                    checks=(
                        VerificationCheck(
                            name="declared output averages.json",
                            passed=True,
                            evidence_ref=f"artifact:{claimed}",
                        ),
                    ),
                    evidence_refs=(f"artifact:{claimed}",),
                    missing=(),
                    explanation="the step said it was done",
                ),
            )
        assert read_progress(db, run_id).verified_steps == ()


def test_cross_scope_or_changed_artifact_fails_verification(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        user, project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user.id)
        chart = _publish(
            db,
            store,
            run,
            operation,
            name=CHART,
            media_type="image/png",
            data=CHART_BYTES,
        )
        _publish(
            db,
            store,
            run,
            operation,
            name=AVERAGES,
            media_type="application/json",
            data=AVERAGE_BYTES,
        )
        _attempt(db, claim, "render", operation)
        borrowed = Artifact(
            project_id=project.id,
            conversation_id=uuid.uuid4(),
            originating_run_id=uuid.uuid4(),
            originating_operation_id=uuid.uuid4(),
            kind="analysis_output",
            media_type="text/plain",
            display_name="borrowed.txt",
            size_bytes=4,
            sha256=hashlib.sha256(b"else").hexdigest(),
            relative_storage_key="borrowed/other",
            status="ready",
            source="tool",
        )
        db.add(borrowed)
        db.flush()
        operation.output_artifact_ids = json.dumps(
            [*json.loads(operation.output_artifact_ids), str(borrowed.id)]
        )
        borrowed_sha = borrowed.sha256
        chart_key = chart.relative_storage_key
        run_id = run.id
        db.commit()

    with TestSessionLocal() as db:
        before = verify_step(
            db, run_id=run_id, step_id="render", assessment=_assessment()
        )
        assert before.passed is False
        assert any(
            check.name == "published artifact provenance borrowed.txt"
            for check in before.checks
            if not check.passed
        )
        assert borrowed_sha not in [
            ref.removeprefix("artifact:") for ref in before.evidence_refs
        ]

    _tamper(store, chart_key)
    with TestSessionLocal() as db:
        changed = verify_step(
            db, run_id=run_id, step_id="render", assessment=_assessment()
        )
        assert changed.passed is False
        assert CHART in changed.missing


def test_user_skip_is_not_verified_success(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        user, _project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user.id)
        _publish(
            db,
            store,
            run,
            operation,
            name=CHART,
            media_type="image/png",
            data=CHART_BYTES,
        )
        _attempt(db, claim, "render", operation)
        _skip(db.query(TaskRecord).filter_by(run_id=run.id).one(), "fetch")
        run_id = run.id
        db.commit()

    with TestSessionLocal() as db:
        progress = read_progress(db, run_id)
        assert progress.skipped_steps == ("fetch",)
        assert "fetch" not in progress.verified_steps

        with pytest.raises(TaskStateError, match="skipped step"):
            record_verification(
                db,
                claim,
                step_id="fetch",
                report=VerificationReport(
                    step_id="fetch",
                    passed=True,
                    checks=(
                        VerificationCheck(
                            name="declared output observations.csv",
                            passed=True,
                            evidence_ref=OPERATION_EVIDENCE,
                        ),
                    ),
                    evidence_refs=(OPERATION_EVIDENCE,),
                    missing=(),
                    explanation="the user chose to skip this step",
                ),
            )

        step_report = verify_step(
            db, run_id=run_id, step_id="fetch", assessment=_assessment()
        )
        assert step_report.passed is False
        assert OBSERVATIONS in step_report.missing

        task_report = verify_task(db, run_id=run_id, assessment=_assessment())
        assert task_report.passed is False
        assert OBSERVATIONS in task_report.missing


def test_missing_skipped_output_blocks_dependent_step(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    with TestSessionLocal() as db:
        _user, _project, _conversation, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        _skip(db.query(TaskRecord).filter_by(run_id=run.id).one(), "fetch")
        run_id = run.id
        db.commit()

    with TestSessionLocal() as db:
        progress = read_progress(db, run_id)
        action = select_action(
            progress,
            None,
            policy_allows=True,
            budget_allows=True,
            mode="auto",
        )
        # Continuing onto the render step would run it on inputs nothing
        # validated, so the only eligible action asks for input or a revision.
        assert action.kind == "wait"
        assert "unvalidated" in action.reason
