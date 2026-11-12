"""Caller-owned transactions for durable task waits and decision expiry."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...artifacts.models import Artifact
from ...capabilities.contracts import canonical_digest
from ...chat.lifecycle import ConversationLifecycleError, get_owned_conversation_scope
from ...chat.models import Conversation
from ...connectors.blockers import (
    _validate_references_in_session,
    _validate_resource_bindings_in_session,
)
from ...events.service import append_event
from ...execution.models import ToolAttempt, ToolOperation
from ...sandboxes.models import WorkflowSandbox
from ...workspace.models import Project
from ..models import ACTIVE_RUN_STATUSES, Run, RunJob
from ..queue import Claim
from .clock import close_active_window
from .contracts import (
    ExecutionConfigV5,
    TaskCounters,
    TaskDecisionCommand,
    TaskProgress,
    TaskRecoveryReport,
    _public_text,
)
from .models import (
    TaskDecisionCommandRecord,
    TaskDecisionRecord,
    TaskRecord,
    TaskStepAttempt,
)
from .projections import TaskProgressProjection, recovery_summary
from .state import (
    TaskStateError,
    _claim_state,
    _current_plan,
    _default_context,
    _load_plan,
    _project,
)

_WAIT_STATUS = {
    "plan_confirmation": "waiting_plan",
    "clarification": "waiting_input",
    "configuration": "waiting_config",
    "recovery": "waiting_input",
}


def _execution_digest(run: Run) -> str:
    return canonical_digest(
        {
            "execution_configuration": run.execution_configuration,
            "model_configuration": run.model_configuration,
            "model_selection": run.model_selection,
            "tool_policy_revision": run.tool_policy_revision,
            "graph_version": run.graph_version,
        }
    )


def _operation_digest(operation: ToolOperation) -> str:
    return canonical_digest(
        {
            "id": str(operation.id),
            "run_id": str(operation.run_id),
            "project_id": str(operation.project_id),
            "conversation_id": str(operation.conversation_id),
            "principal_id": str(operation.principal_id),
            "logical_call_id": operation.logical_call_id,
            "capability_id": operation.capability_id,
            "capability_version": operation.capability_version,
            "manifest_digest": operation.manifest_digest,
            "request_digest": operation.request_digest,
            "arguments_digest": operation.arguments_digest,
            "execution_bundle_digest": operation.execution_bundle_digest,
            "configuration_revision": operation.configuration_revision,
            "tool_policy_revision": operation.tool_policy_revision,
            "status": operation.status,
        }
    )


def _decision_error(status: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": "Decision unavailable"}},
    )


def apply_decision(
    db: Session,
    *,
    principal_id: uuid.UUID,
    run_id: uuid.UUID,
    command: TaskDecisionCommand,
    now: datetime,
) -> TaskProgress:
    """Consume a decision within the caller's transaction; never dispatch a tool."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    now = now.astimezone(UTC)
    identity = db.query(Run.conversation_id).filter_by(id=run_id).one_or_none()
    if identity is None:
        raise _decision_error(404, "decision_not_found")
    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=principal_id,
            conversation_id=identity.conversation_id,
            for_update=True,
        )
    except ConversationLifecycleError:
        raise _decision_error(404, "decision_not_found") from None
    run = (
        db.query(Run)
        .filter_by(id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = (
        db.query(RunJob)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    task = (
        db.query(TaskRecord)
        .filter_by(run_id=run_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if (
        run is None
        or job is None
        or task is None
        or run.project_id != project.id
        or run.conversation_id != conversation.id
        or task.project_id != project.id
        or task.conversation_id != conversation.id
        or task.initiator_id != principal_id
    ):
        raise _decision_error(404, "decision_not_found")
    decision = (
        db.query(TaskDecisionRecord)
        .filter_by(run_id=run_id, request_id=command.request_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if decision is None:
        raise _decision_error(409, "decision_conflict")
    if decision.actor_id != principal_id or decision.task_id != task.id:
        raise _decision_error(404, "decision_not_found")
    existing = (
        db.query(TaskDecisionCommandRecord)
        .filter_by(run_id=run_id, client_request_id=command.client_request_id)
        .one_or_none()
    )
    if existing is None:
        existing = (
            db.query(TaskDecisionCommandRecord)
            .filter_by(run_id=run_id, decision_id=decision.id)
            .first()
        )
    if existing is not None:
        if (
            existing.request_id,
            existing.action,
            existing.plan_revision,
            existing.expected_digest,
            existing.answer,
        ) != (
            command.request_id,
            command.action,
            command.plan_revision,
            command.expected_digest,
            command.answer,
        ):
            raise _decision_error(409, "decision_conflict")
        if existing.client_request_id != command.client_request_id:
            db.add(
                TaskDecisionCommandRecord(
                    task_id=existing.task_id,
                    run_id=existing.run_id,
                    decision_id=existing.decision_id,
                    client_request_id=command.client_request_id,
                    request_id=existing.request_id,
                    action=existing.action,
                    plan_revision=existing.plan_revision,
                    expected_digest=existing.expected_digest,
                    answer=existing.answer,
                    result_json=existing.result_json,
                )
            )
            db.flush()
        return TaskProgressProjection.model_validate_json(existing.result_json)
    deadline = decision.expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if (
        decision.state != "open"
        or deadline <= now
        or task.status != "waiting"
        or run.status != _WAIT_STATUS.get(decision.kind)
        or run.ended_at is not None
        or job.owner_id is not None
        or job.lease_until is not None
        or task.fence != run.fence
        or job.fence != run.fence
        or decision.plan_revision != task.current_revision
        or command.plan_revision != decision.plan_revision
        or command.expected_digest != decision.expected_digest
        or (decision.kind != "recovery" and task.digest != decision.expected_digest)
    ):
        raise _decision_error(409, "decision_conflict")
    actions = {
        "plan_confirmation": {"confirm", "reject"},
        "clarification": {"answer"},
        "configuration": {"resume_configuration"},
        "recovery": {"abort", "repeat_step", "continue_next"},
    }
    if command.action not in actions.get(decision.kind, set()):
        raise _decision_error(409, "decision_action_unavailable")
    if decision.kind == "recovery":
        try:
            report = TaskRecoveryReport.model_validate_json(
                json.dumps(json.loads(decision.request_json)["recovery_report"])
            )
        except (KeyError, TypeError, ValueError):
            raise _decision_error(409, "decision_conflict") from None
        if (
            report.digest != decision.expected_digest
            or report.plan_digest != task.digest
            or report.plan_revision != task.current_revision
        ):
            raise _decision_error(409, "decision_conflict")
        # ``blockers`` explains why some actions are unavailable; it is not a
        # global veto. ``eligible_actions`` is the authoritative offer, so an
        # eligible action stays selectable even when another action is blocked.
        if command.action != "abort" and (
            report.operation_id is None or command.action not in report.eligible_actions
        ):
            raise _decision_error(409, "recovery_ineligible")
    rejecting = command.action in {"reject", "abort"}
    if not rejecting:
        active_run = (
            db.query(Run.id)
            .filter(
                Run.conversation_id == conversation.id,
                Run.id != run.id,
                Run.status.in_(ACTIVE_RUN_STATUSES),
            )
            .first()
        )
        if active_run is not None:
            raise _decision_error(409, "run_already_active")
        unsettled = (
            db.query(ToolOperation)
            .filter(
                ToolOperation.run_id == run.id,
                ToolOperation.status.in_(
                    ("admitted", "dispatching", "running", "interrupted")
                ),
            )
            .with_for_update()
            .populate_existing()
            .all()
        )
        if decision.kind != "recovery" and unsettled:
            raise _decision_error(409, "execution_unavailable")
        try:
            request_data = json.loads(decision.request_json)
            if not isinstance(request_data, dict):
                raise TypeError("invalid decision request")
            if not request_data.get("execution_digest"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "decision_pins_unverifiable",
                            "message": "The original execution pins cannot be verified. Cancel this run and start a new task; no settings were replaced.",
                        }
                    },
                )
            execution = ExecutionConfigV5.model_validate_json(
                run.execution_configuration or ""
            )
            execution_digest = _execution_digest(run)
        except (ValueError, TypeError, AttributeError):
            raise _decision_error(409, "execution_unavailable") from None
        context = execution.task_context
        execution_deadline = run.execution_deadline_at
        if execution_deadline is not None and execution_deadline.tzinfo is None:
            execution_deadline = execution_deadline.replace(tzinfo=UTC)
        if (
            request_data.get("execution_digest") != execution_digest
            or context.model_dump(mode="json") != json.loads(task.context_json)
            or project.archived_at is not None
            or conversation.archived_at is not None
            or (execution_deadline is not None and execution_deadline <= now)
            or not _validate_references_in_session(db, project.id, execution)
            or not _validate_resource_bindings_in_session(db, project.id, execution)
        ):
            raise _decision_error(409, "execution_unavailable")
        for ref in execution.selected_artifacts:
            artifact = (
                db.query(Artifact)
                .filter_by(
                    id=ref.id,
                    project_id=project.id,
                    conversation_id=conversation.id,
                    status="ready",
                )
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if artifact is None or (
                artifact.sha256,
                artifact.size_bytes,
                artifact.media_type,
            ) != (ref.sha256, ref.size_bytes, ref.media_type):
                raise _decision_error(409, "execution_unavailable")
        from ...sandboxes.profiles import (
            TaskProfileError,
            resolve_task_requirements,
            runtime_catalog,
        )

        plan_row = _current_plan(db, task)
        if task.current_revision and plan_row is None:
            raise _decision_error(409, "execution_unavailable")
        try:
            plan = None
            if plan_row is not None:
                plan = _load_plan(plan_row)
                for step in plan.steps:
                    if step.profile_id is not None:
                        resolve_task_requirements(
                            execution, step.profile_id, runtime_catalog()
                        )
        except (TaskProfileError, TaskStateError):
            raise _decision_error(409, "execution_unavailable") from None
        if decision.kind == "recovery":
            operation = unsettled[0] if len(unsettled) == 1 else None
            steps = {step.id: step for step in plan.steps} if plan else {}
            step = steps.get(report.step_id)
            progress = _project(db, task)
            bindings = [
                binding
                for binding in progress.call_bindings
                if binding.step_id == report.step_id
                and binding.attempt == report.attempt
            ]
            attempts = (
                db.query(TaskStepAttempt)
                .filter_by(run_id=run.id, step_id=report.step_id)
                .with_for_update()
                .populate_existing()
                .all()
            )
            operation_attempt = (
                db.query(TaskStepAttempt)
                .filter_by(run_id=run.id, operation_id=report.operation_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            dispatch = (
                db.query(ToolAttempt)
                .filter_by(operation_id=report.operation_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            sandbox = (
                db.query(WorkflowSandbox)
                .filter_by(project_id=project.id, conversation_id=conversation.id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if (
                operation is None
                or operation.id != report.operation_id
                or request_data.get("recovery_operation_digest")
                != _operation_digest(operation)
                or operation.status != "admitted"
                or dispatch is not None
                or operation.project_id != project.id
                or operation.conversation_id != conversation.id
                or operation.principal_id != principal_id
                or step is None
                or step.capability is None
                or step.capability not in execution.allowed_capabilities
                or (
                    operation.capability_id,
                    operation.capability_version,
                    operation.manifest_digest,
                )
                != (
                    step.capability.id,
                    step.capability.version,
                    step.capability.manifest_digest,
                )
                or len(bindings) != 1
                or bindings[0].plan_revision != report.plan_revision
                or bindings[0].logical_call_id != operation.logical_call_id
                or (
                    operation_attempt is not None
                    and (
                        operation_attempt.task_id != task.id
                        or operation_attempt.step_id != report.step_id
                        or operation_attempt.ordinal != report.attempt
                        or operation_attempt.plan_revision != report.plan_revision
                    )
                )
                or any(
                    a.ordinal >= report.attempt
                    and (
                        a.ordinal != report.attempt
                        or a.operation_id != operation.id
                        or a.plan_revision != report.plan_revision
                    )
                    for a in attempts
                )
                or report.step_id in progress.verified_steps
                or report.step_id in progress.skipped_steps
                or (sandbox is not None and sandbox.command_uncertain_until is not None)
                or (
                    command.action == "repeat_step"
                    and (
                        report.attempt
                        >= min(3, execution.task_limits.max_repairs_per_step + 1)
                        or progress.counters.repairs_by_step.get(report.step_id, 0)
                        >= execution.task_limits.max_repairs_per_step
                        or progress.counters.tool_calls
                        >= min(
                            execution.task_limits.max_tool_calls,
                            execution.loop_limits.max_tool_calls,
                        )
                        or progress.counters.model_turns
                        >= min(
                            execution.task_limits.max_model_turns,
                            execution.loop_limits.max_model_turns,
                        )
                        or progress.counters.generation_attempts
                        >= min(
                            execution.task_limits.max_generation_attempts,
                            execution.loop_limits.max_generation_attempts,
                        )
                        or progress.counters.output_tokens
                        >= min(
                            execution.task_limits.max_total_output_tokens,
                            execution.loop_limits.max_total_output_tokens,
                        )
                    )
                )
                or (
                    command.action == "continue_next"
                    and any(
                        report.step_id in candidate.depends_on
                        for candidate in steps.values()
                    )
                )
            ):
                raise _decision_error(409, "recovery_ineligible")
    decision.state = (
        "rejected"
        if rejecting
        else ("answered" if command.action == "answer" else "confirmed")
    )
    decision.resolved_at = now
    task.updated_at = now
    if rejecting:
        task.status = "cancelled"
        run.status = "cancelled"
        run.error_code = (
            "plan_rejected" if command.action == "reject" else "recovery_aborted"
        )
        run.ended_at = now
        run.fence += 1
        task.fence = job.fence = run.fence
    else:
        from ...messaging.models import MessageTransportState
        from ...messaging.outbox import enqueue_run

        if command.action == "continue_next":
            # The skip is the durable record of the choice: the step is settled
            # as skipped (never verified, never replayed) inside the same
            # transaction that re-queues the run, so a crash leaves either both
            # or neither.
            skipped = json.loads(task.skipped_steps_json or "[]")
            if not isinstance(skipped, list):
                raise _decision_error(409, "state_corrupt")
            if report.step_id not in skipped:
                skipped.append(report.step_id)
            task.skipped_steps_json = json.dumps(skipped, sort_keys=True)
        task.status = "planned" if task.current_revision else "unknown"
        if command.action in {"repeat_step", "continue_next"}:
            # The uncertainty now has an owner: the explicit choice is what
            # says the step runs again (or is skipped), so the ledger's
            # "uncertain" disposition is answered rather than left to re-open
            # the question. An unsettled binding still keeps the read honest
            # until the new attempt settles, and a crash there diagnoses
            # again from the durable rows.
            task.last_disposition = "none"
        run.status = "queued"
        run.error_code = None
        job.available_at = now
        job.dispatch_generation += 1
        transport = db.query(MessageTransportState).filter_by(id=1).one()
        enqueue_run(
            db, run.id, job.dispatch_generation, epoch=transport.transport_epoch
        )
    db.flush()
    append_event(
        db,
        conversation_id=conversation.id,
        run_id=run.id,
        event_type="task.decision_resolved",
        actor_id=principal_id,
        payload={
            "schema_version": 1,
            "run_id": str(run.id),
            "revision": decision.plan_revision,
            "request_id": str(decision.request_id),
            "state": decision.state,
        },
    )
    db.flush()
    result = _project(db, task)
    db.add(
        TaskDecisionCommandRecord(
            task_id=task.id,
            run_id=run.id,
            decision_id=decision.id,
            client_request_id=command.client_request_id,
            request_id=command.request_id,
            action=command.action,
            plan_revision=command.plan_revision,
            expected_digest=command.expected_digest,
            answer=command.answer,
            result_json=result.model_dump_json(),
        )
    )
    db.flush()
    return result


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
    if run is not None and run.execution_configuration:
        request["execution_digest"] = _execution_digest(run)
    if recovery_report is not None:
        request["recovery_report"] = recovery_report.model_dump(mode="json")
    if decision is not None:
        stored_request = json.loads(decision.request_json)
        replay_request = dict(request)
        if "execution_digest" not in stored_request:
            replay_request.pop("execution_digest", None)
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
            and {
                key: value
                for key, value in stored_request.items()
                if key != "recovery_operation_digest"
            }
            == replay_request
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
        progress = _project(db, task)
        if (
            task.current_revision != report.plan_revision
            or task.digest != report.plan_digest
            or report.step_id not in progress.step_ids
        ):
            raise TaskStateError(
                "recovery_conflict",
                "recovery report does not bind the current plan",
            )
        if report.operation_id is None:
            # A bound logical call with no admitted operation can only be
            # aborted: there is no operation identity to repeat or continue.
            bindings = [
                binding
                for binding in progress.call_bindings
                if binding.step_id == report.step_id
                and binding.attempt == report.attempt
            ]
            if (
                operations
                or interrupted_dispatches
                or tuple(report.eligible_actions) != ("abort",)
                or len(bindings) != 1
                or bindings[0].plan_revision != report.plan_revision
            ):
                raise TaskStateError(
                    "recovery_conflict",
                    "abort-only report does not bind the unsettled call",
                )
            task.last_disposition = "uncertain"
        else:
            operation = (
                db.query(ToolOperation)
                .filter_by(id=report.operation_id, run_id=run.id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if (
                operation is None
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
            attempt = (
                db.query(TaskStepAttempt)
                .filter_by(run_id=run.id, operation_id=operation.id)
                .one_or_none()
            )
            ordinal = (
                db.query(TaskStepAttempt)
                .filter_by(
                    run_id=run.id, step_id=report.step_id, ordinal=report.attempt
                )
                .one_or_none()
            )
            bindings = [
                binding
                for binding in progress.call_bindings
                if binding.logical_call_id == operation.logical_call_id
                or (
                    binding.step_id == report.step_id
                    and binding.attempt == report.attempt
                )
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
            request["recovery_operation_digest"] = _operation_digest(operation)
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
    close_active_window(task, now)
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
            "expected_digest": decision.expected_digest,
            "prompt": prompt,
            "expires_at": decision.expires_at.isoformat(),
            # A recovery carries its diagnosis so a reconnect can show the
            # same choices the reader was offered the first time. The
            # diagnosis names the operation it diagnosed, this view does not.
            "diagnosis": (
                recovery_summary(recovery_report).model_dump(mode="json")
                if recovery_report is not None
                else None
            ),
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
