"""Versioned task contracts reject unsafe plans and keep old decoders intact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    ExecutionConfigV2,
    ExecutionConfigV3,
    ExecutionConfigV4,
    canonical_digest,
    decode_execution_config,
)
from src.openship.runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskAction,
    TaskAssessment,
    TaskAttempt,
    TaskContext,
    TaskCounters,
    TaskDecision,
    TaskDecisionCommand,
    TaskLimits,
    TaskPlan,
    TaskProgress,
    TaskStep,
    VerificationCheck,
    VerificationReport,
)
from src.openship.sandboxes.profile_schema import ProfileCatalog, catalog_digest

RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-3333-3333-333333333333")
USER_ID = UUID("44444444-4444-4444-4444-444444444444")
REQUEST_ID = UUID("55555555-5555-5555-5555-555555555555")
OPERATION_ID = UUID("66666666-6666-6666-6666-666666666666")
CLIENT_REQUEST_ID = UUID("77777777-7777-7777-7777-777777777777")


def test_recovery_contracts_bind_report_and_positive_revision():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport

    report = TaskRecoveryReport(
        plan_revision=1,
        plan_digest="a" * 64,
        step_id="inspect",
        attempt=1,
        operation_id=OPERATION_ID,
        explanation="Outcome unknown",
        eligible_actions=("abort",),
    )
    values = {
        "request_id": REQUEST_ID,
        "kind": "recovery",
        "plan_revision": 1,
        "expected_digest": report.digest,
        "expires_at": datetime.now(UTC),
        "recovery_report": report,
    }
    assert TaskDecision(**values).recovery_report == report
    for change in (
        {"recovery_report": None},
        {"plan_revision": 0},
        {"expected_digest": "b" * 64},
    ):
        with pytest.raises(ValidationError):
            TaskDecision(**(values | change))


def test_skipped_is_disjoint_from_verified():
    from src.openship.runs.tasks.contracts import TaskCallBinding

    assert TaskProgress().skipped_steps == ()
    assert TaskProgress().call_bindings == ()
    binding = TaskCallBinding(
        plan_revision=1, step_id="inspect", attempt=1, logical_call_id="model:1:call:0"
    )
    with pytest.raises(ValidationError):
        TaskProgress(
            step_ids=("inspect",),
            skipped_steps=("inspect",),
            verified_steps=("inspect",),
        )
    with pytest.raises(ValidationError):
        TaskProgress(step_ids=("inspect",), call_bindings=(binding, binding))


CAPABILITY = CapabilityRef(
    id="python_execute", version="1.0.0", manifest_digest="a" * 64
)
OTHER_CAPABILITY = CapabilityRef(
    id="download_file", version="1.0.0", manifest_digest="b" * 64
)


def profile_catalog() -> ProfileCatalog:
    metadata = {
        "label": "Data",
        "description": "Charts and log analysis.",
        "use_for": ("charts",),
        "highlights": ("Python",),
        "limitations": ("offline",),
    }
    payload = {
        "schema_version": 1,
        "profiles": {
            "logs-data": {
                "enabled": True,
                "extensions": {},
                "metadata": metadata,
                "metadata_digest": canonical_digest(metadata),
                "runtime": {
                    "image_tag": "image:tag",
                    "image_id": "sha256:" + "1" * 64,
                    "dependency_identity": "sha256:" + "2" * 64,
                    "platform": "linux/amd64",
                    "fingerprint": "2" * 64,
                    "engine_identity": "test-engine",
                    "smoke_image_id": "sha256:" + "1" * 64,
                    "smoke_fingerprint": "2" * 64,
                    "smoke_passed": True,
                    "smoke_evidence_digest": "3" * 64,
                },
            }
        },
    }
    return ProfileCatalog.model_validate(payload)


def task_context(*, mode: str = "copilot") -> TaskContext:
    return TaskContext(
        root_run_id=RUN_ID,
        project_id=PROJECT_ID,
        conversation_id=CONVERSATION_ID,
        initiator_id=USER_ID,
        scope="conversation",
        mode=mode,
        graph_version=5,
        original_intent="Chart the London observations.",
        constraints=("Use the supplied source",),
    )


def step(
    step_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    capability: CapabilityRef | None = CAPABILITY,
    profile_id: str | None = "logs-data",
    expected_outputs: tuple[str, ...] = ("chart.png",),
    completion_checks: tuple[str, ...] = ("chart exists",),
) -> TaskStep:
    return TaskStep(
        id=step_id,
        intent=f"Do {step_id}",
        depends_on=depends_on,
        capability=capability,
        profile_id=profile_id,
        expected_outputs=expected_outputs,
        completion_checks=completion_checks,
    )


def task_plan(
    steps: tuple[TaskStep, ...] | None = None,
    *,
    available_capabilities: tuple[CapabilityRef, ...] = (CAPABILITY,),
    available_profiles: tuple[str, ...] = ("logs-data",),
    revision: int = 1,
) -> TaskPlan:
    return TaskPlan(
        revision=revision,
        intent="Produce the requested chart.",
        constraints=("Use the supplied source",),
        steps=steps or (step("inspect"), step("render", depends_on=("inspect",))),
        available_capabilities=available_capabilities,
        available_profiles=available_profiles,
    )


def decision(*, kind: str = "plan_confirmation", revision: int = 1) -> TaskDecision:
    return TaskDecision(
        request_id=REQUEST_ID,
        kind=kind,
        plan_revision=revision,
        expected_digest="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )


def test_limits_defaults_match_p1_p3_and_ceilings_cannot_be_inflated():
    limits = TaskLimits()
    assert limits.max_steps == 8
    assert limits.max_tool_calls == 16
    assert limits.max_model_turns == 20
    assert limits.max_generation_attempts == 24
    assert limits.max_output_tokens_per_request == 4000
    assert limits.max_total_output_tokens == 48000
    assert limits.max_repairs_per_step == 2
    assert limits.decision_expiry_hours == 24

    assert (
        TaskLimits(
            max_steps=4,
            max_tool_calls=8,
            max_model_turns=10,
            max_generation_attempts=12,
            max_output_tokens_per_request=2000,
            max_total_output_tokens=24000,
            max_repairs_per_step=1,
            decision_expiry_hours=12,
        ).max_steps
        == 4
    )

    for field, value in (
        ("max_steps", 9),
        ("max_tool_calls", 17),
        ("max_model_turns", 21),
        ("max_generation_attempts", 25),
        ("max_output_tokens_per_request", 4001),
        ("max_total_output_tokens", 48001),
        ("max_repairs_per_step", 3),
        ("decision_expiry_hours", 25),
    ):
        with pytest.raises(ValidationError):
            TaskLimits(**{field: value})
    with pytest.raises(ValidationError):
        TaskLimits(max_steps=0)
    with pytest.raises(ValidationError):
        TaskLimits(max_steps=True)


def test_public_contracts_are_strict_frozen_and_versioned():
    for contract in (
        TaskLimits,
        TaskContext,
        TaskPlan,
        TaskStep,
        TaskAssessment,
        TaskAction,
        TaskProgress,
        TaskDecisionCommand,
        VerificationReport,
    ):
        assert contract.model_config["extra"] == "forbid"
        assert contract.model_config["frozen"] is True
        assert contract.model_config["strict"] is True
        assert contract.model_fields["schema_version"].default == 1

    with pytest.raises(ValidationError):
        TaskLimits(unexpected=1)
    with pytest.raises(ValidationError):
        TaskLimits(schema_version=2)

    limits = TaskLimits()
    with pytest.raises(ValidationError):
        limits.max_steps = 4


def test_plan_rejects_cycles_future_dependencies_duplicate_steps_and_unknown_capability():
    with pytest.raises(ValidationError):
        task_plan((step("inspect"), step("inspect")))

    with pytest.raises(ValidationError):
        task_plan((step("render", depends_on=("inspect",)), step("inspect")))

    with pytest.raises(ValidationError):
        task_plan(
            (
                step("inspect", depends_on=("render",)),
                step("render", depends_on=("inspect",)),
            )
        )

    with pytest.raises(ValidationError):
        task_plan((step("inspect", depends_on=("inspect",)),))

    with pytest.raises(ValidationError):
        task_plan((step("inspect", depends_on=("missing",)),))

    with pytest.raises(ValidationError):
        task_plan(available_capabilities=(OTHER_CAPABILITY,))

    with pytest.raises(ValidationError):
        task_plan(available_profiles=("standard",))

    with pytest.raises(ValidationError):
        task_plan((step("inspect", profile_id="not-a-profile"),))

    with pytest.raises(ValidationError):
        task_plan((step("UPPER"),))

    with pytest.raises(ValidationError):
        task_plan((step("a" * 65),))


def test_plan_accepts_ordered_steps_and_digest_changes_with_revision():
    plan = task_plan()
    assert tuple(item.id for item in plan.steps) == ("inspect", "render")
    assert plan.status == "proposed"
    assert plan.digest == plan.digest
    assert plan.digest != task_plan(revision=2).digest
    assert plan.as_validated().status == "validated"
    assert plan.as_validated().digest == plan.digest


def test_limits_and_missing_evidence_fail_closed():
    with pytest.raises(ValidationError):
        TaskLimits(max_total_output_tokens=48001)

    passing = VerificationCheck(
        name="chart exists", passed=True, evidence_ref="artifact:" + "1" * 64
    )
    failing = VerificationCheck(name="chart exists", passed=False)
    with pytest.raises(ValidationError):
        VerificationReport(
            step_id="render",
            passed=True,
            checks=(passing,),
            evidence_refs=(),
            explanation="ok",
        )
    with pytest.raises(ValidationError):
        VerificationReport(
            step_id="render",
            passed=True,
            checks=(failing,),
            evidence_refs=("artifact:" + "1" * 64,),
            explanation="ok",
        )
    with pytest.raises(ValidationError):
        VerificationReport(
            step_id="render",
            passed=True,
            checks=(passing,),
            evidence_refs=("artifact:" + "1" * 64,),
            missing=("still missing",),
            explanation="ok",
        )
    failed = VerificationReport(
        step_id="render",
        passed=False,
        checks=(failing,),
        missing=("chart.png",),
        explanation="The chart is absent.",
    )
    assert failed.passed is False

    with pytest.raises(ValidationError):
        TaskAssessment(
            classification="clarification",
            explanation="Which units?",
            missing_information=(),
        )
    with pytest.raises(ValidationError):
        TaskAssessment(
            classification="blocked",
            explanation="No source.",
            evidence_refs=(),
            missing_information=(),
        )
    with pytest.raises(ValidationError):
        VerificationCheck(name="x", passed=True, evidence_ref="../secret")
    with pytest.raises(ValidationError):
        TaskStep(
            id="inspect",
            intent="Inspect",
            expected_outputs=(),
            completion_checks=(),
        )
    with pytest.raises(ValidationError):
        step("inspect", expected_outputs=("../chart.png",))


def test_assessment_uses_bounded_public_explanation_only():
    with pytest.raises(ValidationError):
        TaskAssessment(classification="task", explanation="x" * 2001)
    with pytest.raises(ValidationError):
        TaskAssessment(
            classification="task",
            explanation="Fine.",
            agent_reasoning="private chain of thought",
        )
    with pytest.raises(ValidationError):
        TaskAssessment(classification="reasoning", explanation="Fine.")
    with pytest.raises(ValidationError):
        TaskAssessment(classification="task", explanation=" padded ")

    known_failure = TaskAssessment(
        classification="task",
        explanation="The JSON field name was wrong.",
        evidence_refs=("operation:" + "1" * 64,),
        failure="known",
        step_id="render",
    )
    assert known_failure.failure == "known"
    with pytest.raises(ValidationError):
        TaskAssessment(classification="task", explanation="Broken.", failure="known")


def test_task_action_literals_and_wait_kind():
    for kind in ("assess", "revise", "finish", "stop"):
        action = TaskAction(kind=kind, reason="reason")
        assert action.kind == kind
        assert action.step_id is None
    for kind in ("execute", "repair", "verify"):
        action = TaskAction(kind=kind, step_id="render", reason="reason")
        assert action.kind == kind
    assert (
        TaskAction(kind="wait", wait_kind="decision", reason="need input").wait_kind
        == "decision"
    )
    assert (
        TaskAction(
            kind="wait", wait_kind="configuration", reason="needs profile"
        ).wait_kind
        == "configuration"
    )

    with pytest.raises(ValidationError):
        TaskAction(kind="wait", reason="need input")
    with pytest.raises(ValidationError):
        TaskAction(kind="finish", wait_kind="decision", reason="done")
    with pytest.raises(ValidationError):
        TaskAction(kind="execute", reason="go")
    with pytest.raises(ValidationError):
        TaskAction(kind="finish", step_id="render", reason="done")
    with pytest.raises(ValidationError):
        TaskAction(kind="dispatch", reason="go")


def test_task_decision_command_bounds():
    command = TaskDecisionCommand(
        client_request_id=str(CLIENT_REQUEST_ID),
        request_id=str(REQUEST_ID),
        plan_revision=1,
        expected_digest="d" * 64,
        action="confirm",
        answer=None,
    )
    assert command.client_request_id == CLIENT_REQUEST_ID
    assert command.request_id == REQUEST_ID

    answer = TaskDecisionCommand(
        client_request_id=CLIENT_REQUEST_ID,
        request_id=REQUEST_ID,
        plan_revision=1,
        expected_digest="d" * 64,
        action="answer",
        answer="x" * 10000,
    )
    assert len(answer.answer) == 10000
    with pytest.raises(ValidationError):
        TaskDecisionCommand(
            client_request_id=CLIENT_REQUEST_ID,
            request_id=REQUEST_ID,
            plan_revision=1,
            expected_digest="d" * 64,
            action="answer",
            answer="x" * 10001,
        )
    with pytest.raises(ValidationError):
        TaskDecisionCommand(
            client_request_id=CLIENT_REQUEST_ID,
            request_id=REQUEST_ID,
            plan_revision=1,
            expected_digest="d" * 64,
            action="answer",
        )
    with pytest.raises(ValidationError):
        TaskDecisionCommand(
            client_request_id=CLIENT_REQUEST_ID,
            request_id=REQUEST_ID,
            plan_revision=1,
            expected_digest="d" * 64,
            action="confirm",
            answer="not allowed",
        )
    with pytest.raises(ValidationError):
        TaskDecisionCommand(
            client_request_id=CLIENT_REQUEST_ID,
            request_id=REQUEST_ID,
            plan_revision=0,
            expected_digest="d" * 64,
            action="confirm",
        )
    with pytest.raises(ValidationError):
        TaskDecisionCommand(
            client_request_id=CLIENT_REQUEST_ID,
            request_id=REQUEST_ID,
            plan_revision=1,
            expected_digest="not-a-digest",
            action="confirm",
        )


def test_progress_binds_pending_decision_to_revision_and_counters():
    progress = TaskProgress(
        current_revision=1,
        status="waiting",
        step_ids=("inspect", "render"),
        pending_decision=decision(),
        counters=TaskCounters(repairs_by_step={"render": 2}),
    )
    assert progress.pending_decision is not None
    assert progress.counters.repairs_by_step["render"] == 2

    with pytest.raises(ValidationError):
        TaskProgress(
            current_revision=2,
            status="waiting",
            step_ids=("inspect", "render"),
            pending_decision=decision(revision=1),
        )
    with pytest.raises(ValidationError):
        TaskProgress(
            step_ids=("inspect",),
            verified_steps=("render",),
        )
    with pytest.raises(ValidationError):
        TaskProgress(
            step_ids=("inspect",),
            attempts=(TaskAttempt(step_id="render", attempt=1, status="failed"),),
        )
    with pytest.raises(ValidationError):
        TaskCounters(repairs_by_step={"render": 3})
    with pytest.raises(ValidationError):
        TaskCounters(repairs_by_step={"UPPER": 1})


def test_execution_v5_pins_task_context_and_profile_catalog():
    catalog = profile_catalog()
    execution = ExecutionConfigV5(
        aws_read_catalog_digest="e" * 64,
        task_context=task_context(),
        profile_catalog=catalog,
        profile_catalog_digest=catalog_digest(catalog),
    )
    assert execution.schema_version == 5
    assert execution.task_limits == TaskLimits()

    payload = execution.model_dump(mode="json")
    decoded = decode_execution_config(payload)
    assert isinstance(decoded, ExecutionConfigV5)
    assert decoded.profile_catalog == catalog

    with pytest.raises(ValidationError):
        ExecutionConfigV5(
            aws_read_catalog_digest="e" * 64,
            task_context=task_context(),
            profile_catalog=catalog,
            profile_catalog_digest="f" * 64,
        )
    with pytest.raises(ValidationError):
        ExecutionConfigV5(
            graph_kind="tool",
            aws_read_catalog_digest="e" * 64,
            task_context=task_context(),
            profile_catalog=catalog,
            profile_catalog_digest=catalog_digest(catalog),
        )
    with pytest.raises(ValidationError):
        ExecutionConfigV5(
            graph_version=4,
            aws_read_catalog_digest="e" * 64,
            task_context=task_context(),
            profile_catalog=catalog,
            profile_catalog_digest=catalog_digest(catalog),
        )
    with pytest.raises(ValidationError):
        ExecutionConfigV5(
            aws_read_catalog_digest="e" * 64,
            task_context=task_context().model_copy(update={"graph_version": 4}),
            profile_catalog=catalog,
            profile_catalog_digest=catalog_digest(catalog),
        )
    with pytest.raises(ValidationError):
        ExecutionConfigV5(
            aws_read_catalog_digest="e" * 64,
            profile_catalog=catalog,
            profile_catalog_digest=catalog_digest(catalog),
        )
    assert "agent_reasoning" not in ExecutionConfigV5.model_fields


def test_old_execution_versions_decode_without_task_fields():
    old_v4 = {
        "schema_version": 4,
        "graph_kind": "chat",
        "graph_version": 4,
        "aws_read_catalog_digest": "a" * 64,
    }
    decoded_v4 = decode_execution_config(old_v4)
    assert isinstance(decoded_v4, ExecutionConfigV4)
    assert decoded_v4.schema_version == 4
    assert not hasattr(decoded_v4, "task_context")
    assert "task_context" not in decoded_v4.model_dump()
    with pytest.raises(ValidationError):
        decode_execution_config({**old_v4, "task_context": {}})

    assert isinstance(
        decode_execution_config(
            {"schema_version": 1, "graph_kind": "chat", "graph_version": 1}
        ),
        ExecutionConfig,
    )
    assert isinstance(
        decode_execution_config(
            {"schema_version": 2, "graph_kind": "chat", "graph_version": 2}
        ),
        ExecutionConfigV2,
    )
    assert isinstance(
        decode_execution_config(
            {"schema_version": 3, "graph_kind": "chat", "graph_version": 3}
        ),
        ExecutionConfigV3,
    )
    with pytest.raises(ValueError, match="unsupported execution configuration version"):
        decode_execution_config({"schema_version": 6, "graph_kind": "chat"})


def test_agent_runtime_responsibilities_are_not_registered_tools():
    from src.openship.capabilities.catalog import list_capability_manifests

    ids = {manifest.id for manifest in list_capability_manifests()}
    assert ids.isdisjoint(
        {
            "agent_reasoning",
            "agent_planning",
            "agent_tracking",
            "agent_switch",
        }
    )
