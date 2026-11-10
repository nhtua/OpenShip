"""Deterministic task switching for the bounded v5 task graph.

This module only chooses the next validated action. It performs no model
inference, dispatch, persistence or authority check; callers supply the
policy and budget decisions and remain responsible for enforcing them.
"""

from __future__ import annotations

from typing import Literal

from .contracts import TaskAction, TaskAssessment, TaskLimits, TaskPlan, TaskProgress

_LIMITS = TaskLimits()
_TERMINAL_STATUSES = frozenset({"unknown", "blocked", "failed", "cancelled"})
_UNSAFE_DISPOSITIONS = frozenset({"denied", "policy_revoked", "budget_exhausted"})
_UNSAFE_FAILURES = frozenset({"denied", "policy", "budget"})


def _stop(reason: str) -> TaskAction:
    return TaskAction(kind="stop", reason=reason)


def _wait(reason: str, *, wait_kind: str = "decision") -> TaskAction:
    return TaskAction(kind="wait", wait_kind=wait_kind, reason=reason)


def has_open_uncertainty(progress: TaskProgress) -> bool:
    """Work may have dispatched without a settled outcome, on durable rows only.

    A bound logical call whose step attempt never settled is uncertainty, not
    absence: the call may have run, and Task 15's diagnosis plus abort/repeat/
    continue choice owns that, so no switch here may re-run or skip it. An
    attempt recorded as uncertain or an uncertain last disposition says the
    same; a settled interrupted attempt is a known outcome and stays out.
    """
    if progress.last_disposition == "uncertain":
        return True
    if any(attempt.status == "uncertain" for attempt in progress.attempts):
        return True
    settled = {(attempt.step_id, attempt.attempt) for attempt in progress.attempts}
    return any(
        (binding.step_id, binding.attempt) not in settled
        for binding in progress.call_bindings
    )


def _step_action(kind: str, step_id: str, reason: str) -> TaskAction:
    return TaskAction(kind=kind, step_id=step_id, reason=reason)


def _repair_made_no_progress(progress: TaskProgress, step_id: str) -> bool:
    """Whether the step's last two attempts carry the same evidence identity.

    A repair that repeats the same inputs and the same recorded result changed
    nothing: comparing the normalized attempt identity, not a model's words,
    is what stops the loop instead of spending the remaining ceiling.
    """
    attempts = sorted(
        (
            attempt
            for attempt in progress.attempts
            if attempt.step_id == step_id and attempt.status == "failed"
        ),
        key=lambda attempt: attempt.attempt,
    )
    if len(attempts) < 2:
        return False
    left, right = attempts[-2], attempts[-1]
    return (
        left.input_digest == right.input_digest
        and left.result_digest == right.result_digest
    )


def classify_revision(
    previous: TaskPlan, proposed: TaskPlan
) -> Literal["repair", "material", "expanded_scope"]:
    """Which revision boundary a proposed plan crosses.

    A repair keeps the same steps, capabilities and profile choices and changes
    only what a step works on, so it stays inside the reviewed scope. Re-choosing
    a capability, a profile or the dependency shape is a different approach and
    therefore material: it needs a new review. Adding or removing steps, or
    reaching beyond the previous plan's pinned capability, profile and output
    sets, expands scope and always asks the user, in either mode.
    """
    prior = {step.id: step for step in previous.steps}
    if {step.id for step in proposed.steps} != set(prior):
        return "expanded_scope"
    prior_capabilities = {
        (item.id, item.version, item.manifest_digest)
        for item in previous.available_capabilities
    }
    if any(
        (item.id, item.version, item.manifest_digest) not in prior_capabilities
        for item in proposed.available_capabilities
    ):
        return "expanded_scope"
    if not set(proposed.available_profiles) <= set(previous.available_profiles):
        return "expanded_scope"
    material = False
    for step in proposed.steps:
        earlier = prior[step.id]
        if not set(step.expected_outputs) <= set(earlier.expected_outputs):
            return "expanded_scope"
        if (
            step.depends_on != earlier.depends_on
            or step.capability != earlier.capability
            or step.profile_id != earlier.profile_id
        ):
            material = True
    return "material" if material else "repair"


def _attempt_ceiling_allows(progress: TaskProgress, step_id: str, ceilings) -> bool:
    """The shared per-step attempt ceiling, one initial attempt plus repairs.

    The import stays local so this switcher keeps its module imports contract
    only: the ceiling lives with the task clocks, not beside them.
    """
    from .clock import attempt_ceiling_allows

    return attempt_ceiling_allows(progress, step_id, limits=ceilings)


def _all_verified(progress: TaskProgress) -> bool:
    """Only verified work covers a step: a user's skip is never verified success."""
    return bool(progress.step_ids) and set(progress.step_ids) <= set(
        progress.verified_steps
    )


def _next_unverified(progress: TaskProgress) -> str | None:
    """The next step still owed work, skipping steps the user chose to skip.

    Skipped steps stay unverified, so they are never selected for work again;
    what still has to run is decided from the plan order.
    """
    settled = set(progress.verified_steps) | set(progress.skipped_steps)
    for step_id in progress.step_ids:
        if step_id not in settled:
            return step_id
    return None


def _dependency_gap(progress: TaskProgress, step_id: str) -> str | None:
    """A dependency of this step that verified work does not cover.

    The projection groups work per step with its dependency edges, so the
    exact unvalidated input is named when the caller passes one; a bare
    ``TaskProgress`` only pins plan order, where every earlier step is a
    dependency. A step whose dependency was skipped (or never verified) has
    an input nothing validated: continuing there would execute on unvalidated
    inputs, which stays a wait or a visible plan revision instead.
    """
    verified = set(progress.verified_steps)
    steps = getattr(progress, "steps", ())
    if steps:
        for step in steps:
            if step.id != step_id:
                continue
            for dependency in step.depends_on:
                if dependency not in verified:
                    return dependency
        return None
    for previous in progress.step_ids:
        if previous == step_id:
            break
        if previous not in verified:
            return previous
    return None


def _unverified_result(progress: TaskProgress) -> str | None:
    verified = set(progress.verified_steps)
    for step_id in progress.step_ids:
        if step_id in verified:
            continue
        if any(
            attempt.step_id == step_id and attempt.status == "succeeded"
            for attempt in progress.attempts
        ):
            return step_id
    return None


def _verify_or_stop(progress: TaskProgress, reason: str) -> TaskAction:
    step_id = _unverified_result(progress) or _next_unverified(progress)
    if step_id is None:
        return _stop(reason)
    return _step_action("verify", step_id, "verify committed step evidence")


def _continue(progress: TaskProgress) -> TaskAction:
    result_step = _unverified_result(progress)
    if result_step is not None:
        return _step_action("verify", result_step, "verify committed step evidence")
    step_id = _next_unverified(progress)
    if step_id is None:
        if _all_verified(progress):
            return TaskAction(kind="finish", reason="all required steps are verified")
        if set(progress.step_ids) <= set(progress.verified_steps) | set(
            progress.skipped_steps
        ):
            return _stop("skipped work leaves required deliverables uncovered")
        return _stop("there is no eligible step to execute")
    gap = _dependency_gap(progress, step_id)
    if gap is not None:
        if gap in set(progress.skipped_steps):
            return _wait(
                "a skipped dependency leaves this step's required inputs unvalidated"
            )
        return _step_action("execute", gap, "execute the dependency first")
    return _step_action("execute", step_id, "execute the next verified plan step")


def _classification_action(assessment: TaskAssessment) -> TaskAction | None:
    """What an assessment's classification alone decides.

    A run that has no plan yet (revision 0) is judged by what its first
    assessment says: a greeting stops, a clarification parks focused input, a
    blocker asks for what it needs. A run that already has a plan keeps the
    status-first order, so a cancelled or blocked task is never revived.
    """
    if assessment.classification == "blocked":
        if assessment.missing_information:
            return _wait("a blocker requires user input")
        return _stop("the task is blocked by terminal evidence")
    if assessment.classification == "clarification":
        return _wait("a clarification is required")
    if assessment.classification == "informational":
        return _stop("informational submissions do not execute planned work")
    return None


def select_action(
    progress: TaskProgress,
    assessment: TaskAssessment | None,
    *,
    policy_allows: bool,
    budget_allows: bool,
    limits: TaskLimits | None = None,
    mode: str | None = None,
) -> TaskAction:
    """Select exactly one eligible action without inferring or dispatching.

    `limits` lets the caller pass the effective ceilings it validated with the
    budget instead of the contract defaults; it narrows a ceiling only.
    `mode` is the mode this run captured at admission: a Copilot run must
    confirm a proposed plan before any step executes, an Auto run advances
    only because its caller already validated the policy that permits this
    transition. No mode means the caller owns the gate, which is what every
    pre-v5 switch does.
    """
    ceilings = limits if limits is not None else _LIMITS
    if mode is not None and mode not in {"auto", "copilot"}:
        raise ValueError("mode must be auto or copilot")
    if not policy_allows:
        return _stop("policy does not allow this transition")
    if not budget_allows:
        return _stop("the task budget does not allow this transition")

    if progress.pending_decision is not None:
        wait_kind = (
            "configuration"
            if progress.pending_decision.kind == "configuration"
            else "decision"
        )
        return _wait("a pending decision must be resolved", wait_kind=wait_kind)
    if progress.status == "waiting":
        return _wait("the task is waiting for durable input")

    if assessment is not None and progress.current_revision == 0:
        decided = _classification_action(assessment)
        if decided is not None:
            return decided

    if progress.status in _TERMINAL_STATUSES:
        return _stop(f"the task is {progress.status} and cannot advance")
    # A model that calls the work a denied, policy or budget failure outranks
    # the durable uncertainty: those failures stop, they do not park a choice.
    if assessment is not None and assessment.failure in _UNSAFE_FAILURES:
        return _stop("denied, policy or budget failures never replay")
    if has_open_uncertainty(progress):
        return _wait(
            "work may have dispatched without a settled result",
            wait_kind="decision",
        )
    if progress.last_disposition in _UNSAFE_DISPOSITIONS:
        return _stop("the last disposition forbids new dispatch")

    if assessment is not None:
        decided = _classification_action(assessment)
        if decided is not None:
            return decided
        if assessment.failure == "uncertain":
            return _wait(
                "an uncertain failure needs the durable recovery choice",
                wait_kind="decision",
            )
        if assessment.failure in _UNSAFE_FAILURES:
            return _stop("denied, policy or budget failures never replay")
        if assessment.failure == "known":
            step_id = assessment.step_id or _next_unverified(progress)
            if step_id is None or step_id not in progress.step_ids:
                return _stop("no bounded step can be repaired")
            if step_id in progress.verified_steps:
                return _stop("verified steps are not repaired")
            if step_id in set(progress.skipped_steps):
                return _stop("a step the user skipped is not repaired here")
            if (
                progress.counters.repairs_by_step.get(step_id, 0)
                >= ceilings.max_repairs_per_step
            ):
                return _stop("the step repair ceiling is exhausted")
            if _repair_made_no_progress(progress, step_id):
                return _stop("the same evidence repeated without progress")
            if not _attempt_ceiling_allows(progress, step_id, ceilings):
                return _stop("the step attempt ceiling is exhausted")
            return _step_action("repair", step_id, "repair a known, bounded failure")
        if assessment.failure == "material":
            return TaskAction(
                kind="revise",
                reason="a material revision requires a new validated plan",
            )
        if (
            progress.last_disposition == "known_failure"
            or progress.last_disposition == "material_revision"
        ):
            return _stop("the recorded disposition requires a specific assessment")

    if progress.status == "verification_pending":
        return _verify_or_stop(progress, "there is no step left to verify")
    if progress.status == "completed":
        if _all_verified(progress):
            return TaskAction(kind="finish", reason="all required steps are verified")
        return _verify_or_stop(progress, "completion is not yet verified")

    if progress.last_disposition in {"known_failure", "material_revision"}:
        return _stop("the recorded disposition requires an assessment")

    if (
        mode == "copilot"
        and progress.status == "planned"
        and _next_unverified(progress) is not None
    ):
        return _wait(
            "a proposed plan requires confirmation before any step runs",
            wait_kind="decision",
        )

    return _continue(progress)
