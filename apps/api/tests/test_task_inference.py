"""Task inference answers with validated records; it never dispatches work."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.openship.capabilities.contracts import (
    LoopLimits,
    PublishedToolResult,
    ToolFailure,
)
from src.openship.config import settings
from src.openship.execution.models import ToolOperation
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import RunLimits
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import _adapter_factories, load_profiles
from src.openship.providers.runner import authorize_current_profile
from src.openship.runs.budget import RunBudget
from src.openship.runs.models import ProviderAttempt
from src.openship.runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskAttempt,
    TaskContext,
    TaskLimits,
    TaskPlan,
    TaskProgress,
    TaskStep,
)
from src.openship.runs.tasks.decisions import park_task_wait
from src.openship.runs.tasks.inference import (
    assess_task,
    evidence_only_assessment,
    plan_task,
)
from src.openship.runs.tasks.models import (
    TaskDecisionRecord,
    TaskPlanRecord,
    TaskRecord,
)
from src.openship.runs.tasks.routing import select_action
from src.openship.runs.tasks.state import read_progress, save_plan
from src.openship.runs.transcript import read_transcript
from src.openship.sandboxes.profile_schema import ProfileCatalog, catalog_digest
from tests.conftest import TestSessionLocal
from tests.providers.conftest import (
    control,
    provider_stream,
    serve_requests,
)
from tests.test_task_state import CAPABILITY, _identity

FIXTURES = Path(__file__).parent / "fixtures" / "agentic_tasks"
EMPTY_CATALOG = ProfileCatalog(schema_version=1, profiles={})


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _responses_spec(profile_id: str = "responses-fixture") -> dict:
    """A responses-shaped profile the installation can declare."""
    from tests.providers.conftest import CREDENTIAL_REF, FIXTURE_ENDPOINT, FIXTURE_MODEL

    return {
        "id": profile_id,
        "label": "Fixture responses profile",
        "adapter": "openai",
        "model": FIXTURE_MODEL,
        "endpoint": FIXTURE_ENDPOINT,
        "api_version": "responses",
        "credential_ref": CREDENTIAL_REF,
        "capabilities": {
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": 32768,
            "max_output_tokens": 4096,
            "token_counting": False,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "allowed_fallback_profile_ids": [],
        "qualified_model_aliases": [],
        "adapter_options": {
            "adapter": "openai",
            "output_limit_parameter": "max_output_tokens",
            "reasoning_effort": "low",
        },
    }


def _create_envelope(text: str):
    """A Responses-shaped stream that answers with this envelope once."""
    from tests.providers.conftest import FIXTURE_MODEL, StreamTransport

    item = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    response = SimpleNamespace(
        status="completed", output=[item], model=FIXTURE_MODEL, usage=None
    )
    transport = StreamTransport(
        [
            SimpleNamespace(type="response.output_text.delta", delta=text),
            SimpleNamespace(type="response.completed", response=response),
        ]
    )
    return transport.create


def _answer(name: str) -> dict:
    """One synthetic request answered with the fixture's whole envelope."""
    return {"chunks": provider_stream("openai", text=[_fixture(name)], finish="stop")}


def _observations(status: str = "succeeded") -> tuple[PublishedToolResult, ...]:
    """The retained record the stage is shown, as position-labelled evidence."""
    result = json.loads(_fixture("observations_logs.json"))
    error = (
        ToolFailure(
            code="execution_interrupted", message="the sandbox stopped mid-write"
        )
        if status == "interrupted"
        else None
    )
    return (
        PublishedToolResult(
            operation_id=uuid.uuid4(), status=status, result=result, error=error
        ),
    )


def _v5_execution(run, user_id, *, allowed=()):
    return ExecutionConfigV5(
        aws_read_catalog_digest="e" * 64,
        task_context=TaskContext(
            root_run_id=run.id,
            project_id=run.project_id,
            conversation_id=run.conversation_id,
            initiator_id=user_id,
            scope="conversation",
            mode="copilot",
            graph_version=5,
            original_intent="Chart the supplied observations and report their averages.",
        ),
        allowed_capabilities=tuple(allowed),
        profile_catalog=EMPTY_CATALOG,
        profile_catalog_digest=catalog_digest(EMPTY_CATALOG),
        task_limits=TaskLimits(),
        loop_limits=LoopLimits(deadline_seconds=120),
    )


def _running_v5_run(*, allowed=()):
    with TestSessionLocal() as db:
        _user, _project, _conversation, run, claim = _identity(db)
        execution = _v5_execution(run, _user.id, allowed=allowed)
        run.execution_configuration = execution.model_dump_json()
        db.commit()
        return execution, claim


def _validated_plan() -> TaskPlan:
    """The server's own plan for the fixture request, used to seed progress."""
    steps = tuple(
        TaskStep(
            id=name,
            intent=intent,
            depends_on=deps,
            capability=CAPABILITY,
            expected_outputs=outputs,
            completion_checks=(f"{name} finished",),
        )
        for name, intent, deps, outputs in (
            ("inspect", "Inspect the supplied observations", (), ("summary.json",)),
            (
                "render",
                "Chart the observations and compute averages",
                ("inspect",),
                ("chart.png", "averages.json"),
            ),
        )
    )
    return TaskPlan(
        revision=1,
        intent="Chart the supplied observations and report their averages.",
        steps=steps,
        available_capabilities=(CAPABILITY,),
        status="validated",
    )


def _invocation(execution, claim, monkeypatch, behaviors):
    """The stage's view of this run: its pinned snapshot and durable budget."""
    registry = load_profiles(settings)
    model_config = registry.resolve_task(
        None, task_limits=RunLimits(max_attempts=3, max_output_tokens=4_000)
    )
    transport = serve_requests(monkeypatch, behaviors)
    invocation = SimpleNamespace(
        claim=claim,
        control=control(),
        recorder=DatabaseAttemptRecorder(
            lambda: TestSessionLocal(), claim.run_id, 1, commit_each=True
        ),
        budget=RunBudget(
            TestSessionLocal,
            claim,
            RunLimits(max_attempts=64, max_output_tokens=4_000),
            execution,
        ),
        session_factory=TestSessionLocal,
        authorize_profile=authorize_current_profile,
        execution=execution,
        model_config=model_config,
    )
    return invocation, transport


def _dispatched(db, run_id) -> list[str]:
    return [
        row.logical_call_id
        for row in db.query(ToolOperation).filter_by(run_id=run_id).all()
    ]


def _decision_rows(db, run_id):
    return db.query(TaskDecisionRecord).filter_by(run_id=run_id).all()


def _command_rows(db, run_id):
    from src.openship.runs.tasks.models import TaskDecisionCommandRecord

    return db.query(TaskDecisionCommandRecord).filter_by(run_id=run_id).all()


def _attempt_rows(db, run_id) -> int:
    return db.query(ProviderAttempt).filter_by(run_id=run_id).count()


def _pre_plan() -> TaskProgress:
    return TaskProgress(current_revision=0, status="unknown")


def test_greeting_answers_without_confirmation(monkeypatch):
    execution, claim = _running_v5_run()
    invocation, _transport = _invocation(
        execution, claim, monkeypatch, [_answer("greeting.json")]
    )
    progress = _pre_plan()
    assessment = assess_task(invocation, progress)
    assert assessment.classification == "informational"
    action = select_action(progress, assessment, policy_allows=True, budget_allows=True)
    assert action.kind == "stop"
    assert action.reason == "informational submissions do not execute planned work"
    with TestSessionLocal() as db:
        gateway_calls = _dispatched(db, claim.run_id)
        greeting_decision = (_decision_rows(db, claim.run_id) or [None])[0]
        attempts = _attempt_rows(db, claim.run_id)
    assert gateway_calls == []
    assert greeting_decision is None
    assert attempts == 1


def test_actionable_request_cannot_dispatch_during_planning(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    invocation, transport = _invocation(
        execution,
        claim,
        monkeypatch,
        [_answer("assessment_task.json"), _answer("plan_chart.json")],
    )
    assessment = assess_task(invocation, _pre_plan(), observations=_observations())
    assert assessment.classification == "task"
    accepted_plan = plan_task(invocation, _pre_plan(), assessment)
    assert accepted_plan.status == "validated"
    assert accepted_plan.steps[-1].expected_outputs == ("chart.png", "averages.json")

    with TestSessionLocal() as db:
        save_plan(db, claim, accepted_plan, expected_revision=0)
        db.commit()
        planned = read_progress(db, claim.run_id)
        gateway_calls = _dispatched(db, claim.run_id)
    assert planned.status == "planned"
    assert gateway_calls == []

    copilot = select_action(
        planned, None, policy_allows=True, budget_allows=True, mode="copilot"
    )
    assert copilot.kind == "wait" and copilot.wait_kind == "decision"
    assert (
        copilot.reason == "a proposed plan requires confirmation before any step runs"
    )
    auto = select_action(
        planned, None, policy_allows=True, budget_allows=True, mode="auto"
    )
    assert auto.kind == "execute"
    assert transport.requests == 2


def test_missing_units_requests_focused_input(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    invocation, transport = _invocation(
        execution, claim, monkeypatch, [_answer("assessment_clarification.json")]
    )
    assessment = assess_task(invocation, _pre_plan(), observations=_observations())
    assert assessment.classification == "clarification"
    assert assessment.missing_information == ("the units the recorded readings use",)
    action = select_action(
        _pre_plan(), assessment, policy_allows=True, budget_allows=True
    )
    assert action.kind == "wait"

    with TestSessionLocal() as db:
        save_plan(db, claim, _validated_plan(), expected_revision=0)
        db.commit()
    prompt = "Which units do the recorded readings use?"
    with TestSessionLocal() as db:
        park_task_wait(
            db, claim, kind="clarification", prompt=prompt, now=datetime.now(UTC)
        )
        db.commit()
        decisions = _decision_rows(db, claim.run_id)
        task = db.query(TaskRecord).filter_by(run_id=claim.run_id).one()
        attempts = _attempt_rows(db, claim.run_id)
    assert len(decisions) == 1
    assert decisions[0].kind == "clarification"
    assert json.loads(decisions[0].request_json)["prompt"] == prompt
    assert task.status == "waiting"
    assert attempts == 1
    assert transport.requests == 1


def test_malformed_envelope_consumes_attempts(monkeypatch):
    execution, claim = _running_v5_run()
    invocation, transport = _invocation(
        execution,
        claim,
        monkeypatch,
        [_answer("envelope_duplicate.json"), _answer("assessment_task.json")],
    )
    assessment = assess_task(invocation, _pre_plan(), observations=_observations())
    assert assessment.classification == "task"
    assert transport.requests == 2
    with TestSessionLocal() as db:
        assert _attempt_rows(db, claim.run_id) == 2
        entries = read_transcript(db, claim.run_id)
    assert [entry.model_turn_ordinal for entry in entries] == [0, 1]
    assert (
        entries[0].message.content.strip()
        == _fixture("envelope_duplicate.json").strip()
    )


def test_untrusted_log_instructions_do_not_expand_plan_authority(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    invocation, transport = _invocation(
        execution,
        claim,
        monkeypatch,
        [_answer("assessment_task.json"), _answer("plan_in_log.json")],
    )
    assessment = assess_task(invocation, _pre_plan(), observations=_observations())
    assert assessment.classification == "task"
    assert assessment.evidence_refs == ("obs:0",)
    assert "delete_records" not in assessment.explanation

    with pytest.raises(ProviderError) as refused:
        plan_task(invocation, _pre_plan(), assessment, corrections=0)
    assert refused.value.code == "invalid_response"

    with TestSessionLocal() as db:
        gateway_calls = _dispatched(db, claim.run_id)
        refused_task_records = len(
            db.query(TaskRecord).filter_by(run_id=claim.run_id).all()
        )
        refused_plan_revisions = len(
            db.query(TaskPlanRecord).filter_by(run_id=claim.run_id).all()
        )
        attempts = _attempt_rows(db, claim.run_id)
    assert gateway_calls == []
    assert refused_task_records == 0
    assert refused_plan_revisions == 0
    assert attempts == 2
    assert transport.requests == 2


def test_no_four_model_calls_for_deterministic_progress(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    _stage, transport = _invocation(
        execution, claim, monkeypatch, [_answer("assessment_task.json")]
    )
    with TestSessionLocal() as db:
        save_plan(db, claim, _validated_plan(), expected_revision=0)
        db.commit()
        planned = read_progress(db, claim.run_id)
    action = select_action(
        planned, None, policy_allows=True, budget_allows=True, mode="auto"
    )
    assert action.kind == "execute" and action.step_id == "inspect"
    with TestSessionLocal() as db:
        no_progress_route_inference_calls = _attempt_rows(db, claim.run_id)
    assert no_progress_route_inference_calls == 0
    assert transport.requests == 0


def test_uncertain_assessment_diagnoses_without_repeating_command(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    invocation, transport = _invocation(
        execution, claim, monkeypatch, [_answer("diagnosis_uncertain.json")]
    )
    diagnosis = TaskProgress(
        current_revision=1,
        status="running",
        step_ids=("inspect", "render"),
        attempts=(TaskAttempt(step_id="inspect", attempt=1, status="interrupted"),),
        last_disposition="uncertain",
    )
    assessment = assess_task(
        invocation, diagnosis, observations=_observations(status="interrupted")
    )
    assert assessment.classification == "task"
    assert assessment.failure == "uncertain"
    assert assessment.step_id == "inspect"
    assert assessment.recommended_recovery_action == "repeat_step"
    assert assessment.unknowns == (
        "whether the sandbox wrote anything before it stopped",
    )

    action = select_action(
        diagnosis, assessment, policy_allows=True, budget_allows=True
    )
    assert action.kind == "wait"
    assert action.reason == "work may have dispatched without a settled result"
    open_diagnosis = TaskProgress(
        current_revision=1,
        status="running",
        step_ids=("inspect", "render"),
        attempts=(TaskAttempt(step_id="inspect", attempt=1, status="interrupted"),),
    )
    assert (
        select_action(
            open_diagnosis, assessment, policy_allows=True, budget_allows=True
        ).reason
        == "an uncertain failure needs the durable recovery choice"
    )

    uncertain_diagnosis_gateway_calls = []
    with TestSessionLocal() as db:
        uncertain_diagnosis_gateway_calls = _dispatched(db, claim.run_id)
        decisions = _decision_rows(db, claim.run_id)
        attempts = _attempt_rows(db, claim.run_id)
    assert uncertain_diagnosis_gateway_calls == []
    assert decisions == []
    assert attempts == 1
    assert transport.requests == 1


def test_recovery_recommendation_is_not_user_consent(monkeypatch):
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    invocation, _transport = _invocation(
        execution, claim, monkeypatch, [_answer("diagnosis_uncertain.json")]
    )
    diagnosis = TaskProgress(
        current_revision=1,
        status="running",
        step_ids=("inspect", "render"),
        attempts=(TaskAttempt(step_id="inspect", attempt=1, status="interrupted"),),
        last_disposition="uncertain",
    )
    assessment = assess_task(
        invocation, diagnosis, observations=_observations(status="interrupted")
    )
    assert assessment.recommended_recovery_action == "repeat_step"

    with TestSessionLocal() as db:
        recommendation_consumed_decision_count = len(_command_rows(db, claim.run_id))
        decisions = len(_decision_rows(db, claim.run_id))
    assert recommendation_consumed_decision_count == 0
    assert decisions == 0

    fallback = evidence_only_assessment(diagnosis, _observations(status="interrupted"))
    assert fallback.recommended_recovery_action is None
    assert fallback.failure == "uncertain"
    assert fallback.evidence_refs == ("obs:0",)


def test_responses_continuation_is_recorded_once_and_replayed(monkeypatch):
    """A Responses-shaped turn is replayed from its record, not asked again."""
    from src.openship.providers.openai_responses import OpenAIResponsesAdapter
    from tests.providers.conftest import CREDENTIAL_REF

    execution, claim = _running_v5_run()
    from tests.conftest import configure_profiles

    execution, claim = _running_v5_run()
    spec = _responses_spec()
    configure_profiles(monkeypatch, [spec], spec["id"])
    monkeypatch.setenv(CREDENTIAL_REF, "sentinel-credential-value")
    monkeypatch.setitem(
        _adapter_factories,
        "openai",
        lambda profile: OpenAIResponsesAdapter(
            client_factory=lambda _config: SimpleNamespace(
                responses=SimpleNamespace(
                    create=_create_envelope(_fixture("greeting.json"))
                )
            )
        ),
    )
    registry = load_profiles(settings)
    model_config = registry.resolve_task(
        spec["id"], task_limits=RunLimits(max_attempts=8, max_output_tokens=4_000)
    )
    invocation = SimpleNamespace(
        claim=claim,
        control=control(),
        recorder=DatabaseAttemptRecorder(
            lambda: TestSessionLocal(), claim.run_id, 1, commit_each=True
        ),
        budget=RunBudget(
            TestSessionLocal,
            claim,
            RunLimits(max_attempts=8, max_output_tokens=4_000),
            execution,
        ),
        session_factory=TestSessionLocal,
        authorize_profile=authorize_current_profile,
        execution=execution,
        model_config=model_config,
    )
    assessment = assess_task(invocation, _pre_plan())
    assert assessment.classification == "informational"
    replayed = assess_task(invocation, _pre_plan())
    assert replayed.model_dump(mode="json") == assessment.model_dump(mode="json")
    with TestSessionLocal() as db:
        entries = read_transcript(db, claim.run_id)
        attempts = _attempt_rows(db, claim.run_id)
    assert len(entries) == 1
    assert entries[0].message.continuation is not None
    assert attempts == 1
