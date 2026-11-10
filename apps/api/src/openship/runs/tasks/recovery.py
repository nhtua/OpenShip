"""Recovery-grade reads of durable task truth.

The module is deliberately side-effect free: it reads committed rows and
reports what they prove. A logical call that was bound before dispatch but has
no settled ``TaskStepAttempt`` is uncertainty, not absence, so the projection
keeps it at the binding's ordinal and names the admitted operation only when
one exists. Nothing here reads a checkpoint, a prompt or a provider response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from ...artifacts.models import Artifact
from ...execution.models import ToolAttempt, ToolOperation
from ...sandboxes.models import WorkflowSandbox
from ..models import Run
from .clock import attempt_ceiling_allows, effective_loop_limits
from .contracts import (
    ExecutionConfigV5,
    RecoveryAction,
    TaskAssessment,
    TaskAttempt,
    TaskCallBinding,
    TaskPlan,
    TaskProgress,
    TaskRecoveryReport,
)
from .models import TaskPlanRecord, TaskRecord, TaskStepAttempt
from .state import (
    TaskStateError,
    _counters,
    _current_plan,
    _json,
    _load_plan,
    _pending_decision,
    _verified_outputs,
    _verified_steps,
)

_UNSETTLED_STATUSES = ("admitted", "dispatching", "running", "interrupted")
_EVIDENCE_ONLY_EXPLANATION = (
    "Evidence-only recovery report: only durable evidence is shown; no inference "
    "or provider call was made and no recovery action is recommended."
)


def _call_bindings(task: TaskRecord) -> tuple[TaskCallBinding, ...]:
    raw = _json(task.call_bindings_json, [])
    if not isinstance(raw, list):
        raise TaskStateError("state_corrupt", "call bindings are not a list")
    try:
        bindings = tuple(TaskCallBinding.model_validate(item) for item in raw)
    except ValueError as exc:
        raise TaskStateError("state_corrupt", "a call binding is unreadable") from exc
    if len({binding.logical_call_id for binding in bindings}) != len(bindings):
        raise TaskStateError("state_corrupt", "call bindings repeat a logical call")
    return bindings


def _committed_attempts(
    db: Session, task: TaskRecord, step_ids: tuple[str, ...]
) -> tuple[tuple[TaskAttempt, ...], tuple[TaskStepAttempt, ...]]:
    """The attempts the ledger has settled, exactly as the rows record them."""
    if not step_ids:
        return (), ()
    rows = (
        db.query(TaskStepAttempt)
        .filter(
            TaskStepAttempt.task_id == task.id,
            TaskStepAttempt.step_id.in_(step_ids),
        )
        .order_by(TaskStepAttempt.step_id.asc(), TaskStepAttempt.ordinal.asc())
        .all()
    )
    attempts = tuple(
        TaskAttempt(
            step_id=row.step_id,
            attempt=row.ordinal,
            status=row.status,
            operation_id=row.operation_id,
            input_digest=row.input_digest,
            result_digest=row.result_digest,
            evidence_refs=tuple(_json(row.evidence_json, [])),
        )
        for row in rows
    )
    return attempts, tuple(rows)


def _operations_by_logical_call(
    db: Session, run_id: UUID, logical_call_ids: set[str]
) -> dict[str, ToolOperation]:
    if not logical_call_ids:
        return {}
    rows = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run_id,
            ToolOperation.logical_call_id.in_(logical_call_ids),
        )
        .all()
    )
    return {row.logical_call_id: row for row in rows}


def reconcile_task_projection(db: Session, *, run_id: UUID) -> TaskProgress:
    """Read one task's truth from committed rows without writing anything.

    Attempts come from ``TaskStepAttempt`` rows. A call binding with no settled
    attempt row is projected as ``uncertain`` at the binding's own ordinal: if
    an operation was admitted for that logical call, its id and arguments
    digest are reported, and otherwise both stay ``None``. No ordinal, step,
    digest or operation identity is ever derived from the plan or the model.
    """
    task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one_or_none()
    if task is None:
        raise TaskStateError(
            "task_missing", "no durable task record exists for this run"
        )

    plan_row = _current_plan(db, task)
    if plan_row is None and task.current_revision > 0:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row) if plan_row is not None else None
    step_ids = tuple(step.id for step in plan.steps) if plan is not None else ()
    plan_steps = set(step_ids)

    bindings = _call_bindings(task)
    if bindings and not plan_steps:
        raise TaskStateError(
            "state_corrupt", "call bindings require a current plan"
        )
    for binding in bindings:
        if binding.step_id not in plan_steps:
            raise TaskStateError(
                "state_corrupt",
                "a call binding references a step outside the current plan",
            )

    committed, rows = _committed_attempts(db, task, step_ids)
    settled_ordinals = {(row.step_id, row.ordinal) for row in rows}
    settled_operations = {row.operation_id for row in rows}

    pending = [
        binding
        for binding in bindings
        if (binding.step_id, binding.attempt) not in settled_ordinals
    ]
    operations = _operations_by_logical_call(
        db, task.run_id, {binding.logical_call_id for binding in pending}
    )

    uncertain: list[TaskAttempt] = []
    for binding in bindings:
        if (binding.step_id, binding.attempt) in settled_ordinals:
            continue
        operation = operations.get(binding.logical_call_id)
        if operation is not None and operation.id in settled_operations:
            continue
        uncertain.append(
            TaskAttempt(
                step_id=binding.step_id,
                attempt=binding.attempt,
                status="uncertain",
                operation_id=operation.id if operation is not None else None,
                input_digest=(
                    operation.arguments_digest if operation is not None else None
                ),
            )
        )

    step_order = {step_id: index for index, step_id in enumerate(step_ids)}
    attempts = tuple(
        sorted(
            (*committed, *uncertain),
            key=lambda attempt: (step_order[attempt.step_id], attempt.attempt),
        )
    )

    return TaskProgress(
        current_revision=task.current_revision,
        status=task.status,
        step_ids=step_ids,
        attempts=attempts,
        verified_outputs=_verified_outputs(task),
        verified_steps=_verified_steps(task),
        skipped_steps=tuple(_json(task.skipped_steps_json, [])),
        call_bindings=bindings,
        pending_decision=_pending_decision(db, task),
        counters=_counters(task),
        last_disposition=task.last_disposition,
    )


def _subject(
    db: Session, run_id: UUID
) -> tuple[Run, TaskRecord, TaskPlanRecord, TaskPlan, tuple[TaskCallBinding, ...]]:
    run = db.query(Run).filter(Run.id == run_id).one_or_none()
    if run is None:
        raise TaskStateError("run_not_found", "the run does not exist")
    task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one_or_none()
    if task is None:
        raise TaskStateError("task_missing", "no durable task record exists for this run")
    if task.current_revision < 1:
        raise TaskStateError("plan_missing", "recovery requires a saved plan")
    plan_row = _current_plan(db, task)
    if plan_row is None:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row)
    if (
        task.project_id != run.project_id
        or task.conversation_id != run.conversation_id
        or not task.digest
        or task.digest != plan_row.digest
    ):
        raise TaskStateError(
            "state_corrupt", "the task identity does not match its run and plan"
        )
    return run, task, plan_row, plan, _call_bindings(task)


def _single_unsettled_operation(db: Session, run: Run) -> ToolOperation:
    operations = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run.id,
            ToolOperation.status.in_(_UNSETTLED_STATUSES),
        )
        .order_by(ToolOperation.id.asc())
        .all()
    )
    if len(operations) != 1:
        raise TaskStateError(
            "recovery_conflict",
            "recovery requires exactly one unsettled operation",
        )
    return operations[0]


def _affected_attempt(
    db: Session,
    run: Run,
    operation: ToolOperation,
    bindings: tuple[TaskCallBinding, ...],
    revision: int,
) -> tuple[str, int]:
    """The one step attempt the unsettled operation belongs to."""
    attempt = (
        db.query(TaskStepAttempt)
        .filter(
            TaskStepAttempt.run_id == run.id,
            TaskStepAttempt.operation_id == operation.id,
        )
        .one_or_none()
    )
    if attempt is not None:
        if attempt.plan_revision != revision:
            raise TaskStateError(
                "recovery_conflict",
                "the unsettled operation belongs to an earlier plan revision",
            )
        step_id, ordinal = attempt.step_id, attempt.ordinal
    else:
        matches = [
            binding
            for binding in bindings
            if binding.logical_call_id == operation.logical_call_id
        ]
        if len(matches) != 1:
            raise TaskStateError(
                "recovery_conflict",
                "the unsettled operation has no single bound call",
            )
        step_id, ordinal = matches[0].step_id, matches[0].attempt

    same_attempt = [
        binding
        for binding in bindings
        if binding.step_id == step_id and binding.attempt == ordinal
    ]
    if len(same_attempt) > 1:
        raise TaskStateError(
            "recovery_conflict",
            "more than one call is bound to the affected step attempt",
        )
    if same_attempt and same_attempt[0].logical_call_id != operation.logical_call_id:
        raise TaskStateError(
            "recovery_conflict",
            "the affected step attempt is bound to another call",
        )
    if attempt is None and len(same_attempt) != 1:
        raise TaskStateError(
            "recovery_conflict",
            "the unsettled operation is not bound to a step attempt",
        )
    for binding in same_attempt:
        if binding.plan_revision != revision:
            raise TaskStateError(
                "recovery_conflict",
                "the call binding belongs to an earlier plan revision",
            )
    return step_id, ordinal


def _evidence(
    db: Session, run: Run, task: TaskRecord, operation: ToolOperation
) -> tuple[str, ...]:
    """Only the operation request digest and exact ready artifact hashes."""
    if (
        operation.run_id != run.id
        or operation.project_id != run.project_id
        or operation.conversation_id != run.conversation_id
        or operation.principal_id != task.initiator_id
        or operation.status not in _UNSETTLED_STATUSES
        or not operation.request_digest
    ):
        raise TaskStateError(
            "recovery_conflict", "the unsettled operation is outside this task's scope"
        )
    raw = _json(operation.output_artifact_ids, [])
    if not isinstance(raw, list):
        raise TaskStateError(
            "recovery_conflict", "the operation artifact ids are unreadable"
        )
    ids: list[UUID] = []
    for item in raw:
        try:
            ids.append(UUID(str(item)))
        except (TypeError, ValueError) as exc:
            raise TaskStateError(
                "recovery_conflict", "an operation artifact id is invalid"
            ) from exc
    if len(ids) != len(set(ids)):
        raise TaskStateError(
            "recovery_conflict", "an operation artifact id is duplicated"
        )
    artifacts = (
        {row.id: row for row in db.query(Artifact).filter(Artifact.id.in_(ids)).all()}
        if ids
        else {}
    )
    if len(artifacts) != len(ids):
        raise TaskStateError(
            "recovery_conflict", "an operation artifact is missing"
        )

    evidence = [f"operation:{operation.request_digest}"]
    for identity in ids:
        artifact = artifacts[identity]
        if (
            artifact.project_id != run.project_id
            or artifact.conversation_id != run.conversation_id
            or artifact.originating_run_id != run.id
            or artifact.originating_operation_id != operation.id
        ):
            raise TaskStateError(
                "recovery_conflict", "an artifact belongs to another scope"
            )
        if artifact.status != "ready":
            raise TaskStateError(
                "recovery_conflict", "an operation artifact is not committed"
            )
        reference = f"artifact:{artifact.sha256}"
        if reference not in evidence:
            evidence.append(reference)
    if len(evidence) > 16:
        raise TaskStateError(
            "recovery_conflict", "the operation published too much evidence"
        )
    return tuple(evidence)


def _execution_config(run: Run) -> ExecutionConfigV5 | None:
    if not run.execution_configuration:
        return None
    try:
        return ExecutionConfigV5.model_validate_json(run.execution_configuration)
    except ValueError:
        return None


def _command_still_uncertain(db: Session, run: Run, now: datetime) -> bool:
    sandbox = (
        db.query(WorkflowSandbox)
        .filter_by(project_id=run.project_id, conversation_id=run.conversation_id)
        .one_or_none()
    )
    if sandbox is None or sandbox.command_uncertain_until is None:
        return False
    deadline = sandbox.command_uncertain_until
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline > now


def _repeat_blockers(
    db: Session,
    run: Run,
    operation: ToolOperation,
    step,
    progress: TaskProgress,
    execution: ExecutionConfigV5 | None,
    now: datetime,
) -> list[str]:
    blockers: list[str] = []
    if operation.status != "admitted":
        blockers.append("the earlier command may still be running")
    dispatched = (
        db.query(ToolAttempt.id)
        .filter(ToolAttempt.operation_id == operation.id)
        .first()
    )
    if dispatched is not None:
        blockers.append("the earlier command was already dispatched")
    if _command_still_uncertain(db, run, now):
        blockers.append("the sandbox may still be running the earlier command")
    if step is None:
        blockers.append("the affected step is not in the current plan")
    else:
        if step.id in progress.verified_steps:
            blockers.append("the affected step is already verified")
        if step.id in progress.skipped_steps:
            blockers.append("the affected step was skipped")
        if step.capability is None:
            blockers.append("the affected step has no pinned capability")
        else:
            if execution is None:
                blockers.append("the affected step's pinned capability cannot be verified")
            elif step.capability not in execution.allowed_capabilities:
                blockers.append("the affected step's capability is no longer allowed")
            if (
                operation.capability_id,
                operation.capability_version,
                operation.manifest_digest,
            ) != (
                step.capability.id,
                step.capability.version,
                step.capability.manifest_digest,
            ):
                blockers.append(
                    "the operation's capability does not match the affected step"
                )

    if execution is None:
        blockers.append("the pinned execution limits are unavailable")
        return blockers

    limits = effective_loop_limits(execution)
    if step is not None:
        if not attempt_ceiling_allows(progress, step.id, limits=limits):
            blockers.append("the step attempt ceiling is exhausted")
        if (
            progress.counters.repairs_by_step.get(step.id, 0)
            >= limits.max_repairs_per_step
        ):
            blockers.append("the step repair ceiling is exhausted")

    counters = progress.counters
    if counters.tool_calls >= min(
        limits.max_tool_calls, execution.loop_limits.max_tool_calls
    ):
        blockers.append("the tool-call budget is exhausted")
    if counters.model_turns >= min(
        limits.max_model_turns, execution.loop_limits.max_model_turns
    ):
        blockers.append("the model-turn budget is exhausted")
    if counters.generation_attempts >= min(
        limits.max_generation_attempts, execution.loop_limits.max_generation_attempts
    ):
        blockers.append("the generation-attempt budget is exhausted")
    if counters.output_tokens >= min(
        limits.max_total_output_tokens, execution.loop_limits.max_total_output_tokens
    ):
        blockers.append("the output-token budget is exhausted")
    return blockers


def _revalidate_assessment(
    assessment: TaskAssessment | None, *, step_id: str, evidence: tuple[str, ...]
) -> None:
    """Refuse a diagnosis that no longer matches the durable evidence."""
    if assessment is None:
        return
    known = set(evidence)
    for reference in assessment.evidence_refs:
        if reference not in known:
            raise TaskStateError(
                "recovery_superseded",
                "the assessment cites evidence that has changed",
            )
    if assessment.step_id is not None and assessment.step_id != step_id:
        raise TaskStateError(
            "recovery_superseded", "the assessment diagnosed another step"
        )
    proposed = assessment.proposed_action
    if (
        proposed is not None
        and proposed.step_id is not None
        and proposed.step_id != step_id
    ):
        raise TaskStateError(
            "recovery_superseded", "the assessment proposed another step"
        )


def prepare_task_recovery(
    db: Session,
    *,
    run_id: UUID,
    assessment: TaskAssessment | None,
    now: datetime,
) -> TaskRecoveryReport:
    """Build one evidence-backed recovery report without writing or inferring.

    The report binds the one unsettled operation to the current plan revision,
    step attempt and operation identity. ``abort`` is always offered;
    ``repeat_step`` is offered only while the command is quiescent and the
    pinned policy, attempt ceiling and budgets still allow it; ``continue_next``
    is offered only when no later step depends on the unresolved output. An
    assessment can label a recommendation, but this function never promotes it
    into an eligible choice. The caller persists the returned report atomically
    through ``park_task_wait``.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    now = now.astimezone(UTC)

    run, task, plan_row, plan, bindings = _subject(db, run_id)
    operation = _single_unsettled_operation(db, run)
    step_id, ordinal = _affected_attempt(db, run, operation, bindings, task.current_revision)
    steps = {step.id: step for step in plan.steps}
    step = steps.get(step_id)
    if step is None:
        raise TaskStateError(
            "recovery_conflict", "the affected step is not in the current plan"
        )
    evidence = _evidence(db, run, task, operation)
    _revalidate_assessment(assessment, step_id=step_id, evidence=evidence)

    progress = reconcile_task_projection(db, run_id=run_id)
    execution = _execution_config(run)
    eligible: list[RecoveryAction] = ["abort"]
    blockers: list[str] = []

    repeat_blockers = _repeat_blockers(
        db, run, operation, step, progress, execution, now
    )
    if repeat_blockers:
        blockers.extend(repeat_blockers)
    else:
        eligible.append("repeat_step")

    if any(step_id in candidate.depends_on for candidate in plan.steps):
        blockers.append("a later step depends on the unresolved step's output")
    else:
        eligible.append("continue_next")

    if assessment is None:
        explanation = _EVIDENCE_ONLY_EXPLANATION
        unknowns: tuple[str, ...] = ()
        risks: tuple[str, ...] = ()
        recommended: RecoveryAction | None = None
    else:
        explanation = assessment.explanation
        unknowns = assessment.unknowns
        risks = assessment.risks
        recommended = (
            assessment.recommended_recovery_action
            if assessment.failure == "uncertain"
            else None
        )

    return TaskRecoveryReport(
        plan_revision=task.current_revision,
        plan_digest=plan_row.digest,
        step_id=step_id,
        attempt=ordinal,
        operation_id=operation.id,
        explanation=explanation,
        evidence_refs=evidence,
        unknowns=unknowns,
        risks=risks,
        recommended_action=recommended,
        eligible_actions=tuple(eligible),
        blockers=tuple(blockers),
    )
