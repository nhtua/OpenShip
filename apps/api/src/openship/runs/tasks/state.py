"""Fenced, version-checked task state transitions.

The caller owns the transaction. Every mutation validates the worker claim,
the run fence and the current task revision, and appends its public event in
the same transaction so a rollback cannot leave one half visible.

Execution outcomes are never copied from a caller's assertion: the committed
``ToolOperation`` row (and the artifacts it owns) remains the only source of
an attempt's result, digests and evidence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from ...artifacts.contracts import ArtifactRef
from ...artifacts.models import Artifact
from ...chat.models import Conversation
from ...events.service import append_event
from ...execution.models import ToolOperation
from ...workspace.models import Project
from ..models import Run, RunJob
from ..queue import Claim
from .contracts import (
    ExecutionConfigV5,
    TaskAttempt,
    TaskCallBinding,
    TaskCounters,
    TaskDecision,
    TaskPlan,
    TaskRecoveryReport,
    VerificationReport,
)
from .models import (
    TaskDecisionRecord,
    TaskPlanRecord,
    TaskRecord,
    TaskStepAttempt,
)
from .projections import TaskProgressProjection, TaskStepProgress, read_task_summary

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "stale"}
)
_OPERATION_OUTCOMES = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
    "interrupted": "interrupted",
}
_TERMINAL_ATTEMPT_STATUSES = frozenset(
    {"succeeded", "failed", "interrupted", "uncertain"}
)


class TaskStateError(RuntimeError):
    """A safe, code-bearing refusal from the task state boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class _ClaimState:
    run: Run
    job: RunJob
    task: TaskRecord | None


def _now() -> datetime:
    return datetime.now(UTC)


def _json(text: str | None, default):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise TaskStateError("state_corrupt", "task state is not valid JSON") from exc


def _counters(task: TaskRecord) -> TaskCounters:
    try:
        return TaskCounters.model_validate(_json(task.counters_json, {}))
    except ValueError as exc:
        raise TaskStateError("state_corrupt", "task counters are invalid") from exc


def _verified_outputs(task: TaskRecord) -> tuple[ArtifactRef, ...]:
    raw = _json(task.verified_outputs_json, [])
    try:
        items = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                # The durable JSON stores UUIDs as strings; the strict contract
                # wants the instance itself, so re-materialise it here.
                item = {**item, "id": uuid.UUID(item["id"])}
            items.append(item)
        return tuple(ArtifactRef.model_validate(item) for item in items)
    except ValueError as exc:
        raise TaskStateError("state_corrupt", "verified outputs are invalid") from exc


def _verified_steps(task: TaskRecord) -> tuple[str, ...]:
    raw = _json(task.verified_steps_json, [])
    return tuple(raw)


def _load_plan(row: TaskPlanRecord) -> TaskPlan:
    try:
        plan = TaskPlan.model_validate_json(row.plan_json)
    except ValueError as exc:
        raise TaskStateError("state_corrupt", "stored plan is invalid") from exc
    if plan.revision != row.revision or plan.digest != row.digest:
        raise TaskStateError(
            "state_corrupt", "stored plan identity does not match its digest"
        )
    return plan


def _current_plan(db, task: TaskRecord) -> TaskPlanRecord | None:
    return (
        db.query(TaskPlanRecord)
        .filter(
            TaskPlanRecord.task_id == task.id,
            TaskPlanRecord.revision == task.current_revision,
        )
        .one_or_none()
    )


def _pending_decision(db, task: TaskRecord) -> TaskDecision | None:
    row = (
        db.query(TaskDecisionRecord)
        .filter(
            TaskDecisionRecord.task_id == task.id,
            TaskDecisionRecord.state == "open",
        )
        .one_or_none()
    )
    if row is None or row.plan_revision != task.current_revision:
        return None
    report = _json(row.request_json, {}).get("recovery_report")
    return TaskDecision(
        request_id=row.request_id,
        kind=row.kind,
        plan_revision=row.plan_revision,
        expected_digest=row.expected_digest,
        expires_at=(
            row.expires_at.replace(tzinfo=UTC)
            if row.expires_at.tzinfo is None
            else row.expires_at
        ),
        recovery_report=(
            TaskRecoveryReport.model_validate_json(json.dumps(report))
            if report is not None
            else None
        ),
    )


def _step_status(
    step_id: str, attempts: tuple[TaskAttempt, ...], verified_steps: tuple[str, ...]
) -> str:
    if step_id in verified_steps:
        return "verified"
    if not attempts:
        return "pending"
    statuses = {attempt.status for attempt in attempts}
    for candidate in ("succeeded", "running", "interrupted", "uncertain", "failed"):
        if candidate in statuses:
            return candidate
    return "uncertain"


def _project(db, task: TaskRecord | None) -> TaskProgressProjection:
    if task is None:
        # A run with no task record is pre-plan: revision 0, not the contract's
        # default of revision 1, which belongs to a plan's first revision.
        return TaskProgressProjection(current_revision=0, status="unknown")
    plan_row = _current_plan(db, task)
    plan = _load_plan(plan_row) if plan_row is not None else None
    verified_steps = _verified_steps(task)

    rows_by_step: dict[str, list[TaskStepAttempt]] = {}
    if plan is not None:
        rows = (
            db.query(TaskStepAttempt)
            .filter(
                TaskStepAttempt.task_id == task.id,
                TaskStepAttempt.step_id.in_([step.id for step in plan.steps]),
            )
            .order_by(TaskStepAttempt.step_id.asc(), TaskStepAttempt.ordinal.asc())
            .all()
        )
        for row in rows:
            rows_by_step.setdefault(row.step_id, []).append(row)

    steps: list[TaskStepProgress] = []
    flat_attempts: list[TaskAttempt] = []
    if plan is not None:
        for step in plan.steps:
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
                for row in rows_by_step.get(step.id, ())
            )
            flat_attempts.extend(attempts)
            steps.append(
                TaskStepProgress(
                    id=step.id,
                    intent=step.intent,
                    depends_on=step.depends_on,
                    status=_step_status(step.id, attempts, verified_steps),
                    attempts=attempts,
                )
            )

    return TaskProgressProjection(
        current_revision=task.current_revision,
        status=task.status,
        step_ids=tuple(step.id for step in steps),
        attempts=tuple(flat_attempts),
        verified_outputs=_verified_outputs(task),
        verified_steps=verified_steps,
        skipped_steps=tuple(_json(task.skipped_steps_json, [])),
        call_bindings=tuple(_json(task.call_bindings_json, [])),
        pending_decision=_pending_decision(db, task),
        counters=_counters(task),
        last_disposition=task.last_disposition,
        steps=tuple(steps),
    )


def read_progress(db, run_id: uuid.UUID) -> TaskProgressProjection:
    """Read the current durable projection without locking or mutating."""
    task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one_or_none()
    return _project(db, task)


def _progress_payload(db, task: TaskRecord) -> dict:
    """The public progress view for the event stream.

    It is the same ``TaskSummary`` the run reads and the snapshot return,
    minus the plan (its own ``task.plan_proposed`` event) and the open
    decision (its own request and resolution events).
    """
    payload = read_task_summary(db, task.run_id).model_dump(mode="json")
    payload.pop("plan", None)
    payload.pop("pending_decision", None)
    return payload


def read_plan(db, run_id: uuid.UUID) -> TaskPlan | None:
    """The plan this run is on, as its durable record says, without locking.

    A resumed graph invocation has no checkpoint draft to read, so the step
    machinery needs the same record ``save_plan`` wrote: the current revision,
    with its step intents, capabilities and expectations. Nothing is rebuilt
    from a model draft here.
    """
    task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one_or_none()
    if task is None:
        return None
    row = _current_plan(db, task)
    if row is None:
        return None
    return _load_plan(row)


def _claim_state(db, claim: Claim) -> _ClaimState:
    if not isinstance(claim, Claim):
        raise TaskStateError("invalid_claim", "a worker claim is required")
    if claim.user_id is None:
        raise TaskStateError("invalid_claim", "the claim has no initiator")

    identity = (
        db.query(Run.conversation_id, Run.project_id)
        .filter(Run.id == claim.run_id)
        .one_or_none()
    )
    if identity is None:
        raise TaskStateError("run_not_found", "the claimed run does not exist")

    # Lock order: active conversation/project scope, then run/job, then task.
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == identity.conversation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    project = (
        db.query(Project)
        .filter(Project.id == identity.project_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if conversation is None or project is None:
        raise TaskStateError("run_not_found", "the claimed run scope is missing")
    if conversation.user_id != claim.user_id:
        raise TaskStateError(
            "claim_scope", "the claim is not owned by this run's initiator"
        )

    run = (
        db.query(Run)
        .filter(Run.id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    task = (
        db.query(TaskRecord)
        .filter(TaskRecord.run_id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )

    if run is None:
        raise TaskStateError("run_not_found", "the claimed run does not exist")
    if run.kind != "chat" or (run.graph_version or 1) != 5:
        raise TaskStateError(
            "run_not_task", "task state is only valid for a v5 chat root run"
        )
    if run.status in TERMINAL_RUN_STATUSES:
        raise TaskStateError("run_terminal", "the run is already terminal")
    if run.fence != claim.fence:
        raise TaskStateError("stale_claim", "the run fence has moved")
    if job is None or job.fence != claim.fence or job.owner_id != claim.worker_id:
        raise TaskStateError("stale_claim", "the job is not owned by this claim")
    return _ClaimState(run=run, job=job, task=task)


def _default_context(run: Run, claim: Claim) -> dict:
    """The task context pinned at admission, or the legacy conversation shape.

    A v5 admission writes its context into the run's execution snapshot. That
    pinned mode is authoritative for the task; the account's current
    preference is never re-read here. Runs created before admission pinned a
    context (tests and historical fixtures) keep the prior defaults.
    """
    legacy = {
        "root_run_id": str(run.id),
        "project_id": str(run.project_id),
        "conversation_id": str(run.conversation_id),
        "initiator_id": str(claim.user_id),
        "scope": "conversation",
        "mode": "copilot",
        "graph_version": 5,
    }
    if not run.execution_configuration:
        return legacy
    try:
        config = ExecutionConfigV5.model_validate_json(run.execution_configuration)
    except ValueError as exc:
        raise TaskStateError(
            "state_corrupt", "the task run execution configuration is invalid"
        ) from exc
    context = config.task_context
    if (
        context.root_run_id != run.id
        or context.project_id != run.project_id
        or context.conversation_id != run.conversation_id
        or context.initiator_id != claim.user_id
    ):
        raise TaskStateError(
            "state_corrupt", "the pinned task context does not match its run"
        )
    return context.model_dump(mode="json")


def save_plan(
    db,
    claim: Claim,
    plan: TaskPlan,
    *,
    expected_revision: int,
) -> TaskProgressProjection:
    """Persist one validated plan revision, refusing stale or rewritten plans."""
    if not isinstance(plan, TaskPlan):
        raise TaskStateError("invalid_plan", "a strict task plan is required")
    if plan.status != "validated":
        raise TaskStateError(
            "plan_not_validated", "only a server-validated plan can be saved"
        )
    if not isinstance(expected_revision, int) or expected_revision < 0:
        raise TaskStateError(
            "stale_revision", "expected_revision must be the current revision"
        )

    state = _claim_state(db, claim)
    task = state.task
    digest = plan.digest

    if task is None:
        if expected_revision != 0 or plan.revision != 1:
            raise TaskStateError(
                "stale_revision",
                "a new task starts at revision 1 with expected_revision 0",
            )
        context = _default_context(state.run, claim)
        task = TaskRecord(
            run_id=state.run.id,
            project_id=state.run.project_id,
            conversation_id=state.run.conversation_id,
            initiator_id=claim.user_id,
            scope=context["scope"],
            mode=context["mode"],
            graph_version=int(context["graph_version"]),
            current_revision=1,
            status="planned",
            digest=digest,
            fence=claim.fence,
            context_json=json.dumps(context),
            counters_json=TaskCounters().model_dump_json(),
            accepted_evidence_json="[]",
            verified_outputs_json="[]",
            verified_steps_json="[]",
            last_disposition="none",
        )
        db.add(task)
        db.flush()
    else:
        existing = (
            db.query(TaskPlanRecord)
            .filter(
                TaskPlanRecord.task_id == task.id,
                TaskPlanRecord.revision == plan.revision,
            )
            .one_or_none()
        )
        if existing is not None:
            if _load_plan(existing).digest == digest and (
                plan.revision == expected_revision + 1
            ):
                return _project(db, task)
            raise TaskStateError("plan_conflict", "that plan revision is immutable")
        if expected_revision != task.current_revision:
            raise TaskStateError("stale_revision", "the task revision has advanced")
        if plan.revision != task.current_revision + 1:
            raise TaskStateError(
                "stale_revision", "a new plan revision must follow the current one"
            )
        progress = _project(db, task)
        referenced = set(progress.skipped_steps) | {
            binding.step_id for binding in progress.call_bindings
        }
        if not referenced <= {step.id for step in plan.steps}:
            raise TaskStateError(
                "recovery_history_conflict",
                "a new plan cannot remove steps referenced by recovery history",
            )

    db.add(
        TaskPlanRecord(
            task_id=task.id,
            run_id=task.run_id,
            revision=plan.revision,
            digest=digest,
            status=plan.status,
            plan_json=plan.model_dump_json(),
        )
    )
    task.current_revision = plan.revision
    task.digest = digest
    task.status = "planned"
    task.fence = claim.fence
    task.updated_at = _now()
    db.flush()

    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.plan_proposed",
        payload={
            "schema_version": 1,
            "run_id": str(task.run_id),
            "revision": plan.revision,
            "mode": task.mode,
            "review_digest": digest,
            "steps": [
                {
                    "id": step.id,
                    "intent": step.intent,
                    "depends_on": list(step.depends_on),
                }
                for step in plan.steps
            ],
        },
    )
    db.flush()
    return _project(db, task)


def _attempt_for_operation(db, run_id: uuid.UUID, operation_id: uuid.UUID):
    return (
        db.query(TaskStepAttempt)
        .filter(
            TaskStepAttempt.run_id == run_id,
            TaskStepAttempt.operation_id == operation_id,
        )
        .one_or_none()
    )


def _validate_operation(
    db, state: _ClaimState, claim: Claim, operation_id: uuid.UUID
) -> tuple[ToolOperation, str, tuple[str, ...]]:
    operation = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == operation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if operation is None:
        raise TaskStateError(
            "operation_not_found", "the committed operation does not exist"
        )
    run = state.run
    if (
        operation.run_id != run.id
        or operation.project_id != run.project_id
        or operation.conversation_id != run.conversation_id
        or operation.principal_id != claim.user_id
    ):
        raise TaskStateError(
            "operation_scope", "the operation belongs to a different scope"
        )
    outcome = _OPERATION_OUTCOMES.get(operation.status)
    if outcome is None:
        raise TaskStateError(
            "operation_not_committed",
            "only a committed operation can record an attempt outcome",
        )
    # Only a succeeded operation has a result to digest. The gateway commits a
    # refused or cancelled one with no result at all, and reading that outcome -
    # not losing it - is what lets the switcher diagnose and repair the step.
    if outcome == "succeeded" and not operation.result_digest:
        raise TaskStateError(
            "operation_incomplete", "a succeeded operation has no result digest"
        )

    artifact_ids = _json(operation.output_artifact_ids, [])
    if not isinstance(artifact_ids, list):
        raise TaskStateError("state_corrupt", "operation artifact ids are not a list")
    parsed_ids = []
    for item in artifact_ids:
        try:
            parsed_ids.append(uuid.UUID(str(item)))
        except (TypeError, ValueError) as exc:
            raise TaskStateError(
                "state_corrupt", "operation artifact id is invalid"
            ) from exc
    artifacts = (
        db.query(Artifact).filter(Artifact.id.in_(parsed_ids)).all()
        if parsed_ids
        else []
    )
    if len(artifacts) != len(set(parsed_ids)):
        raise TaskStateError(
            "artifact_scope", "an operation artifact is missing or duplicated"
        )
    evidence: list[str] = [f"operation:{operation.request_digest}"]
    for artifact in artifacts:
        if (
            artifact.project_id != run.project_id
            or artifact.conversation_id != run.conversation_id
            or artifact.originating_run_id != run.id
            or artifact.originating_operation_id != operation.id
        ):
            raise TaskStateError(
                "artifact_scope", "an artifact belongs to a different scope"
            )
        if artifact.status != "ready":
            raise TaskStateError(
                "artifact_not_ready", "an operation artifact is not committed"
            )
        reference = f"artifact:{artifact.sha256}"
        if reference not in evidence:
            evidence.append(reference)
    return operation, outcome, tuple(evidence)


def record_attempt_result(
    db,
    claim: Claim,
    *,
    step_id: str,
    attempt: int,
    operation_id: uuid.UUID,
) -> TaskProgressProjection:
    """Track one committed operation as a step attempt, idempotently."""
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or not 1 <= attempt <= 3
    ):
        raise TaskStateError(
            "invalid_attempt", "attempt ordinals are bounded from 1 to 3"
        )
    state = _claim_state(db, claim)
    task = state.task
    if task is None:
        raise TaskStateError("task_missing", "no plan has been saved for this run")

    plan_row = _current_plan(db, task)
    if plan_row is None:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row)
    if step_id not in {step.id for step in plan.steps}:
        raise TaskStateError(
            "step_unknown", "the step does not belong to the current plan"
        )

    operation, outcome, evidence = _validate_operation(db, state, claim, operation_id)

    existing_operation = _attempt_for_operation(db, task.run_id, operation.id)
    if existing_operation is not None:
        if (
            existing_operation.step_id != step_id
            or existing_operation.ordinal != attempt
        ):
            raise TaskStateError(
                "attempt_conflict",
                "that operation is already recorded for another attempt",
            )
        return _project(db, task)

    existing_ordinal = (
        db.query(TaskStepAttempt)
        .filter(
            TaskStepAttempt.run_id == task.run_id,
            TaskStepAttempt.step_id == step_id,
            TaskStepAttempt.ordinal == attempt,
        )
        .one_or_none()
    )
    if existing_ordinal is not None:
        raise TaskStateError(
            "attempt_conflict", "that step attempt ordinal is already recorded"
        )

    try:
        with db.begin_nested():
            db.add(
                TaskStepAttempt(
                    task_id=task.id,
                    run_id=task.run_id,
                    plan_revision=task.current_revision,
                    step_id=step_id,
                    ordinal=attempt,
                    status=outcome,
                    operation_id=operation.id,
                    input_digest=operation.arguments_digest,
                    result_digest=operation.result_digest,
                    evidence_json=json.dumps(list(evidence)),
                    finished_at=(
                        _now() if outcome in _TERMINAL_ATTEMPT_STATUSES else None
                    ),
                )
            )
            db.flush()
    except IntegrityError as exc:
        replay = _attempt_for_operation(db, task.run_id, operation.id)
        if (
            replay is not None
            and replay.step_id == step_id
            and replay.ordinal == attempt
        ):
            return _project(db, task)
        raise TaskStateError(
            "attempt_conflict", "that attempt is already recorded"
        ) from exc

    counters = _counters(task)
    task.counters_json = counters.model_copy(
        update={"tool_calls": counters.tool_calls + 1}
    ).model_dump_json()
    accepted = list(_json(task.accepted_evidence_json, []))
    for reference in evidence:
        if reference not in accepted:
            accepted.append(reference)
    task.accepted_evidence_json = json.dumps(accepted)
    task.status = "running"
    task.fence = claim.fence
    task.updated_at = _now()
    db.flush()

    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.attempt_recorded",
        payload={
            "run_id": str(task.run_id),
            "revision": task.current_revision,
            "step_id": step_id,
            "attempt": attempt,
            "status": outcome,
            "operation_id": str(operation.id),
            "evidence_refs": list(evidence),
        },
    )
    db.flush()
    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.progress",
        payload=_progress_payload(db, task),
    )
    db.flush()
    return _project(db, task)


def record_task_call(
    db,
    claim: Claim,
    *,
    binding: TaskCallBinding,
) -> TaskProgressProjection:
    """Commit the logical call a step is about to make, before it dispatches.

    The binding is what lets a later reader say "this step may have run": a
    worker that dies between here and the gateway leaves the call recorded,
    and the missing result is uncertainty, never absence of work. The
    identity is checked against the current plan and the step's committed
    attempts, so a stale checkpoint cannot approve a new plan or re-number
    an attempt.
    """
    if not isinstance(binding, TaskCallBinding):
        raise TaskStateError("invalid_binding", "a strict call binding is required")
    state = _claim_state(db, claim)
    task = state.task
    if task is None:
        raise TaskStateError("task_missing", "no plan has been saved for this run")
    plan_row = _current_plan(db, task)
    if plan_row is None:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row)
    if binding.plan_revision != task.current_revision:
        raise TaskStateError(
            "stale_revision", "the call binds a plan revision the run is not on"
        )
    if binding.step_id not in {step.id for step in plan.steps}:
        raise TaskStateError("step_unknown", "the call binds a step outside the current plan")

    recorded = _json(task.call_bindings_json, [])
    if not isinstance(recorded, list):
        raise TaskStateError("state_corrupt", "call bindings are not a list")
    for item in recorded:
        try:
            existing = TaskCallBinding(**item)
        except (TypeError, ValueError):
            raise TaskStateError("state_corrupt", "a recorded call binding is unreadable") from None
        if existing.logical_call_id == binding.logical_call_id:
            if (
                existing.step_id == binding.step_id
                and existing.attempt == binding.attempt
                and existing.plan_revision == binding.plan_revision
            ):
                return _project(db, task)
            raise TaskStateError(
                "call_binding_conflict",
                "that logical call is already bound to another step attempt",
            )

    attempts = (
        db.query(TaskStepAttempt)
        .filter_by(run_id=task.run_id, step_id=binding.step_id)
        .count()
    )
    if binding.attempt != attempts + 1:
        raise TaskStateError(
            "attempt_order",
            "the call would skip or repeat a step attempt ordinal",
        )

    task.call_bindings_json = json.dumps(
        [*recorded, binding.model_dump(mode="json")],
        sort_keys=True,
    )
    task.fence = claim.fence
    task.updated_at = _now()
    db.flush()
    return _project(db, task)


def record_verification(
    db,
    claim: Claim,
    *,
    step_id: str,
    report: VerificationReport,
) -> TaskProgressProjection:
    """Commit one step's verdict, recording only evidence the records hold.

    The report is a verdict over durable records, so recording cannot add
    evidence: a passing verdict only names the step and the ready artifacts its
    own committed operation published, and a failing verdict keeps the step
    unverified while recording the disposition the records support. A skipped
    step is refused outright, because a user's skip is never a verified success.
    """
    if not isinstance(report, VerificationReport):
        raise TaskStateError("invalid_report", "a strict verification report is required")
    state = _claim_state(db, claim)
    task = state.task
    if task is None:
        raise TaskStateError("task_missing", "no plan has been saved for this run")
    plan_row = _current_plan(db, task)
    if plan_row is None:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row)
    step = next((item for item in plan.steps if item.id == step_id), None)
    if step is None or report.step_id != step_id:
        raise TaskStateError(
            "step_unknown",
            "the report does not name a step of the current plan",
        )
    skipped = [item for item in _json(task.skipped_steps_json, [])]
    if step_id in skipped:
        raise TaskStateError(
            "skip_not_verifiable",
            "a skipped step is recorded as skipped and never as verified",
        )

    attempts = (
        db.query(TaskStepAttempt)
        .filter_by(run_id=task.run_id, step_id=step_id)
        .order_by(TaskStepAttempt.ordinal)
        .all()
    )
    latest = attempts[-1] if attempts else None
    counters = _counters(task)

    if not report.passed:
        disposition = "none"
        if latest is not None and latest.status == "failed":
            disposition = "known_failure"
        elif latest is not None and latest.status == "uncertain":
            disposition = "uncertain"
        repairs = dict(counters.repairs_by_step)
        if latest is not None:
            repairs[step_id] = max(0, len(attempts) - 1)
        task.counters_json = counters.model_copy(
            update={"repairs_by_step": repairs}
        ).model_dump_json()
        task.last_disposition = disposition
        task.fence = claim.fence
        task.updated_at = _now()
        db.flush()
        append_event(
            db,
            conversation_id=task.conversation_id,
            run_id=task.run_id,
            event_type="task.verification_recorded",
            payload={
                "run_id": str(task.run_id),
                "revision": task.current_revision,
                "step_id": step_id,
                "passed": False,
                "missing": list(report.missing),
                "disposition": disposition,
            },
        )
        db.flush()
        append_event(
            db,
            conversation_id=task.conversation_id,
            run_id=task.run_id,
            event_type="task.disposition",
            payload={
                "schema_version": 1,
                "run_id": str(task.run_id),
                "revision": task.current_revision,
                "step_id": step_id,
                "disposition": disposition,
            },
        )
        db.flush()
        append_event(
            db,
            conversation_id=task.conversation_id,
            run_id=task.run_id,
            event_type="task.progress",
            payload=_progress_payload(db, task),
        )
        db.flush()
        return _project(db, task)

    latest = attempts[-1] if attempts else None
    if latest is None or latest.status != "succeeded":
        raise TaskStateError(
            "verification_conflict",
            "a passing verdict requires a committed successful attempt",
        )
    operation = (
        db.query(ToolOperation)
        .filter_by(id=latest.operation_id)
        .one_or_none()
    )
    if operation is None or operation.status != "succeeded":
        raise TaskStateError(
            "verification_conflict",
            "a passing verdict requires a committed successful operation",
        )
    published = _json(operation.output_artifact_ids, [])
    if not isinstance(published, list):
        raise TaskStateError("state_corrupt", "operation artifact ids are not a list")
    rows = []
    for item in published:
        try:
            identity = uuid.UUID(str(item))
        except (TypeError, ValueError):
            raise TaskStateError("state_corrupt", "operation artifact id is invalid") from None
        row = db.query(Artifact).filter_by(id=identity).one_or_none()
        if (
            row is None
            or row.status != "ready"
            or row.project_id != operation.project_id
            or row.conversation_id != operation.conversation_id
            or row.originating_run_id != operation.run_id
            or row.originating_operation_id != operation.id
        ):
            raise TaskStateError(
                "artifact_scope",
                "a published artifact is missing, unready or belongs to another scope",
            )
        rows.append(row)
    if step.is_result_only:
        # A result-only local step commits its own result as durable evidence
        # instead of publishing a new artifact. The recorder independently
        # re-validates the committed result against the stored artifact it
        # references, so a forged or stale verdict cannot be recorded.
        from .verification import _result_only_evidence

        result_ok, _reason = _result_only_evidence(operation, db)
        if not result_ok:
            raise TaskStateError(
                "verification_conflict",
                "a passing result-only verdict requires committed evidence that "
                "still matches the stored artifact",
            )
    else:
        if not rows:
            raise TaskStateError(
                "verification_conflict",
                "a passing verdict requires published evidence to record",
            )

    # Recording a passing verdict is not authority for the bytes behind it:
    # the verification module is what proves the stored bytes still match the
    # identity the record claims, so a record whose volume no longer proves it
    # cannot be written as verified.
    from .verification import _stored_bytes

    for row in rows:
        if _stored_bytes(row) is None:
            raise TaskStateError(
                "verification_conflict",
                "a passing verdict requires stored bytes that prove the recorded identity",
            )

    recorded = {ref.id for ref in _verified_outputs(task)}
    additions = [
        ArtifactRef(
            id=row.id,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            media_type=row.media_type,
        )
        for row in rows
        if row.id not in recorded
    ]
    steps = [item for item in _verified_steps(task)]
    if step_id not in steps:
        steps.append(step_id)
    outputs = [ref.model_dump(mode="json") for ref in _verified_outputs(task)]
    outputs.extend(ref.model_dump(mode="json") for ref in additions)
    task.verified_steps_json = json.dumps(steps)
    task.verified_outputs_json = json.dumps(outputs)

    plan_steps = [item.id for item in plan.steps]
    covered = all(item in steps for item in plan_steps)
    task.status = "completed" if covered else "verification_pending"
    task.last_disposition = "none"
    task.fence = claim.fence
    task.updated_at = _now()
    db.flush()
    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.verification_recorded",
        payload={
            "run_id": str(task.run_id),
            "revision": task.current_revision,
            "step_id": step_id,
            "passed": True,
            "artifact_ids": [str(row.id) for row in rows],
            "task_status": task.status,
        },
    )
    db.flush()
    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.disposition",
        payload={
            "schema_version": 1,
            "run_id": str(task.run_id),
            "revision": task.current_revision,
            "step_id": step_id,
            "disposition": task.last_disposition,
        },
    )
    db.flush()
    append_event(
        db,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        event_type="task.progress",
        payload=_progress_payload(db, task),
    )
    db.flush()
    return _project(db, task)
