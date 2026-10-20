"""Durable model-free tool command and admission behavior."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    ToolCommandRequest,
)
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.admission import (
    acquire_execution_slot,
    admit_operation,
    release_execution_slot,
)
from src.openship.execution.models import ExecutionSlot, ToolOperation
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.routes import router, submit_tool_command
from src.openship.runs.service import submit_tool_run
from src.openship.workspace.models import Project


def _principals(db, *, count=1):
    rows = []
    for index in range(count):
        user = User(
            username=f"tool_user_{uuid.uuid4().hex[:12]}",
            email=f"tool_{uuid.uuid4().hex}@example.com",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name=f"Project {index}")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title="Tool test",
        )
        db.add(conversation)
        db.flush()
        rows.append((user, project, conversation))
    db.commit()
    return rows


def _command(client_request_id=None, *, capability=None, arguments=None):
    manifest = list_capability_manifests()[0]
    return ToolCommandRequest(
        client_request_id=client_request_id or uuid.uuid4(),
        capability=capability
        or CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        ),
        arguments=arguments or {"query": "terraform modules", "max_results": 3},
    )


def test_manual_tool_run_has_no_model_or_chat_message(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "workflow_run_timeout_seconds", 900)
    db = SessionLocal()
    try:
        user, _project, conversation = _principals(db)[0]
        command = _command()

        run = submit_tool_run(db, user.id, conversation.id, command)
        db.commit()

        assert run.kind == "tool"
        assert run.graph_version == 1
        assert run.model_configuration is None
        assert run.execution_configuration
        assert ExecutionConfig.model_validate_json(
            run.execution_configuration
        ).loop_limits.deadline_seconds == 900
        assert '"query":"terraform modules"' in run.tool_command_json
        assert db.query(Message).filter(Message.run_id == run.id).count() == 0
        assert db.query(RunJob).filter(RunJob.run_id == run.id).count() == 1
        assert db.query(Event).filter(Event.run_id == run.id, Event.type == "tool.submitted").count() == 1
        assert db.query(Outbox).filter(
            Outbox.conversation_id == conversation.id,
            Outbox.type == "tool.submitted",
        ).count() == 1
    finally:
        db.close()


def test_exact_tool_command_replay_precedes_current_enablement(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, _, conversation = _principals(db)[0]
        command = _command()
        first = submit_tool_run(db, user.id, conversation.id, command)
        db.commit()

        monkeypatch.setattr(settings, "tool_enabled_ids", "")
        replay = submit_tool_run(db, user.id, str(conversation.id), command)

        assert replay.id == first.id
        assert db.query(Run).filter(Run.client_request_id == str(command.client_request_id)).count() == 1
    finally:
        db.close()


def test_changed_or_foreign_command_key_is_not_replayed(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        (owner, project, conversation), (foreign_user, _, foreign_conversation) = _principals(db, count=2)
        other_conversation = Conversation(user_id=owner.id, project_id=project.id, title="Owned by same user")
        db.add(other_conversation)
        db.commit()
        key = uuid.uuid4()
        original = _command(key)
        submit_tool_run(db, owner.id, conversation.id, original)
        db.commit()

        changed = _command(key, arguments={"query": "different query", "max_results": 3})
        with pytest.raises(HTTPException) as changed_error:
            submit_tool_run(db, owner.id, conversation.id, changed)
        assert changed_error.value.status_code == 409

        with pytest.raises(HTTPException) as foreign_error:
            submit_tool_run(db, owner.id, other_conversation.id, original)
        assert foreign_error.value.status_code == 409

        with pytest.raises(HTTPException) as ownership_error:
            submit_tool_run(db, foreign_user.id, foreign_conversation.id, original)
        assert ownership_error.value.status_code == 404
    finally:
        db.close()


def test_only_one_active_manual_run_is_admitted(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, _, conversation = _principals(db)[0]
        submit_tool_run(db, user.id, conversation.id, _command())
        db.commit()

        with pytest.raises(HTTPException) as error:
            submit_tool_run(db, user.id, conversation.id, _command())
        assert error.value.status_code == 409
        assert error.value.detail["error"]["code"] == "run_already_active"
    finally:
        db.close()


def test_legacy_chat_run_defaults_remain_readable():
    db = SessionLocal()
    try:
        _user, project, conversation = _principals(db)[0]
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="completed",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        assert run.kind == "chat"
        assert run.graph_version == 1
        assert run.execution_configuration is None
        assert run.model_configuration is None
    finally:
        db.close()


def _running_tool_run(db, user, project, conversation, *, fence=1, worker_id="worker-a"):
    command = _command()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind="tool",
        graph_version=1,
        fence=fence,
        execution_configuration=ExecutionConfig(
            graph_kind="tool",
            graph_version=1,
            allowed_capabilities=(command.capability,),
        ).model_dump_json(),
    )
    db.add(run)
    db.flush()
    job = RunJob(
        run_id=run.id,
        fence=fence,
        owner_id=worker_id,
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db.add(job)
    db.flush()
    claim = Claim(
        run_id=run.id,
        fence=fence,
        lease_until=job.lease_until,
        worker_id=worker_id,
        user_id=user.id,
    )
    return run, claim


def test_tool_operation_replay_is_exact_and_requires_live_fence(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, project, conversation = _principals(db)[0]
        run, claim = _running_tool_run(db, user, project, conversation)
        manifest = list_capability_manifests()[0]
        ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        context = AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        )
        arguments = {"query": "terraform modules", "max_results": 3}
        first = admit_operation(db, claim, context, "manual:0", ref, arguments, ())
        replay = admit_operation(db, claim, context, "manual:0", ref, arguments, ())

        assert replay.id == first.id
        assert db.query(ToolOperation).filter(ToolOperation.run_id == run.id).count() == 1
        changed = {"query": "different", "max_results": 3}
        with pytest.raises(HTTPException) as conflict:
            admit_operation(db, claim, context, "manual:0", ref, changed, ())
        assert conflict.value.status_code == 409
    finally:
        db.close()


def test_tool_command_accepts_json_uuid_and_preserves_historical_chat_contract():
    manifest = list_capability_manifests()[0]
    request_id = uuid.uuid4()
    parsed = ToolCommandRequest.model_validate(
        {
            "client_request_id": str(request_id),
            "capability": {
                "id": manifest.id,
                "version": manifest.version,
                "manifest_digest": manifest.manifest_digest,
            },
            "arguments": {"query": "persisted command", "max_results": 2},
        }
    )
    assert parsed.client_request_id == request_id


def test_tool_submission_route_is_authenticated_and_model_free(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, _, conversation = _principals(db)[0]
        tool_route = next(route for route in router.routes if route.path == "/api/runs/{conversation_id}/tools")
        assert require_jwt in [dependency.call for dependency in tool_route.dependant.dependencies]

        response = asyncio.run(
            submit_tool_command(str(conversation.id), _command(), db, user)
        )

        assert response.status == "queued"
        assert response.model is None
    finally:
        db.close()


def test_execution_slot_is_shared_and_fence_checked(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "tool_concurrency", 1, raising=False)
    db = SessionLocal()
    try:
        (user_a, project_a, conversation_a), (user_b, project_b, conversation_b) = _principals(db, count=2)
        run_a, claim_a = _running_tool_run(db, user_a, project_a, conversation_a)
        run_b, claim_b = _running_tool_run(db, user_b, project_b, conversation_b, worker_id="worker-b")
        manifest = list_capability_manifests()[0]
        ref = CapabilityRef(id=manifest.id, version=manifest.version, manifest_digest=manifest.manifest_digest)

        def operation(run, claim, user, project, conversation):
            context = AuthorizationContext(
                principal_id=user.id,
                project_id=project.id,
                conversation_id=conversation.id,
                action="execute_tool",
                target_id=str(run.id),
            )
            return admit_operation(
                db, claim, context, "manual:0", ref,
                {"query": "terraform modules", "max_results": 3}, (),
            )

        operation_a = operation(run_a, claim_a, user_a, project_a, conversation_a)
        operation_b = operation(run_b, claim_b, user_b, project_b, conversation_b)
        slot = acquire_execution_slot(db, claim_a, operation_a.id)
        assert slot is not None
        assert acquire_execution_slot(db, claim_b, operation_b.id) is None
        assert not release_execution_slot(db, claim_b, slot.id)
        assert release_execution_slot(db, claim_a, slot.id)
        assert db.query(ExecutionSlot).filter(ExecutionSlot.operation_id.isnot(None)).count() == 0
    finally:
        db.close()


def test_expired_execution_claim_cannot_release_or_acquire_a_slot(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, project, conversation = _principals(db)[0]
        run, claim = _running_tool_run(db, user, project, conversation)
        manifest = list_capability_manifests()[0]
        ref = CapabilityRef(id=manifest.id, version=manifest.version, manifest_digest=manifest.manifest_digest)
        context = AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        )
        operation = admit_operation(
            db, claim, context, "manual:0", ref,
            {"query": "terraform modules", "max_results": 3}, (),
        )
        slot = acquire_execution_slot(db, claim, operation.id)
        assert slot is not None

        job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        db.flush()

        assert not release_execution_slot(db, claim, slot.id)
        with pytest.raises(HTTPException) as error:
            acquire_execution_slot(db, claim, operation.id)
        assert error.value.detail["error"]["code"] == "lease_lost"
    finally:
        db.close()
