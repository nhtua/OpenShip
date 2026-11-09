"""Caller-owned transactions for durable task waits and decision expiry."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ...capabilities.contracts import canonical_digest
from ...chat.models import Conversation
from ...events.service import append_event
from ...execution.models import ToolOperation
from ...workspace.models import Project
from ..models import Run, RunJob
from ..queue import Claim
from .contracts import TaskCounters, TaskProgress, TaskRecoveryReport, _public_text
from .models import TaskDecisionRecord, TaskRecord, TaskStepAttempt
from .state import TaskStateError, _claim_state, _default_context, _project

_WAIT_STATUS = {
    "plan_confirmation": "waiting_plan",
    "clarification": "waiting_input",
    "configuration": "waiting_config",
    "recovery": "waiting_input",
}


def park_task_wait(
    db: Session,
    claim: Claim,
    *,
    kind: str,
    prompt: str,
    now: datetime,
    recovery_report: TaskRecoveryReport | None = None,
) -> TaskProgress:
    """Park an owned task, atomically recording its decision and fencing its worker."""
    if kind not in _WAIT_STATUS or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("invalid wait kind or timestamp")
    _public_text(prompt, 10000)
    if (kind == "recovery") != isinstance(recovery_report, TaskRecoveryReport):
        raise ValueError("recovery waits require exactly one recovery report")
    now = now.astimezone(UTC)

    # A replay may read the exact parked request, but may never mutate it or
    # revive the released claim. Lock the same scope before examining it.
    if not isinstance(claim, Claim) or claim.user_id is None:
        raise TaskStateError("invalid_claim", "a worker claim is required")
    identity = (
        db.query(Run.conversation_id, Run.project_id)
        .filter_by(id=claim.run_id)
        .one_or_none()
    )
    if identity is None:
        raise TaskStateError("run_not_found", "the claimed run does not exist")
    conversation = (
        db.query(Conversation)
        .filter_by(id=identity.conversation_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    project = (
        db.query(Project)
        .filter_by(id=identity.project_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    run = (
        db.query(Run)
        .filter_by(id=claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = (
        db.query(RunJob)
        .filter_by(run_id=claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    task = (
        db.query(TaskRecord)
        .filter_by(run_id=claim.run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    decision = (
        db.query(TaskDecisionRecord)
        .filter_by(run_id=claim.run_id, state="open")
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if (
        not all((conversation, project, run, job))
        or conversation.user_id != claim.user_id
        or conversation.project_id != project.id
        or run.project_id != project.id
        or run.conversation_id != conversation.id
    ):
        raise TaskStateError("claim_scope", "the claim scope is invalid")
    request = {"prompt": prompt}
    if recovery_report is not None:
        request["recovery_report"] = recovery_report.model_dump(mode="json")
    if decision is not None:
        if (
            task is not None
            and task.id == decision.task_id
            and task.status == "waiting"
            and run.status == _WAIT_STATUS[kind]
            and run.fence == claim.fence + 1
            and job.fence == run.fence
            and task.fence == run.fence
            and job.owner_id is None
            and job.lease_until is None
            and decision.kind == kind
            and json.loads(decision.request_json) == request
            and decision.actor_id == claim.user_id
        ):
            return _project(db, task)
        raise TaskStateError("decision_conflict", "another decision is already open")

    state = _claim_state(db, claim)
    run, job, task = state.run, state.job, state.task
    lease = job.lease_until
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=UTC)
    if (
        run.status != "running"
        or lease is None
        or lease <= now
        or run.ended_at is not None
    ):
        raise TaskStateError("stale_claim", "the worker lease is not live")
    if task is not None and (
        task.fence != claim.fence
        or task.initiator_id != claim.user_id
        or task.project_id != run.project_id
        or task.conversation_id != run.conversation_id
    ):
        raise TaskStateError("stale_claim", "the task fence or scope has moved")
    operations = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run.id,
            ToolOperation.status.in_(("admitted", "dispatching", "running")),
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    interrupted_dispatches = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run.id,
            ToolOperation.status == "interrupted",
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    if kind != "recovery" and (operations or interrupted_dispatches):
        raise TaskStateError(
            "operation_unsettled", "ordinary waits cannot hide an unsettled operation"
        )
    if task is None:
        if kind not in {"clarification", "configuration"}:
            raise TaskStateError("plan_missing", "this wait requires a plan")
        context = _default_context(run, claim)
        task = TaskRecord(
            run_id=run.id,
            project_id=run.project_id,
            conversation_id=run.conversation_id,
            initiator_id=claim.user_id,
            scope=context["scope"],
            mode=context["mode"],
            graph_version=context["graph_version"],
            current_revision=0,
            status="unknown",
            digest=canonical_digest(context),
            fence=claim.fence,
            context_json=json.dumps(context),
            counters_json=TaskCounters().model_dump_json(),
        )
        db.add(task)
        db.flush()
    if kind == "plan_confirmation" and task.current_revision == 0:
        raise TaskStateError("plan_missing", "plan confirmation requires a plan")
    if recovery_report is not None:
        report = recovery_report
        operation = (
            db.query(ToolOperation)
            .filter_by(id=report.operation_id, run_id=run.id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if (
            task.current_revision != report.plan_revision
            or task.digest != report.plan_digest
            or operation is None
            or operation.project_id != run.project_id
            or operation.conversation_id != run.conversation_id
            or operation.principal_id != claim.user_id
            or operation.status
            not in {"admitted", "dispatching", "running", "interrupted"}
            or any(item.id != operation.id for item in operations)
            or any(item.id != operation.id for item in interrupted_dispatches)
        ):
            raise TaskStateError(
                "recovery_conflict",
                "recovery report does not bind the unsettled operation",
            )
        progress = _project(db, task)
        if report.step_id not in progress.step_ids:
            raise TaskStateError(
                "recovery_conflict", "recovery step is not in the plan"
            )
        attempt = (
            db.query(TaskStepAttempt)
            .filter_by(run_id=run.id, operation_id=operation.id)
            .one_or_none()
        )
        ordinal = (
            db.query(TaskStepAttempt)
            .filter_by(run_id=run.id, step_id=report.step_id, ordinal=report.attempt)
            .one_or_none()
        )
        bindings = [
            binding
            for binding in progress.call_bindings
            if binding.logical_call_id == operation.logical_call_id
            or (binding.step_id == report.step_id and binding.attempt == report.attempt)
        ]
        if (
            (attempt is None and not bindings)
            or (
                attempt is not None
                and (
                    attempt.task_id != task.id
                    or attempt.step_id != report.step_id
                    or attempt.ordinal != report.attempt
                    or attempt.plan_revision != report.plan_revision
                )
            )
            or (ordinal is not None and ordinal.operation_id != operation.id)
            or any(
                binding.logical_call_id != operation.logical_call_id
                or binding.step_id != report.step_id
                or binding.attempt != report.attempt
                or binding.plan_revision != report.plan_revision
                for binding in bindings
            )
        ):
            raise TaskStateError(
                "recovery_conflict", "recovery attempt identity does not match"
            )
        task.last_disposition = "uncertain"
    digest = recovery_report.digest if recovery_report else task.digest
    if not digest:
        raise TaskStateError("state_corrupt", "the task has no decision digest")
    decision = TaskDecisionRecord(
        task_id=task.id,
        run_id=run.id,
        request_id=uuid.uuid4(),
        kind=kind,
        plan_revision=task.current_revision,
        expected_digest=digest,
        state="open",
        request_json=json.dumps(request),
        actor_id=claim.user_id,
        expires_at=now + timedelta(hours=24),
    )
    db.add(decision)
    task.status = "waiting"
    task.updated_at = now
    run.status = _WAIT_STATUS[kind]
    run.fence = claim.fence + 1
    job.fence = task.fence = run.fence
    job.owner_id = job.lease_until = None
    db.flush()
    append_event(
        db,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="task.decision_requested",
        payload={
            "schema_version": 1,
            "run_id": str(run.id),
            "revision": task.current_revision,
            "request_id": str(decision.request_id),
            "kind": kind,
            "prompt": prompt,
            "expires_at": decision.expires_at.isoformat(),
        },
    )
    db.flush()
    return _project(db, task)


def expire_task_decisions(db: Session, *, now: datetime, batch_size: int = 100) -> int:
    """Expire up to batch_size matching waits, without committing the caller's work."""
    if now.tzinfo is None or now.utcoffset() is None or batch_size < 1:
        raise ValueError("an aware timestamp and positive batch size are required")
    now = now.astimezone(UTC)
    candidates = (
        db.query(TaskDecisionRecord.id, Run.conversation_id, Run.project_id)
        .join(Run, Run.id == TaskDecisionRecord.run_id)
        .filter(
            TaskDecisionRecord.state == "open", TaskDecisionRecord.expires_at <= now
        )
        .order_by(TaskDecisionRecord.expires_at, TaskDecisionRecord.id)
        .limit(batch_size)
        .all()
    )
    expired = 0
    for decision_id, conversation_id, project_id in candidates:
        conversation = (
            db.query(Conversation)
            .filter_by(id=conversation_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        project = (
            db.query(Project)
            .filter_by(id=project_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        identity = (
            db.query(TaskDecisionRecord.run_id).filter_by(id=decision_id).one_or_none()
        )
        if identity is None:
            continue
        run = (
            db.query(Run)
            .filter_by(id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        job = (
            db.query(RunJob)
            .filter_by(run_id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        task = (
            db.query(TaskRecord)
            .filter_by(run_id=identity.run_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        decision = (
            db.query(TaskDecisionRecord)
            .filter_by(id=decision_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if not all((conversation, project, run, job, task, decision)):
            continue
        deadline = decision.expires_at
        if deadline.tzinfo is None:  # SQLite returns naive UTC timestamps.
            deadline = deadline.replace(tzinfo=UTC)
        if (
            decision.state != "open"
            or deadline > now
            or run.conversation_id != conversation.id
            or run.project_id != project.id
            or conversation.project_id != project.id
            or task.project_id != project.id
            or task.conversation_id != conversation.id
            or decision.task_id != task.id
            or task.status != "waiting"
            or run.status != _WAIT_STATUS.get(decision.kind)
            or job.owner_id is not None
            or job.lease_until is not None
        ):
            continue
        decision.state = "expired"
        decision.resolved_at = now
        task.status = "blocked"
        task.updated_at = now
        run.status = "timed_out"
        run.error_code = "decision_expired"
        run.ended_at = now
        run.fence = max(run.fence, job.fence) + 1
        job.fence = run.fence
        task.fence = run.fence
        append_event(
            db,
            conversation_id=conversation.id,
            run_id=run.id,
            event_type="task.decision_resolved",
            payload={
                "schema_version": 1,
                "run_id": str(run.id),
                "revision": decision.plan_revision,
                "request_id": str(decision.request_id),
                "state": "expired",
            },
        )
        db.flush()
        expired += 1
    return expired
