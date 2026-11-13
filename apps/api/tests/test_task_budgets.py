"""Task-scoped budgets share root caps and honest active windows."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.openship.capabilities.contracts import LoopLimits
from src.openship.config import Settings, settings
from src.openship.providers.contracts import (
    ChatMessage,
    RunLimits,
    ToolCall,
    pin_profile,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import load_profiles
from src.openship.runs.budget import RunBudget
from src.openship.runs.models import ProviderAttempt
from src.openship.runs.tasks.clock import (
    attempt_ceiling_allows,
    charge_interrupted_window,
    close_task_window,
    effective_loop_limits,
    open_task_window,
)
from src.openship.runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskAssessment,
    TaskAttempt,
    TaskContext,
    TaskCounters,
    TaskLimits,
    TaskProgress,
)
from src.openship.runs.tasks.decisions import park_task_wait
from src.openship.runs.tasks.models import TaskRecord
from src.openship.runs.tasks.routing import select_action
from src.openship.runs.tasks.state import save_plan
from src.openship.runs.transcript import append_transcript
from src.openship.sandboxes.profile_schema import ProfileCatalog, catalog_digest
from tests.conftest import TestSessionLocal, configure_profiles, profile_spec
from tests.test_task_state import CAPABILITY, _identity, _plan

EMPTY_CATALOG = ProfileCatalog(schema_version=1, profiles={})


def _v5_execution(run, user_id, *, deadline_seconds=120, task_limits=None, allowed=()):
    """The pinned v5 snapshot the run carries, with the task ceilings under test."""
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
            original_intent="Chart the supplied observations.",
        ),
        allowed_capabilities=tuple(allowed),
        profile_catalog=EMPTY_CATALOG,
        profile_catalog_digest=catalog_digest(EMPTY_CATALOG),
        task_limits=task_limits or TaskLimits(),
        loop_limits=LoopLimits(deadline_seconds=deadline_seconds),
    )


def _running_v5_run(*, deadline_seconds=120, task_limits=None, allowed=()):
    """A claimed v5 task run holding one saved plan revision."""
    with TestSessionLocal() as db:
        user, _project, _conversation, run, claim = _identity(db)
        execution = _v5_execution(
            run,
            user.id,
            deadline_seconds=deadline_seconds,
            task_limits=task_limits,
            allowed=allowed,
        )
        run.execution_configuration = execution.model_dump_json()
        db.commit()
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        return execution, claim


def _budget(execution, claim):
    return RunBudget(
        TestSessionLocal,
        claim,
        RunLimits(max_attempts=64, max_output_tokens=4_000),
        execution,
    )


def _task_state(db, run_id):
    task = db.query(TaskRecord).filter_by(run_id=run_id).one()
    return (
        int(task.active_ms or 0),
        task.counters_json,
        task.verified_steps_json,
        task.last_disposition,
    )


def _progress(*, repairs=1, attempts=2):
    return TaskProgress(
        current_revision=1,
        status="running",
        step_ids=("inspect",),
        counters=TaskCounters(repairs_by_step={"inspect": repairs}),
        attempts=tuple(
            TaskAttempt(step_id="inspect", attempt=ordinal, status="failed")
            for ordinal in range(1, attempts + 1)
        ),
    )


def test_all_inference_and_repairs_share_root_caps():
    execution, _claim = _running_v5_run()
    effective = effective_loop_limits(execution)
    assert effective.max_tool_calls == 16 and effective.max_model_turns == 20
    assert effective.max_generation_attempts == 24 and effective.max_total_output_tokens == 48_000
    assert effective.max_output_tokens_per_request == 4_000

    execution, claim = _running_v5_run()
    budget = _budget(execution, claim)
    profile = pin_profile(profile_spec("fixture-primary"))
    reservations = [
        budget.reserve_generation(
            profile,
            reason="initial" if turn == 0 else "retry",
            model_turn_ordinal=turn % 20,
            requested_output_tokens=1_000,
            reserved_tokens=None,
            reserved_cost=None,
        )
        for turn in range(24)
    ]
    assert len({item.attempt_id for item in reservations}) == 24
    with TestSessionLocal() as db:
        assert (
            db.query(ProviderAttempt)
            .filter_by(run_id=claim.run_id)
            .count()
            == 24
        )
    with pytest.raises(ProviderError) as refused:
        budget.reserve_generation(
            profile,
            reason="retry",
            model_turn_ordinal=1,
            requested_output_tokens=1_000,
            reserved_tokens=None,
            reserved_cost=None,
        )
    assert refused.value.code == "budget_exceeded"


def test_tool_count_is_not_model_turn_ordinal():
    execution, claim = _running_v5_run(allowed=(CAPABILITY,))
    budget = _budget(execution, claim)
    admitted = 0
    refused = None
    for turn in range(17):
        call = ToolCall(
            provider_call_id=f"provider-call-{turn}",
            name=CAPABILITY.id,
            arguments={"query": "inspect"},
        )
        with TestSessionLocal() as db:
            append_transcript(
                db,
                claim,
                turn,
                ChatMessage(role="assistant", content="", tool_calls=(call,)),
            )
            db.commit()
        try:
            budget.admit_tool(f"model:{turn}:call:0")
        except ProviderError as exc:
            refused = exc
            break
        admitted += 1
        with TestSessionLocal() as db:
            append_transcript(
                db,
                claim,
                turn,
                ChatMessage(
                    role="tool",
                    name=CAPABILITY.id,
                    tool_call_id=call.provider_call_id,
                    content='{"status":"succeeded"}',
                ),
            )
            db.commit()
    assert admitted == 16
    assert refused is not None and refused.code == "tool_limit_exceeded"
    with TestSessionLocal() as db:
        assert (
            db.query(ProviderAttempt).filter_by(run_id=claim.run_id).count()
            == 0
        )


def test_human_wait_excludes_time_but_not_previous_consumption():
    _execution, claim = _running_v5_run(deadline_seconds=120)
    started = datetime(2026, 11, 9, 12, tzinfo=UTC)
    with TestSessionLocal() as db:
        deadline = open_task_window(db, claim, now=started)
        db.commit()
    assert deadline == started + timedelta(seconds=120)

    with TestSessionLocal() as db:
        close_task_window(db, claim, now=started + timedelta(seconds=30))
        db.commit()
        charged_ms, counters_before, _verified, _disposition = _task_state(
            db, claim.run_id
        )
    assert charged_ms == 30_000

    with TestSessionLocal() as db:
        resumed = open_task_window(db, claim, now=started + timedelta(seconds=31))
        db.commit()
    assert resumed == started + timedelta(seconds=121)
    with TestSessionLocal() as db:
        charged_after, counters_after, _verified, _disposition = _task_state(
            db, claim.run_id
        )
    assert charged_after == 30_000
    assert counters_after == counters_before


def test_crash_does_not_refund_unsettled_active_window():
    _execution, claim = _running_v5_run(deadline_seconds=120)
    started = datetime(2026, 11, 9, 12, tzinfo=UTC)
    with TestSessionLocal() as db:
        deadline = open_task_window(db, claim, now=started)
        db.commit()
    assert deadline == started + timedelta(seconds=120)
    with TestSessionLocal() as db:
        charge_interrupted_window(db, claim, now=deadline + timedelta(seconds=60))
        db.commit()
        charged_ms, _counters, _verified, _disposition = _task_state(
            db, claim.run_id
        )
        task = db.query(TaskRecord).filter_by(run_id=claim.run_id).one()
        unresolved = (task.active_started_at, task.active_deadline_at)
    assert charged_ms == 120_000
    assert unresolved == (None, None)


def test_task_defaults_respect_explicit_lower_provider_limits(monkeypatch):
    explicit = {
        **profile_spec("explicit-ceiling"),
        "limits": {"max_attempts": 2, "max_output_tokens": 1_000},
    }
    open_ended = profile_spec("open-ended")
    configure_profiles(monkeypatch, [explicit, open_ended], "explicit-ceiling")
    registry = load_profiles(settings)
    assert settings.agentic_max_generation_attempts == 24
    with pytest.raises(ValidationError):
        Settings(agentic_max_generation_attempts=25)

    legacy = registry.pin_selection("open-ended")
    assert legacy.limits.max_attempts == 3

    task_limits = RunLimits(max_attempts=24, max_output_tokens=4_000)
    explicit_profile_limit_2 = registry.resolve_task(
        None, task_limits=task_limits
    )
    assert explicit_profile_limit_2.limits.max_attempts == 2
    assert explicit_profile_limit_2.limits.max_output_tokens == 1_000
    assert (
        registry.resolve_task("open-ended", task_limits=task_limits).limits.max_attempts
        == 24
    )
    assert (
        registry.resolve_task(
            "open-ended", task_limits=RunLimits(max_attempts=8, max_output_tokens=4_000)
        ).limits.max_attempts
        == 8
    )

    declared = pin_profile(explicit)
    execution, _claim = _running_v5_run()
    effective = effective_loop_limits(execution, profile_limits=declared.limits)
    assert effective.max_generation_attempts == 2
    assert effective.max_output_tokens_per_request == 1_000
    assert effective.max_tool_calls == 16


def test_recovery_diagnosis_and_user_repeat_keep_root_consumption():
    execution, claim = _running_v5_run()
    budget = _budget(execution, claim)
    profile = pin_profile(profile_spec("fixture-primary"))
    diagnosis = budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=0,
        requested_output_tokens=1_000,
        reserved_tokens=None,
        reserved_cost=None,
    )
    assert diagnosis.tool_calls_allowed is True
    with TestSessionLocal() as db:
        _charged, counters_before = _task_state(db, claim.run_id)[:2]
    with TestSessionLocal() as db:
        park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source should the chart use?",
            now=datetime.now(UTC),
        )
        db.commit()
    with TestSessionLocal() as db:
        _charged, counters_after = _task_state(db, claim.run_id)[:2]
        attempts = (
            db.query(ProviderAttempt).filter_by(run_id=claim.run_id).count()
        )
        task = db.query(TaskRecord).filter_by(run_id=claim.run_id).one()
        window = (task.active_started_at, task.active_deadline_at)
    assert counters_after == counters_before
    assert attempts == 1
    assert window == (None, None)
    assert attempt_ceiling_allows(
        _progress(repairs=0, attempts=1), "inspect", limits=TaskLimits()
    ) is True


def test_lowered_repair_limit_blocks_automatic_and_user_repeat():
    lowered = TaskLimits(max_repairs_per_step=1)
    execution, _claim = _running_v5_run(task_limits=lowered)
    effective = effective_loop_limits(execution)
    assert effective.max_repairs_per_step == 1

    exhausted = _progress(repairs=1, attempts=2)
    action = select_action(
        exhausted,
        TaskAssessment(
            classification="task",
            explanation="The recorded command failed and can be retried once more.",
            failure="known",
            step_id="inspect",
        ),
        policy_allows=True,
        budget_allows=True,
        limits=lowered,
    )
    assert action.kind == "stop"
    assert action.reason == "the step repair ceiling is exhausted"

    repeat_at_attempt_ceiling_allowed = attempt_ceiling_allows(
        exhausted, "inspect", limits=lowered
    )
    assert repeat_at_attempt_ceiling_allowed is False
    assert (
        attempt_ceiling_allows(
            _progress(repairs=0, attempts=1), "inspect", limits=lowered
        )
        is True
    )
    assert (
        select_action(
            _progress(repairs=0, attempts=1),
            TaskAssessment(
                classification="task",
                explanation="The recorded command failed once and stays inside the ceiling.",
                failure="known",
                step_id="inspect",
            ),
            policy_allows=True,
            budget_allows=True,
            limits=lowered,
        ).kind
        == "repair"
    )
