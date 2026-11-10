"""Independent transaction-level tests for durable decision expiry."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.openship.runs.models import Event, RunJob
from src.openship.runs.tasks.models import TaskDecisionRecord, TaskRecord
from src.openship.runs.tasks.state import save_plan
from tests.conftest import TestSessionLocal
from tests.test_task_state import _identity, _operation, _plan


def _pinned_execution(run, user, project, conversation):
    from src.openship.sandboxes.profiles import install_runtime_catalog
    from tests.test_task_profile_selection import _all_profiles, _catalog, _execution

    execution = _execution(_catalog(_all_profiles()))
    install_runtime_catalog(execution.profile_catalog)
    context = execution.task_context.model_copy(
        update={
            "root_run_id": run.id,
            "project_id": project.id,
            "conversation_id": conversation.id,
            "initiator_id": user.id,
        }
    )
    run.execution_configuration = execution.model_copy(
        update={"task_context": context}
    ).model_dump_json()


@pytest.fixture(autouse=True)
def _reset_profile_catalog(monkeypatch):
    from src.openship.config import settings
    from src.openship.sandboxes.profiles import reset_runtime_catalog

    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    yield
    reset_runtime_catalog()


def test_lost_response_replays_same_decision_once(monkeypatch):
    from src.openship.execution.gateway import ToolGateway
    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.runs.tasks.models import TaskDecisionCommandRecord

    def no_dispatch(*args, **kwargs):
        pytest.fail("decision handler dispatched a tool")

    monkeypatch.setattr(ToolGateway, "execute_tool", no_dispatch)
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        generation = db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        first = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        db.commit()
        second = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert first == second
        assert run.status == "queued"
        assert (
            db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
            == generation + 1
        )
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )
        assert db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).count() == 1


def test_same_choice_with_new_client_id_replays_but_conflict_is_rejected():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="reject",
        )
        first = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        db.commit()
        retry = command.model_copy(update={"client_request_id": uuid.uuid4()})
        assert (
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=retry, now=now
            )
            == first
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=retry.model_copy(update={"action": "confirm"}),
                now=now,
            )
        assert error.value.status_code == 409


def test_replayed_client_id_cannot_authorize_a_later_decision():
    from fastapi import HTTPException

    from src.openship.runs.queue import Claim
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        first = park_task_wait(
            db, claim, kind="clarification", prompt="First?", now=now
        ).pending_decision
        db.commit()
        cmd = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=first.request_id,
            plan_revision=first.plan_revision,
            expected_digest=first.expected_digest,
            action="answer",
            answer="first answer",
        )
        apply_decision(db, principal_id=user.id, run_id=run.id, command=cmd, now=now)
        db.commit()
        alias = uuid.uuid4()
        apply_decision(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=cmd.model_copy(update={"client_request_id": alias}),
            now=now,
        )
        db.commit()
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        run.status = "running"
        job.owner_id = "next-worker"
        job.lease_until = now + timedelta(minutes=5)
        db.flush()
        next_claim = Claim(
            run_id=run.id,
            fence=run.fence,
            worker_id=job.owner_id,
            lease_until=job.lease_until,
            user_id=user.id,
        )
        second = park_task_wait(
            db, next_claim, kind="clarification", prompt="Second?", now=now
        ).pending_decision
        db.commit()
        next_cmd = TaskDecisionCommand(
            client_request_id=alias,
            request_id=second.request_id,
            plan_revision=second.plan_revision,
            expected_digest=second.expected_digest,
            action="answer",
            answer="second answer",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=next_cmd, now=now
            )
        assert error.value.status_code == 409
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(request_id=second.request_id)
            .one()
            .state
            == "open"
        )


def test_cancel_wins_over_confirmation():
    from fastapi import HTTPException

    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        run.status = "cancelled"
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 0
        )


def test_conflicting_stale_expired_wrong_actor_and_bare_yes_do_not_release():
    from fastapi import HTTPException

    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=uuid.uuid4(), run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 404
        for bad in (
            command.model_copy(update={"expected_digest": "a" * 64}),
            command.model_copy(update={"request_id": uuid.uuid4()}),
            command.model_copy(update={"action": "answer", "answer": "yes"}),
        ):
            with pytest.raises(HTTPException) as error:
                apply_decision(
                    db, principal_id=user.id, run_id=run.id, command=bad, now=now
                )
            assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=command,
                now=now + timedelta(days=2),
            )
        assert error.value.status_code == 409
        assert run.status == "waiting_plan"
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 0
        )


@pytest.mark.parametrize(
    ("kind", "action", "answer"),
    [
        ("clarification", "answer", "Additional details"),
        ("configuration", "resume_configuration", None),
    ],
)
def test_ordinary_decision_resumes_unchanged_execution(kind, action, answer):
    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind=kind, prompt="Details?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action=action,
            answer=answer,
        )
        apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert run.status == "queued"
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )


def test_configuration_resume_cannot_replace_pins():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        ).pending_decision
        db.commit()
        config = json.loads(run.execution_configuration)
        config["aws_read_catalog_digest"] = "a" * 64
        run.execution_configuration = json.dumps(config)
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="resume_configuration",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert run.status == "waiting_config"
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_resume_rejects_changed_model_pin():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        run.model_configuration = "original"
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        ).pending_decision
        db.commit()
        run.model_configuration = "replacement"
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="resume_configuration",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409


def test_resume_refuses_another_active_run():
    from fastapi import HTTPException

    from src.openship.runs.models import Run
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        db.add(
            Run(
                project_id=project.id, conversation_id=conversation.id, status="running"
            )
        )
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409


def test_configuration_resume_blocks_unavailable_runtime_profile():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.sandboxes.profiles import (
        empty_catalog,
        install_runtime_catalog,
        reset_runtime_catalog,
    )

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="resume_configuration",
        )
        install_runtime_catalog(empty_catalog())
        try:
            with pytest.raises(HTTPException) as error:
                apply_decision(
                    db, principal_id=user.id, run_id=run.id, command=command, now=now
                )
            assert error.value.status_code == 409
            assert run.status == "waiting_config"
        finally:
            reset_runtime_catalog()


def test_ordinary_resume_refuses_new_unsettled_operation():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        _operation(db, run, user, status="interrupted")
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert run.status == "waiting_plan"


def test_resume_rejects_missing_pinned_artifact():
    from fastapi import HTTPException

    from src.openship.artifacts.contracts import ArtifactRef
    from src.openship.runs.tasks.contracts import ExecutionConfigV5, TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        config = ExecutionConfigV5.model_validate_json(run.execution_configuration)
        ref = ArtifactRef(
            id=uuid.uuid4(), sha256="a" * 64, size_bytes=1, media_type="text/plain"
        )
        run.execution_configuration = config.model_copy(
            update={"selected_artifacts": (ref,)}
        ).model_dump_json()
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert run.status == "waiting_plan"


def test_confirm_cannot_consume_recovery_request():
    from fastapi import HTTPException

    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import (
        TaskDecisionCommand,
        TaskRecoveryReport,
    )
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.commit()
        now = datetime.now(UTC)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        request = park_task_wait(
            db, claim, kind="recovery", prompt="Next?", now=now, recovery_report=report
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="abort",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=command.model_copy(update={"action": "confirm"}),
                now=now,
            )
        assert error.value.status_code == 409
        result = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert result.status == "cancelled" and run.status == "cancelled"
        assert operation.status == "interrupted"
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 0
        )


@pytest.mark.parametrize("action", ["repeat_step", "continue_next"])
def test_recovery_choice_records_one_bound_intent_without_dispatch(action, monkeypatch):
    from src.openship.execution.gateway import ToolGateway
    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import (
        TaskDecisionCommand,
        TaskRecoveryReport,
    )
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.runs.tasks.models import TaskDecisionCommandRecord

    def no_dispatch(*args, **kwargs):
        pytest.fail("recovery decision dispatched a tool")

    monkeypatch.setattr(ToolGateway, "execute_tool", no_dispatch)
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        from src.openship.runs.tasks.contracts import ExecutionConfigV5

        execution = ExecutionConfigV5.model_validate_json(run.execution_configuration)
        run.execution_configuration = execution.model_copy(
            update={"allowed_capabilities": (_plan().steps[0].capability,)}
        ).model_dump_json()
        db.flush()
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.commit()
        now = datetime.now(UTC)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="No dispatch recorded",
            eligible_actions=(action,),
        )
        request = park_task_wait(
            db, claim, kind="recovery", prompt="Next?", now=now, recovery_report=report
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action=action,
        )
        before = (
            task.counters_json,
            task.verified_outputs_json,
            task.verified_steps_json,
        )
        first = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        db.commit()
        assert (
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
            == first
        )
        stored = db.query(TaskDecisionCommandRecord).filter_by(run_id=run.id).one()
        assert stored.action == action
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )
        assert operation.status == "admitted"
        assert (
            task.counters_json,
            task.verified_outputs_json,
            task.verified_steps_json,
        ) == before


@pytest.mark.parametrize(
    "change",
    [
        "dispatched",
        "operation_changed",
        "operation_identity",
        "attempt_binding",
        "not_offered",
        "blocker",
        "budget",
        "repair_limit",
        "repair_counter",
        "model_turns",
        "generation_attempts",
        "output_tokens",
        "task_model_limit",
    ],
)
def test_recovery_choice_refuses_stale_evidence_or_controls(change):
    from fastapi import HTTPException

    from src.openship.execution.models import ToolAttempt
    from src.openship.runs.tasks.contracts import (
        ExecutionConfigV5,
        TaskDecisionCommand,
        TaskRecoveryReport,
    )
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.runs.tasks.models import TaskStepAttempt

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        config = ExecutionConfigV5.model_validate_json(run.execution_configuration)
        if change == "task_model_limit":
            config = config.model_copy(
                update={
                    "task_limits": config.task_limits.model_copy(
                        update={"max_model_turns": 1}
                    )
                }
            )
        if change == "repair_limit":
            config = config.model_copy(
                update={
                    "task_limits": config.task_limits.model_copy(
                        update={"max_repairs_per_step": 0}
                    )
                }
            )
        run.execution_configuration = config.model_copy(
            update={"allowed_capabilities": (_plan().steps[0].capability,)}
        ).model_dump_json()
        db.flush()
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="admitted", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.commit()
        now = datetime.now(UTC)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="No dispatch recorded",
            # Task 15: ``blockers`` explains per-action refusals; the offer is
            # authoritative, so the blocked case removes the action from it
            # instead of vetting the whole report.
            eligible_actions=(
                ()
                if change == "not_offered"
                else ("abort",)
                if change == "blocker"
                else ("repeat_step",)
            ),
            blockers=("the step repair ceiling is exhausted",)
            if change == "blocker"
            else (),
        )
        request = park_task_wait(
            db, claim, kind="recovery", prompt="Next?", now=now, recovery_report=report
        ).pending_decision
        db.commit()
        if change == "dispatched":
            db.add(
                ToolAttempt(
                    operation_id=operation.id,
                    ordinal=1,
                    fence=claim.fence,
                    dispatch_started_at=now,
                    outcome="started",
                )
            )
        elif change == "operation_changed":
            operation.status = "interrupted"
        elif change == "operation_identity":
            operation.request_digest = "a" * 64
        elif change == "attempt_binding":
            db.add(
                TaskStepAttempt(
                    task_id=task.id,
                    run_id=run.id,
                    plan_revision=1,
                    step_id="other",
                    ordinal=1,
                    status="interrupted",
                    operation_id=operation.id,
                )
            )
        elif change == "budget":
            task.counters_json = json.dumps(
                {"tool_calls": config.loop_limits.max_tool_calls}
            )
        elif change == "repair_counter":
            task.counters_json = json.dumps({"repairs_by_step": {"inspect": 2}})
        elif change == "model_turns":
            task.counters_json = json.dumps(
                {"model_turns": config.loop_limits.max_model_turns}
            )
        elif change == "generation_attempts":
            task.counters_json = json.dumps(
                {"generation_attempts": config.loop_limits.max_generation_attempts}
            )
        elif change == "output_tokens":
            task.counters_json = json.dumps(
                {"output_tokens": config.loop_limits.max_total_output_tokens}
            )
        elif change == "task_model_limit":
            task.counters_json = json.dumps({"model_turns": 1})
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="repeat_step",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )
        assert run.status == "waiting_input"


def test_legacy_parked_request_requires_actionable_pin_blocker():
    from fastapi import HTTPException

    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        ).pending_decision
        db.commit()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        payload = json.loads(row.request_json)
        del payload["execution_digest"]
        row.request_json = json.dumps(payload)
        db.flush()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="resume_configuration",
        )
        with pytest.raises(HTTPException) as error:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert error.value.status_code == 409
        assert error.value.detail["error"]["code"] == "decision_pins_unverifiable"
        assert "new" in error.value.detail["error"]["message"].lower()
        assert row.state == "open" and run.status == "waiting_config"


def test_legacy_park_replay_preserves_request_without_repinning():
    from src.openship.runs.tasks.decisions import park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        first = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        )
        db.commit()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        row.request_json = json.dumps({"prompt": "Repair?"})
        db.commit()
        replay = park_task_wait(
            db, claim, kind="configuration", prompt="Repair?", now=now
        )
        assert replay.pending_decision == first.pending_decision
        assert json.loads(row.request_json) == {"prompt": "Repair?"}


def test_crash_before_choice_commit_reprompts_same_request():
    from src.openship.runs.models import Outbox
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        parked = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        )
        db.commit()
        request = parked.pending_decision
        before = db.query(Outbox).filter_by(run_id=run.id).count()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )
        db.rollback()
        decision = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert decision.state == "open"
        assert decision.request_id == request.request_id
        assert db.query(Outbox).filter_by(run_id=run.id).count() == before


def test_park_preserves_completed_work_and_releases_claim(monkeypatch):
    from src.openship.execution.gateway import ToolGateway
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import record_attempt_result

    provider_calls = []
    monkeypatch.setattr(
        ToolGateway, "execute_tool", lambda *a, **kw: provider_calls.append((a, kw))
    )
    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user)
        before = record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()
        initial_events = db.query(Event).filter_by(run_id=run.id).count()
        result = park_task_wait(
            db, claim, kind="clarification", prompt="Details?", now=datetime.now(UTC)
        )
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        assert result.attempts == before.attempts
        assert result.verified_outputs == before.verified_outputs
        assert result.counters == before.counters
        assert run.status == "waiting_input" and run.ended_at is None
        assert job.owner_id is None and job.lease_until is None
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events + 1
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id, state="open").count()
            == 1
        )
        db.rollback()
        assert run.status == "running" and job.owner_id == claim.worker_id
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events
        assert provider_calls == []


def test_preplan_wait_binds_pinned_context_digest():
    from src.openship.capabilities.contracts import canonical_digest
    from src.openship.runs.tasks.decisions import park_task_wait
    from tests.test_task_profile_selection import _all_profiles, _catalog, _execution

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        execution = _execution(_catalog(_all_profiles()))
        context = execution.task_context.model_copy(
            update={
                "root_run_id": run.id,
                "project_id": project.id,
                "conversation_id": conversation.id,
                "initiator_id": user.id,
            }
        )
        run.execution_configuration = execution.model_copy(
            update={"task_context": context}
        ).model_dump_json()
        db.commit()
        progress = park_task_wait(
            db, claim, kind="clarification", prompt="Details?", now=datetime.now(UTC)
        )
        assert progress.current_revision == 0
        assert progress.pending_decision.expected_digest == canonical_digest(
            context.model_dump(mode="json")
        )


def test_park_replay_keeps_request_identity():
    from src.openship.runs.tasks.decisions import expire_task_decisions, park_task_wait

    with TestSessionLocal() as db:
        _, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        now = datetime.now(UTC)
        first = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        )
        db.commit()
        second = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        )
        assert first.pending_decision == second.pending_decision
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 1
        expire_task_decisions(db, now=now + timedelta(days=2))
        db.commit()


def test_expiry_cancels_recovery_wait_without_dispatch():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import expire_task_decisions, park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="dispatching", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.commit()
        now = datetime.now(UTC)
        with pytest.raises(TaskStateError):
            park_task_wait(db, claim, kind="clarification", prompt="Details?", now=now)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Outcome unknown",
        )
        result = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="What next?",
            now=now,
            recovery_report=report,
        )
        assert result.pending_decision.recovery_report == report
        assert result.last_disposition == "uncertain" and run.ended_at is None
        assert operation.status == "dispatching"
        assert expire_task_decisions(db, now=now + timedelta(hours=24, seconds=1)) == 1
        assert operation.status == "dispatching" and run.status == "timed_out"


def test_parking_rejects_expired_lease_and_mismatched_recovery():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="running", result_digest=None)
        db.commit()
        now = datetime.now(UTC)
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest="a" * 64,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Outcome unknown",
        )
        with pytest.raises(TaskStateError, match="recovery report"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=now,
                recovery_report=report,
            )
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        job.lease_until = now - timedelta(seconds=1)
        db.flush()
        with pytest.raises(TaskStateError, match="lease"):
            park_task_wait(db, claim, kind="configuration", prompt="Next?", now=now)
        assert run.status == "running" and job.owner_id == claim.worker_id
        assert db.query(TaskDecisionRecord).filter_by(run_id=run.id).count() == 0


def test_ordinary_wait_rejects_interrupted_dispatched_operation():
    from src.openship.execution.models import ToolAttempt
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        db.add(
            ToolAttempt(
                operation_id=operation.id,
                ordinal=1,
                fence=claim.fence,
                dispatch_started_at=datetime.now(UTC),
                outcome="interrupted",
            )
        )
        db.commit()
        with pytest.raises(TaskStateError, match="unsettled"):
            park_task_wait(
                db,
                claim,
                kind="clarification",
                prompt="Details?",
                now=datetime.now(UTC),
            )


def test_ordinary_wait_rejects_interrupted_without_tool_attempt():
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        _operation(db, run, user, status="interrupted")
        db.commit()
        with pytest.raises(TaskStateError, match="unsettled"):
            park_task_wait(
                db,
                claim,
                kind="clarification",
                prompt="Details?",
                now=datetime.now(UTC),
            )


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {
            "plan_revision": 1,
            "step_id": "inspect",
            "attempt": 2,
            "logical_call_id": "model:1:call:0",
        },
    ],
)
def test_recovery_rejects_missing_or_conflicting_operation_binding(binding):
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="running", result_digest=None)
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps([binding] if binding else [])
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        with pytest.raises(TaskStateError, match="identity"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=datetime.now(UTC),
                recovery_report=report,
            )


def test_recovery_accepts_matching_existing_attempt():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.models import TaskStepAttempt

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        db.add(
            TaskStepAttempt(
                task_id=task.id,
                run_id=run.id,
                plan_revision=1,
                step_id="inspect",
                ordinal=1,
                status="interrupted",
                operation_id=operation.id,
            )
        )
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        result = park_task_wait(
            db,
            claim,
            kind="recovery",
            prompt="Next?",
            now=datetime.now(UTC),
            recovery_report=report,
        )
        assert result.pending_decision.recovery_report == report


def test_recovery_rejects_conflicting_attempt_even_with_matching_binding():
    from src.openship.runs.tasks.contracts import TaskRecoveryReport
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.models import TaskStepAttempt
    from src.openship.runs.tasks.state import TaskStateError

    with TestSessionLocal() as db:
        user, _, _, run, claim = _identity(db)
        plan = _plan()
        save_plan(db, claim, plan, expected_revision=0)
        operation = _operation(db, run, user, status="interrupted")
        operation.logical_call_id = "model:1:call:0"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.call_bindings_json = json.dumps(
            [
                {
                    "plan_revision": 1,
                    "step_id": "inspect",
                    "attempt": 1,
                    "logical_call_id": operation.logical_call_id,
                }
            ]
        )
        db.add(
            TaskStepAttempt(
                task_id=task.id,
                run_id=run.id,
                plan_revision=1,
                step_id="inspect",
                ordinal=2,
                status="interrupted",
                operation_id=operation.id,
            )
        )
        db.commit()
        report = TaskRecoveryReport(
            plan_revision=1,
            plan_digest=plan.digest,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
            explanation="Unknown",
        )
        with pytest.raises(TaskStateError, match="identity"):
            park_task_wait(
                db,
                claim,
                kind="recovery",
                prompt="Next?",
                now=datetime.now(UTC),
                recovery_report=report,
            )


def test_preplan_clarification_contract_and_storage_use_revision_zero():
    from src.openship.runs.tasks.contracts import (
        TaskDecision,
        TaskDecisionCommand,
        TaskProgress,
    )

    now = datetime.now(UTC)
    request_id = uuid.uuid4()
    digest = "a" * 64
    decision = TaskDecision(
        request_id=request_id,
        kind="clarification",
        plan_revision=0,
        expected_digest=digest,
        expires_at=now,
    )
    assert TaskProgress(current_revision=0, status="waiting", pending_decision=decision)
    assert (
        TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request_id,
            plan_revision=0,
            expected_digest=digest,
            action="answer",
            answer="details",
        ).plan_revision
        == 0
    )
    with pytest.raises(ValidationError):
        TaskDecision(
            request_id=request_id,
            kind="plan_confirmation",
            plan_revision=0,
            expected_digest=digest,
            expires_at=now,
        )

    db = TestSessionLocal()
    try:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.current_revision = 0
        db.add(
            TaskDecisionRecord(
                task_id=task.id,
                run_id=run.id,
                request_id=request_id,
                kind="clarification",
                plan_revision=0,
                expected_digest=digest,
                actor_id=user.id,
                expires_at=now,
            )
        )
        db.flush()
    finally:
        db.rollback()
        db.close()


def test_expiry_is_bounded_and_rolls_back_with_caller_transaction():
    from src.openship.runs.tasks.decisions import expire_task_decisions

    db = TestSessionLocal()
    try:
        now = datetime.now(UTC)
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        run.status = "waiting_plan"
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        job.owner_id = job.lease_until = None
        task.status = "waiting"
        decision = TaskDecisionRecord(
            task_id=task.id,
            run_id=run.id,
            request_id=uuid.uuid4(),
            kind="plan_confirmation",
            plan_revision=task.current_revision,
            expected_digest=task.digest,
            state="open",
            actor_id=user.id,
            expires_at=now - timedelta(seconds=1),
        )
        db.add(decision)
        db.commit()
        initial_events = db.query(Event).filter_by(run_id=run.id).count()

        assert expire_task_decisions(db, now=now, batch_size=1) == 1
        assert run.status == "timed_out" and run.error_code == "decision_expired"
        assert decision.state == "expired" and task.status == "blocked"
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events + 1
        db.rollback()
        assert run.status == "waiting_plan" and decision.state == "open"
        assert db.query(Event).filter_by(run_id=run.id).count() == initial_events
        assert expire_task_decisions(db, now=now, batch_size=1) == 1
        db.commit()
        assert expire_task_decisions(db, now=now, batch_size=1) == 0
    finally:
        db.close()


def test_expiry_does_not_override_cancellation_or_live_lease():
    from src.openship.runs.tasks.decisions import expire_task_decisions

    db = TestSessionLocal()
    try:
        now = datetime.now(UTC)
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        task.status = "waiting"
        decision = TaskDecisionRecord(
            task_id=task.id,
            run_id=run.id,
            request_id=uuid.uuid4(),
            kind="plan_confirmation",
            plan_revision=task.current_revision,
            expected_digest=task.digest,
            state="open",
            actor_id=user.id,
            expires_at=now - timedelta(seconds=1),
        )
        db.add(decision)
        run.status = "cancelled"
        db.flush()
        assert expire_task_decisions(db, now=now) == 0
        run.status = "waiting_plan"
        db.flush()
        assert expire_task_decisions(db, now=now) == 0
        assert decision.state == "open"
    finally:
        db.rollback()
        db.close()
