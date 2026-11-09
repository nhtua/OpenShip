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
    TaskAttempt,
    TaskCounters,
    TaskDecision,
    TaskPlan,
)
from .models import (
    TaskDecisionRecord,
    TaskPlanRecord,
    TaskRecord,
    TaskStepAttempt,
)
from .projections import TaskProgressProjection, TaskStepProgress

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
        return tuple(ArtifactRef.model_validate(item) for item in raw)
    except ValueError as exc:
        raise TaskStateError(
            "state_corrupt", "verified outputs are invalid"
        ) from exc


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
    return TaskDecision(
        request_id=row.request_id,
        kind=row.kind,
        plan_revision=row.plan_revision,
        expected_digest=row.expected_digest,
        expires_at=row.expires_at,
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
        return TaskProgressProjection()
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
            .order_by(
                TaskStepAttempt.step_id.asc(), TaskStepAttempt.ordinal.asc()
            )
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
        pending_decision=_pending_decision(db, task),
        counters=_counters(task),
        last_disposition=task.last_disposition,
        steps=tuple(steps),
    )


def read_progress(db, run_id: uuid.UUID) -> TaskProgressProjection:
    """Read the current durable projection without locking or mutating."""
    task = (
        db.query(TaskRecord)
        .filter(TaskRecord.run_id == run_id)
        .one_or_none()
    )
    return _project(db, task)


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
    if (
        job is None
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
    ):
        raise TaskStateError("stale_claim", "the job is not owned by this claim")
    return _ClaimState(run=run, job=job, task=task)


def _default_context(run: Run, claim: Claim) -> dict:
    return {
        "root_run_id": str(run.id),
        "project_id": str(run.project_id),
        "conversation_id": str(run.conversation_id),
        "initiator_id": str(claim.user_id),
        "scope": "conversation",
        "mode": "copilot",
        "graph_version": 5,
    }


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
        task = TaskRecord(
            run_id=state.run.id,
            project_id=state.run.project_id,
            conversation_id=state.run.conversation_id,
            initiator_id=claim.user_id,
            scope="conversation",
            mode="copilot",
            graph_version=5,
            current_revision=1,
            status="planned",
            digest=digest,
            fence=claim.fence,
            context_json=json.dumps(_default_context(state.run, claim)),
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
            raise TaskStateError(
                "plan_conflict", "that plan revision is immutable"
            )
        if expected_revision != task.current_revision:
            raise TaskStateError(
                "stale_revision", "the task revision has advanced"
            )
        if plan.revision != task.current_revision + 1:
            raise TaskStateError(
                "stale_revision", "a new plan revision must follow the current one"
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
        event_type="task.plan_saved",
        payload={
            "run_id": str(task.run_id),
            "revision": plan.revision,
            "digest": digest,
            "step_ids": [step.id for step in plan.steps],
            "status": "planned",
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
    if not operation.result_digest:
        raise TaskStateError(
            "operation_incomplete", "the committed operation has no result digest"
        )

    artifact_ids = _json(operation.output_artifact_ids, [])
    if not isinstance(artifact_ids, list):
        raise TaskStateError(
            "state_corrupt", "operation artifact ids are not a list"
        )
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
    if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= 3:
        raise TaskStateError(
            "invalid_attempt", "attempt ordinals are bounded from 1 to 3"
        )
    state = _claim_state(db, claim)
    task = state.task
    if task is None:
        raise TaskStateError(
            "task_missing", "no plan has been saved for this run"
        )

    plan_row = _current_plan(db, task)
    if plan_row is None:
        raise TaskStateError("plan_missing", "the current plan is missing")
    plan = _load_plan(plan_row)
    if step_id not in {step.id for step in plan.steps}:
        raise TaskStateError(
            "step_unknown", "the step does not belong to the current plan"
        )

    operation, outcome, evidence = _validate_operation(
        db, state, claim, operation_id
    )

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
    return _project(db, task)
