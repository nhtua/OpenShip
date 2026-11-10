"""Bounded repair and revision policy: what a known failure may and may not do.

The switcher decides from durable progress records - attempt ordinals, their
input/result digests, recorded repair counters and the plan revision - so the
cheap cases build those records directly from the strict contracts. The one
DB case parks a real decision: a changed profile choice crosses the review
boundary, and a pending decision is durable state only the decision lane
writes.

Uncertainty belongs elsewhere on purpose: a denied, cancelled or possibly-run
dispatch is never a known failure (Task 15 owns the recovery choice), so no
repair action may be selected for it here.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from src.openship.capabilities.contracts import CapabilityRef
from src.openship.runs.tasks.contracts import (
    TaskAssessment,
    TaskAttempt,
    TaskCounters,
    TaskPlan,
    TaskProgress,
    TaskStep,
)
from src.openship.runs.tasks.decisions import park_task_wait
from src.openship.runs.tasks.routing import classify_revision, select_action
from src.openship.runs.tasks.state import save_plan
from tests.conftest import TestSessionLocal
from tests.test_task_state import _identity

RENDER = "render"
PUBLISH = "publish"
CHART = "chart.png"
AVERAGES = "averages.json"

CAPABILITY = CapabilityRef(
    id="python_execute", version="1.0.0", manifest_digest="b" * 64
)


def _step(
    *,
    step_id: str = RENDER,
    intent: str = "Chart the observations",
    profile_id: str = "standard",
    depends_on: tuple[str, ...] = (),
    expected_outputs: tuple[str, ...] = (CHART,),
) -> TaskStep:
    """One reviewed plan step, changed only as the case says."""
    return TaskStep(
        id=step_id,
        intent=intent,
        depends_on=depends_on,
        capability=CAPABILITY,
        profile_id=profile_id,
        expected_outputs=expected_outputs,
        completion_checks=("the declared output exists",),
    )


def _digest(seed: str) -> str:
    """A stable 64-hex digest for one record seed."""
    return hashlib.sha256(seed.encode()).hexdigest()


def _failed_attempts(
    count: int, *, same_inputs: bool = False
) -> tuple[TaskAttempt, ...]:
    """`count` failed attempts of the render step, digest-identical on demand."""
    return tuple(
        TaskAttempt(
            step_id=RENDER,
            attempt=ordinal,
            status="failed",
            input_digest=_digest("same" if same_inputs else f"input-{ordinal}"),
            result_digest=_digest(
                "same-result" if same_inputs else f"result-{ordinal}"
            ),
        )
        for ordinal in range(1, count + 1)
    )


def _progress(
    attempts: tuple[TaskAttempt, ...],
    *,
    repairs: int = 0,
    last_disposition: str = "none",
) -> TaskProgress:
    """Durable progress for the one-step task, carrying these attempts."""
    counters = TaskCounters(repairs_by_step={RENDER: repairs} if repairs else {})
    return TaskProgress(
        current_revision=1,
        status="running",
        step_ids=(RENDER,),
        attempts=attempts,
        counters=counters,
        last_disposition=last_disposition,
    )


def _known_failure() -> TaskAssessment:
    """The model's reading of a KeyError: a known, bounded failure."""
    return TaskAssessment(
        classification="task",
        explanation="The step exited non-zero with a KeyError on the column name.",
        failure="known",
        step_id=RENDER,
    )


def test_known_keyerror_repairs_twice_at_most():
    repairs_selected = 0
    total_attempts_for_step = 0
    for ordinal in range(1, 4):
        progress = _progress(_failed_attempts(ordinal))
        action = select_action(
            progress,
            _known_failure(),
            policy_allows=True,
            budget_allows=True,
            mode="auto",
        )
        total_attempts_for_step = ordinal
        if action.kind == "repair":
            assert action.step_id == RENDER
            repairs_selected += 1
        else:
            assert action.kind == "stop"
            assert ordinal == 3
            assert "ceiling" in action.reason
    assert repairs_selected <= 2
    assert total_attempts_for_step <= 3
    assert repairs_selected == 2 and total_attempts_for_step == 3

    # The counters ceiling agrees with the attempt ceiling: two recorded
    # repairs stop even before the third attempt row exists.
    counters_stop = select_action(
        _progress(_failed_attempts(2), repairs=2),
        _known_failure(),
        policy_allows=True,
        budget_allows=True,
        mode="auto",
    )
    assert counters_stop.kind == "stop"
    assert counters_stop.reason == "the step repair ceiling is exhausted"


def test_repeated_ineffective_repairs_stop():
    same = select_action(
        _progress(_failed_attempts(2, same_inputs=True)),
        _known_failure(),
        policy_allows=True,
        budget_allows=True,
        mode="auto",
    )
    assert same.kind == "stop"
    assert "repeated without progress" in same.reason

    # Evidence-informed repair changed the inputs: the same switch keeps
    # working within the ceilings, because this attempt proved something new.
    changed = select_action(
        _progress(_failed_attempts(2)),
        _known_failure(),
        policy_allows=True,
        budget_allows=True,
        mode="auto",
    )
    assert changed.kind == "repair"
    assert changed.step_id == RENDER


def test_profile_or_approach_revision_requires_new_copilot_confirmation():
    previous = TaskPlan(
        revision=1,
        intent="Chart the observations.",
        steps=(
            TaskStep(
                id=RENDER,
                intent="Chart the observations",
                capability=CAPABILITY,
                profile_id="standard",
                expected_outputs=("chart.png",),
                completion_checks=("chart exists",),
            ),
        ),
        available_capabilities=(CAPABILITY,),
        available_profiles=("standard", "logs-data"),
        status="validated",
    )
    proposed = previous.model_copy(
        update={
            "revision": 2,
            "steps": (
                TaskStep(
                    id=RENDER,
                    intent="Chart the observations with the analysis profile",
                    capability=CAPABILITY,
                    profile_id="logs-data",
                    expected_outputs=("chart.png",),
                    completion_checks=("chart exists",),
                ),
            ),
        }
    )
    assert classify_revision(previous, proposed) == "material"

    with TestSessionLocal() as db:
        _user, _project, _conversation, _run, claim = _identity(db)
        save_plan(db, claim, previous, expected_revision=0)
        prior_revision = 1
        save_plan(db, claim, proposed, expected_revision=1)
        request = park_task_wait(
            db,
            claim,
            kind="plan_confirmation",
            prompt="A new profile choice needs a fresh review.",
            now=datetime.now(UTC),
        ).pending_decision
        db.commit()

        assert request.plan_revision == prior_revision + 1
        progress = TaskProgress(
            current_revision=request.plan_revision,
            status="waiting",
            step_ids=(RENDER,),
            pending_decision=request,
        )
        action = select_action(
            progress,
            None,
            policy_allows=True,
            budget_allows=True,
            mode="copilot",
        )
        # Unconfirmed changed work stays gated: no dispatch until the fresh
        # review is resolved, in either mode.
        assert action.kind == "wait"
        assert action.step_id is None


def test_plan_revision_classes_split_at_the_review_boundary():
    """Only the reviewed scope stays inside its review; growth always asks."""
    reviewed = TaskPlan(
        revision=1,
        intent="Chart the supplied observations and report their averages.",
        steps=(_step(),),
        available_capabilities=(CAPABILITY,),
        available_profiles=("standard", "logs-data"),
        status="validated",
    )
    # Same steps, same capability, same profile: only what the step works on
    # changed, so an evidence-informed repair stays inside the review.
    corrected = reviewed.model_copy(
        update={
            "revision": 2,
            "steps": (
                _step(intent="Chart the observations from the corrected column"),
            ),
        }
    )
    assert classify_revision(reviewed, corrected) == "repair"

    # Re-choosing the profile is a different approach, not a corrected input:
    # it needs a new review before any changed work runs.
    reshaped = reviewed.model_copy(
        update={
            "revision": 2,
            "steps": (_step(profile_id="logs-data"),),
        }
    )
    assert classify_revision(reviewed, reshaped) == "material"

    # Growth beyond the plan, a new deliverable, or a profile outside the ones
    # the plan pinned all reach beyond what was reviewed, in either mode.
    grown = reviewed.model_copy(
        update={
            "revision": 2,
            "steps": (
                _step(),
                _step(
                    step_id=PUBLISH, depends_on=(RENDER,), expected_outputs=(AVERAGES,)
                ),
            ),
        }
    )
    assert classify_revision(reviewed, grown) == "expanded_scope"

    extra_output = reviewed.model_copy(
        update={"revision": 2, "steps": (_step(expected_outputs=(CHART, AVERAGES)),)}
    )
    assert classify_revision(reviewed, extra_output) == "expanded_scope"

    outside_profile = reviewed.model_copy(
        update={
            "revision": 2,
            "available_profiles": ("standard", "logs-data", "devops-shell"),
        }
    )
    assert classify_revision(reviewed, outside_profile) == "expanded_scope"


def test_unknown_dispatch_policy_denial_and_cancel_never_repair():
    actions = []
    for failure in ("uncertain", "denied", "policy", "budget"):
        assessment = TaskAssessment(
            classification="task",
            explanation="The dispatch may have run; the records do not say it failed.",
            failure=failure,
            step_id=RENDER,
        )
        action = select_action(
            _progress(
                (
                    TaskAttempt(
                        step_id=RENDER,
                        attempt=1,
                        status="uncertain",
                    ),
                ),
                last_disposition="uncertain",
            ),
            assessment,
            policy_allows=True,
            budget_allows=True,
            mode="auto",
        )
        actions.append(action)
        # Task 15 splits the shape: an uncertain reading parks the durable
        # choice; denied, policy and budget failures still stop outright.
        if failure == "uncertain":
            assert action.kind == "wait"
        else:
            assert action.kind == "stop"
        assert action.step_id is None

    # Even a model that calls an uncertain dispatch a known failure cannot
    # unlock a repair while the durable disposition says otherwise.
    try_known = select_action(
        _progress(_failed_attempts(1), last_disposition="uncertain"),
        _known_failure(),
        policy_allows=True,
        budget_allows=True,
        mode="auto",
    )
    actions.append(try_known)
    assert try_known.kind == "wait"
    assert try_known.step_id is None
    uncertain_execution_new_dispatches = sum(
        1 for action in actions if action.kind in {"execute", "repair"}
    )
    assert uncertain_execution_new_dispatches == 0


def test_auto_uncertainty_cannot_use_known_failure_repair():
    progress = _progress(
        (
            TaskAttempt(
                step_id=RENDER,
                attempt=1,
                status="uncertain",
            ),
        ),
        last_disposition="uncertain",
    )
    for mode in ("auto", "copilot"):
        action = select_action(
            progress,
            _known_failure(),
            policy_allows=True,
            budget_allows=True,
            mode=mode,
        )
        # Task 15: the durable uncertainty now parks the recovery ask instead
        # of stopping; either way the step is not repaired on the model's say.
        assert action.kind == "wait"
        assert action.step_id is None
    continue_with_missing_required_input_allowed = (
        action.kind == "execute" or action.kind == "repair"
    )
    assert continue_with_missing_required_input_allowed is False
