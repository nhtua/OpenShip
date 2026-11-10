"""Deterministic task switching for the bounded v5 task graph.

This module only chooses the next validated action. It performs no model
inference, dispatch, persistence or authority check; callers supply the
policy and budget decisions and remain responsible for enforcing them.
"""

from __future__ import annotations

from .contracts import TaskAction, TaskAssessment, TaskLimits, TaskProgress

_LIMITS = TaskLimits()
_TERMINAL_STATUSES = frozenset(
    {"unknown", "blocked", "failed", "cancelled"}
)
_UNSAFE_DISPOSITIONS = frozenset(
    {"uncertain", "denied", "policy_revoked", "budget_exhausted"}
)
_UNSAFE_FAILURES = frozenset({"uncertain", "denied", "policy", "budget"})


def _stop(reason: str) -> TaskAction:
    return TaskAction(kind="stop", reason=reason)


def _wait(reason: str, *, wait_kind: str = "decision") -> TaskAction:
    return TaskAction(kind="wait", wait_kind=wait_kind, reason=reason)


def _step_action(kind: str, step_id: str, reason: str) -> TaskAction:
    return TaskAction(kind=kind, step_id=step_id, reason=reason)


def _attempt_ceiling_allows(progress: TaskProgress, step_id: str, ceilings) -> bool:
    """The shared per-step attempt ceiling, one initial attempt plus repairs.

    The import stays local so this switcher keeps its module imports contract
    only: the ceiling lives with the task clocks, not beside them.
    """
    from .clock import attempt_ceiling_allows

    return attempt_ceiling_allows(progress, step_id, limits=ceilings)


def _all_verified(progress: TaskProgress) -> bool:
    return bool(progress.step_ids) and set(progress.step_ids) <= set(
        progress.verified_steps
    )


def _next_unverified(progress: TaskProgress) -> str | None:
    verified = set(progress.verified_steps)
    for step_id in progress.step_ids:
        if step_id not in verified:
            return step_id
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
        return _step_action(
            "verify", result_step, "verify committed step evidence"
        )
    step_id = _next_unverified(progress)
    if step_id is None:
        if _all_verified(progress):
            return TaskAction(kind="finish", reason="all required steps are verified")
        return _stop("there is no eligible step to execute")
    return _step_action("execute", step_id, "execute the next verified plan step")


def select_action(
    progress: TaskProgress,
    assessment: TaskAssessment | None,
    *,
    policy_allows: bool,
    budget_allows: bool,
    limits: TaskLimits | None = None,
) -> TaskAction:
    """Select exactly one eligible action without inferring or dispatching.

    `limits` lets the caller pass the effective ceilings it validated with the
    budget instead of the contract defaults; it narrows a ceiling only.
    """
    ceilings = limits if limits is not None else _LIMITS
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

    if progress.status in _TERMINAL_STATUSES:
        return _stop(f"the task is {progress.status} and cannot advance")
    if progress.last_disposition in _UNSAFE_DISPOSITIONS:
        return _stop("the last disposition forbids new dispatch")

    if assessment is not None:
        if assessment.classification == "blocked":
            if assessment.missing_information:
                return _wait("a blocker requires user input")
            return _stop("the task is blocked by terminal evidence")
        if assessment.classification == "clarification":
            return _wait("a clarification is required")
        if assessment.classification == "informational":
            return _stop("informational submissions do not execute planned work")
        if assessment.failure in _UNSAFE_FAILURES:
            return _stop(
                "uncertain, denied, policy or budget failures never replay"
            )
        if assessment.failure == "known":
            step_id = assessment.step_id or _next_unverified(progress)
            if step_id is None or step_id not in progress.step_ids:
                return _stop("no bounded step can be repaired")
            if step_id in progress.verified_steps:
                return _stop("verified steps are not repaired")
            if (
                progress.counters.repairs_by_step.get(step_id, 0)
                >= ceilings.max_repairs_per_step
            ):
                return _stop("the step repair ceiling is exhausted")
            if not _attempt_ceiling_allows(progress, step_id, ceilings):
                return _stop("the step attempt ceiling is exhausted")
            return _step_action(
                "repair", step_id, "repair a known, bounded failure"
            )
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
            return TaskAction(
                kind="finish", reason="all required steps are verified"
            )
        return _verify_or_stop(progress, "completion is not yet verified")

    if progress.last_disposition in {"known_failure", "material_revision"}:
        return _stop("the recorded disposition requires an assessment")

    return _continue(progress)
