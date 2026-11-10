"""Deterministic task switching never infers, dispatches or bypasses a wait."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from src.openship.runs.tasks.contracts import (
    TaskAction,
    TaskAssessment,
    TaskAttempt,
    TaskCounters,
    TaskDecision,
    TaskProgress,
)
from src.openship.runs.tasks.routing import select_action

REQUEST_ID = UUID("55555555-5555-5555-5555-555555555555")
OPERATION_ID = UUID("66666666-6666-6666-6666-666666666666")


def decision(*, kind: str = "plan_confirmation", revision: int = 1) -> TaskDecision:
    return TaskDecision(
        request_id=REQUEST_ID,
        kind=kind,
        plan_revision=revision,
        expected_digest="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )


def progress(
    *,
    status: str = "planned",
    step_ids: tuple[str, ...] = ("inspect", "render"),
    verified_steps: tuple[str, ...] = (),
    attempts: tuple[TaskAttempt, ...] = (),
    pending_decision: TaskDecision | None = None,
    counters: TaskCounters | None = None,
    last_disposition: str = "none",
    current_revision: int = 1,
) -> TaskProgress:
    return TaskProgress(
        current_revision=current_revision,
        status=status,
        step_ids=step_ids,
        verified_steps=verified_steps,
        attempts=attempts,
        pending_decision=pending_decision,
        counters=counters or TaskCounters(),
        last_disposition=last_disposition,
    )


def select(
    task_progress: TaskProgress,
    assessment: TaskAssessment | None = None,
    *,
    policy: bool = True,
    budget: bool = True,
) -> TaskAction:
    return select_action(
        task_progress,
        assessment,
        policy_allows=policy,
        budget_allows=budget,
    )


def test_waiting_progress_selects_a_wait_action():
    waiting_progress = progress(status="waiting", pending_decision=decision())
    action = select(waiting_progress)
    assert action.kind == "wait"
    assert action.wait_kind == "decision"

    configuration = progress(
        status="waiting",
        pending_decision=decision(kind="configuration"),
    )
    assert select(configuration).wait_kind == "configuration"

    # A waiting status without a recorded request still parks, never executes.
    assert select(progress(status="waiting")).kind == "wait"


def test_unverified_progress_never_selects_finish():
    completed_but_unverified = progress(status="completed")
    action = select(completed_but_unverified)
    assert action.kind != "finish"
    assert action.kind == "verify"
    assert action.step_id == "inspect"

    verification_pending = progress(status="verification_pending")
    assert select(verification_pending).kind == "verify"

    succeeded_unverified = progress(
        status="running",
        attempts=(
            TaskAttempt(
                step_id="inspect",
                attempt=1,
                status="succeeded",
                operation_id=OPERATION_ID,
                result_digest="a" * 64,
            ),
        ),
    )
    assert select(succeeded_unverified).kind == "verify"


def test_verified_completion_finishes_and_empty_plans_stop():
    finished = progress(
        status="completed",
        verified_steps=("inspect", "render"),
    )
    assert select(finished).kind == "finish"

    empty = progress(step_ids=())
    assert select(empty).kind == "stop"


def test_unknown_unsafe_denied_and_exhausted_states_never_execute():
    for status in ("unknown", "blocked", "failed", "cancelled"):
        action = select(progress(status=status))
        assert action.kind in {"stop", "wait"}
        assert action.kind != "execute"
        assert action.kind != "repair"

    for disposition in ("uncertain", "denied", "policy_revoked", "budget_exhausted"):
        action = select(
            progress(status="running", last_disposition=disposition),
            TaskAssessment(classification="task", explanation="Continue."),
        )
        # Task 15 splits the shape: an uncertain disposition parks the durable
        # recovery choice; a denied, revoked or exhausted one stops outright.
        if disposition == "uncertain":
            assert action.kind == "wait"
        else:
            assert action.kind == "stop"
        assert action.kind != "execute"
        assert action.kind != "repair"

    assert select(progress(), policy=False).kind == "stop"
    assert select(progress(), budget=False).kind == "stop"
    assert (
        select(
            progress(status="waiting", pending_decision=decision()), policy=False
        ).kind
        == "stop"
    )


def test_known_failure_repairs_within_the_bounded_limit():
    known = TaskAssessment(
        classification="task",
        explanation="The output path was wrong.",
        evidence_refs=("operation:" + "1" * 64,),
        failure="known",
        step_id="render",
    )
    repair = select(progress(status="running"), known)
    assert repair.kind == "repair"
    assert repair.step_id == "render"

    exhausted = progress(
        status="running",
        counters=TaskCounters(repairs_by_step={"render": 2}),
    )
    assert select(exhausted, known).kind == "stop"


@pytest.mark.parametrize("failure", ["uncertain", "denied", "policy", "budget"])
def test_uncertain_and_denied_failures_never_repair_or_execute(failure):
    assessment = TaskAssessment(
        classification="task",
        explanation="Do not replay this failure.",
        evidence_refs=("operation:" + "1" * 64,),
        failure=failure,
        step_id="render",
    )
    action = select(progress(status="running"), assessment)
    # Task 15: an uncertain reading hands the step to the durable abort/repeat/
    # continue choice, so it waits instead of stopping; a denied, policy or
    # budget failure has no choice to offer and stops.
    if failure == "uncertain":
        assert action.kind == "wait"
    else:
        assert action.kind == "stop"
    assert action.kind not in {"execute", "repair"}


def test_material_revision_is_proposed_not_executed():
    assessment = TaskAssessment(
        classification="task",
        explanation="A different data source is needed.",
        evidence_refs=("artifact:" + "1" * 64,),
        failure="material",
        step_id="render",
    )
    action = select(progress(status="running"), assessment)
    assert action.kind == "revise"
    assert action.kind not in {"execute", "repair"}


def test_clarification_and_blocked_states_wait_or_stop():
    clarification = TaskAssessment(
        classification="clarification",
        explanation="Which date range?",
        missing_information=("date range",),
    )
    assert select(progress(), clarification).kind == "wait"

    blocked_with_question = TaskAssessment(
        classification="blocked",
        explanation="The source is unavailable.",
        evidence_refs=("operation:" + "1" * 64,),
        missing_information=("a valid source",),
    )
    assert select(progress(), blocked_with_question).kind == "wait"

    terminal_blocker = TaskAssessment(
        classification="blocked",
        explanation="The requested capability is unsupported.",
        evidence_refs=("policy:" + "1" * 64,),
    )
    assert select(progress(), terminal_blocker).kind == "stop"

    informational = TaskAssessment(
        classification="informational",
        explanation="This was just a question.",
    )
    assert select(progress(), informational).kind == "stop"


def test_planned_work_dispatches_only_the_next_unverified_step():
    action = select(progress())
    assert action.kind == "execute"
    assert action.step_id == "inspect"

    after_first = progress(
        status="running",
        verified_steps=("inspect",),
    )
    assert select(after_first).step_id == "render"


def test_switch_does_not_infer_or_dispatch():
    task_progress = progress(status="running")
    assessment = TaskAssessment(classification="task", explanation="Continue.")
    snapshot = task_progress.model_dump(mode="json")
    first = select(task_progress, assessment)
    second = select(task_progress, assessment)
    assert first == second
    assert task_progress.model_dump(mode="json") == snapshot
    assert first.kind == "execute"

    import src.openship.runs.tasks.routing as routing_module

    assert not hasattr(routing_module, "dispatch_tool")
    assert not hasattr(routing_module, "invoke_model")
    assert routing_module.select_action.__module__ == "src.openship.runs.tasks.routing"
