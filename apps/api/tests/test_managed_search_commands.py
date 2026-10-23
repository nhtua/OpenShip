from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfigV2,
    ExecutionConfigV3,
    ToolCommandRequest,
    decode_execution_config,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.models import Connector, ConnectorRevision
from src.openship.connectors.service import create_connector, set_default
from src.openship.database.session import SessionLocal
from src.openship.events.service import Event
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.service import submit_tool_run, submit_turn
from src.openship.workspace.models import Project
from tests.conftest import configure_profiles, profile_spec

API_KEY = "synthetic-managed-command-api-key"  # rayloc:ignore


def _install_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    encryption_path = tmp_path / "connector-encryption.json"
    hmac_path = tmp_path / "connector-hmac.json"
    for path, key in ((encryption_path, b"e" * 32), (hmac_path, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "command-test-v1",
                    "keys": {"command-test-v1": base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)


def _identity(db, label: str):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"managed-{label}-{user_id.hex[:10]}",
        email=f"managed-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(id=uuid.uuid4(), owner_user_id=user_id, name=f"Managed {label}")
    db.add_all([user, project])
    db.flush()
    conversation = Conversation(
        id=uuid.uuid4(), user_id=user_id, project_id=project.id, title="Managed search"
    )
    db.add(conversation)
    db.flush()
    return user, project, conversation


def _connector(db, user, monkeypatch, *, kind="brave"):
    monkeypatch.setattr(settings, "connector_operator_user_id", user.id)
    result = create_connector(
        db,
        actor=user,
        visibility="global",
        project_id=None,
        kind=kind,
        name=f"global {kind.title()}",
        configuration_raw={"schema_version": 1, "kind": kind},
        api_key=API_KEY,
    )
    connector_id = uuid.UUID(result["id"])
    return connector_id


def _ref(db, connector_id):
    revision = (
        db.query(ConnectorRevision)
        .filter(ConnectorRevision.connector_id == connector_id)
        .order_by(ConnectorRevision.revision.desc())
        .first()
    )
    return {
        "id": revision.connector_id,
        "kind": db.get(Connector, connector_id).kind,
        "configuration_revision": revision.revision,
        "visibility": revision.visibility,
        "credential_version_id": revision.credential_version_id,
    }


def _capability_ref(capability_id="brave_web_search"):
    manifest = next(
        item for item in list_capability_manifests() if item.id == capability_id
    )
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


def test_manual_search_pins_connector_and_exact_retry_ignores_later_rotation(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, project, conversation = _identity(db, "manual")
        connector_id = _connector(db, user, monkeypatch)
        reference = _ref(db, connector_id)
        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="brave",
            expected_revision=0,
            connector_id=connector_id,
        )
        command = ToolCommandRequest(
            client_request_id=uuid.uuid4(),
            capability=_capability_ref(),
            arguments={"query": "managed search", "max_results": 3},
        )

        first = submit_tool_run(db, user.id, conversation.id, command)
        db.commit()
        pinned = decode_execution_config(first.execution_configuration)
        assert isinstance(pinned, ExecutionConfigV2)
        assert first.kind == "tool" and first.graph_version == 2
        assert pinned.connector_refs[0].model_dump(mode="python") == reference
        assert pinned.connector_refs[0].id == connector_id
        assert API_KEY not in first.execution_configuration
        assert API_KEY not in (first.tool_command_json or "")

        changed_default = _connector(db, user, monkeypatch)
        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="brave",
            expected_revision=1,
            connector_id=changed_default,
        )
        db.commit()
        replay = submit_tool_run(db, user.id, conversation.id, command)
        assert replay.id == first.id
        assert (
            decode_execution_config(replay.execution_configuration).connector_refs[0].id
            == connector_id
        )
        assert changed_default != connector_id
        assert API_KEY not in first.execution_configuration
        assert all(
            API_KEY not in event.payload
            for event in db.query(Event).filter(Event.run_id == first.id)
        )
        assert all(
            API_KEY not in row.payload
            for row in db.query(Outbox).filter(Outbox.conversation_id == conversation.id)
        )
    finally:
        db.close()


def test_changed_connector_selection_conflicts_on_manual_request_replay(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, _project, conversation = _identity(db, "conflict")
        first_connector = _connector(db, user, monkeypatch)
        second_connector = _connector(db, user, monkeypatch)
        request_id = uuid.uuid4()
        original = ToolCommandRequest(
            client_request_id=request_id,
            capability=_capability_ref(),
            arguments={"query": "managed search", "max_results": 3},
            connector_selections={"brave": first_connector},
        )
        submit_tool_run(db, user.id, conversation.id, original)
        db.commit()
        changed = original.model_copy(
            update={"connector_selections": {"brave": second_connector}}
        )

        with pytest.raises(HTTPException) as conflict:
            submit_tool_run(db, user.id, conversation.id, changed)

        assert conflict.value.status_code == 409
        assert db.query(Run).filter(Run.conversation_id == conversation.id).count() == 1
    finally:
        db.close()


def test_tool_chat_pins_selected_connector_and_plain_chat_needs_no_connector_keys(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    primary = profile_spec("managed-primary")
    primary["capabilities"]["tool_calls"] = True
    primary["adapter_options"]["adapter"] = "openai"
    configure_profiles(monkeypatch, [primary], "managed-primary")
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "brave_web_search,workspace_list_files"
    )
    db = SessionLocal()
    try:
        user, project, conversation = _identity(db, "chat")
        connector_id = _connector(db, user, monkeypatch)
        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Find a relevant source.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
            connector_selections={"brave": connector_id},
        )
        db.commit()
        execution = decode_execution_config(run.execution_configuration)
        assert isinstance(execution, ExecutionConfigV3)
        assert run.kind == "chat" and run.graph_version == 3
        assert [ref.id for ref in execution.allowed_capabilities] == [
            "brave_web_search",
            "workspace_list_files",
        ]
        assert execution.connector_refs[0].id == connector_id
        assert API_KEY not in run.execution_configuration

        plain_conversation = Conversation(
            id=uuid.uuid4(),
            user_id=user.id,
            project_id=project.id,
            title="Plain chat",
        )
        db.add(plain_conversation)
        db.commit()
        monkeypatch.setattr(settings, "connector_installation_id", None)
        monkeypatch.setattr(
            settings,
            "connector_encryption_key_file",
            tmp_path / "missing-encryption.json",
        )
        plain = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(plain_conversation.id),
            content="Continue with plain text.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=False,
        )
        db.commit()
        assert plain.kind == "chat" and plain.graph_version == 1
    finally:
        db.close()


def test_resumed_chat_keeps_its_admitted_context_after_later_conversation_activity(
    monkeypatch, tmp_path
):
    from datetime import UTC, datetime, timedelta

    from src.openship.capabilities.contracts import decode_execution_config
    from src.openship.chat.models import Message
    from src.openship.connectors.blockers import (
        configuration_digest,
        resume_connector_wait,
    )
    from src.openship.connectors.readiness import ConnectorReadinessFailure
    from src.openship.resources.models import ConnectorBlocker
    from src.openship.runs import dispatch as dispatch_module
    from src.openship.runs import worker as worker_module
    from src.openship.runs.queue import claim_next
    from src.openship.runs.worker import Worker

    _install_keys(monkeypatch, tmp_path)
    primary = profile_spec("managed-resume-context")
    primary["capabilities"]["tool_calls"] = True
    primary["adapter_options"]["adapter"] = "openai"
    configure_profiles(monkeypatch, [primary], "managed-resume-context")
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    worker = None
    try:
        # This SQLite test database is shared across tests. Park older queued
        # jobs so claim_next deterministically returns the run admitted below.
        stale_job_ids = [
            job.run_id
            for job in db.query(RunJob)
            .join(Run, Run.id == RunJob.run_id)
            .filter(Run.status == "queued")
            .all()
        ]
        if stale_job_ids:
            db.query(RunJob).filter(RunJob.run_id.in_(stale_job_ids)).update(
                {
                    RunJob.owner_id: "prior-test-run",
                    RunJob.lease_until: datetime.now(UTC) + timedelta(minutes=5),
                },
                synchronize_session=False,
            )
            db.commit()
        user, _project, conversation = _identity(db, "resume-context")
        connector_id = _connector(db, user, monkeypatch)
        earlier_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="Earlier admitted context",
        )
        db.add(earlier_message)
        db.commit()

        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Find a relevant source.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
            connector_selections={"brave": connector_id},
        )
        db.commit()
        execution = decode_execution_config(run.execution_configuration)
        current_message = (
            db.query(Message)
            .filter(Message.run_id == run.id, Message.role == "user")
            .one()
        )
        assert execution.admitted_context_message_ids == (
            earlier_message.id,
            current_message.id,
        )

        claim = claim_next(db, worker_id="resume-context-worker", lease_seconds=30)
        assert claim is not None and claim.run_id == run.id
        monkeypatch.setattr(
            worker_module,
            "preflight_connector_references",
            lambda **_kwargs: ConnectorReadinessFailure(
                execution.connector_refs[0], "connector_unavailable"
            ),
        )
        worker = Worker(
            worker_id=claim.worker_id,
            session_factory=SessionLocal,
            checkpointer=None,
        )
        assert worker.process_job(claim)

        db.expire_all()
        blocker = (
            db.query(ConnectorBlocker)
            .filter(ConnectorBlocker.run_id == run.id)
            .one()
        )
        later_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="Newer conversation activity",
        )
        conversation.title = "Title from newer activity"
        db.add(later_message)
        db.commit()

        monkeypatch.setattr(
            worker_module, "preflight_connector_references", lambda **_kwargs: None
        )
        resumed = resume_connector_wait(
            db,
            user_id=user.id,
            run_id=run.id,
            blocker_id=blocker.id,
            request_id=blocker.request_id,
            expected_configuration_digest=configuration_digest(execution),
            session_factory=SessionLocal,
        )
        assert resumed.status == "queued"
        claim = claim_next(db, worker_id="resume-context-worker", lease_seconds=30)
        assert claim is not None and claim.run_id == run.id

        captured_messages = []

        class FakeAgentGraph:
            def invoke(self, state, config):
                captured_messages.extend(state["messages"])
                return {
                    "assistant_response": "Answer from the admitted context.",
                    "usage": None,
                    "finish_reason": "stop",
                }

        monkeypatch.setitem(
            dispatch_module._GRAPH_FACTORIES,
            ("chat", 3),
            lambda _checkpointer, **_kwargs: FakeAgentGraph(),
        )
        assert worker.process_job(claim)

        user_context = [
            message["content"]
            for message in captured_messages
            if message["role"] == "user"
        ]
        assert user_context == [
            "Earlier admitted context",
            "Find a relevant source.",
        ]
        db.expire_all()
        assert db.get(Conversation, conversation.id).title == "Title from newer activity"
    finally:
        if worker is not None:
            worker.engine.dispose()
        db.close()


def test_automatic_tool_chat_prefers_one_project_default_and_pins_fallback_provider(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    primary = profile_spec("managed-default-first-primary")
    primary["capabilities"]["tool_calls"] = True
    primary["adapter_options"]["adapter"] = "openai"
    configure_profiles(monkeypatch, [primary], "managed-default-first-primary")
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "brave_web_search,tavily_web_search"
    )
    db = SessionLocal()
    try:
        user, project, conversation = _identity(db, "default-first")
        brave_id = _connector(db, user, monkeypatch, kind="brave")
        tavily_id = _connector(db, user, monkeypatch, kind="tavily")
        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="tavily",
            expected_revision=0,
            connector_id=tavily_id,
        )

        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Search for a relevant source.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
        )
        execution = decode_execution_config(run.execution_configuration)

        assert isinstance(execution, ExecutionConfigV3)
        assert execution.preferred_search_kind == "tavily"
        assert {ref.kind for ref in execution.connector_refs} == {"brave", "tavily"}
        assert {ref.id for ref in execution.connector_refs} == {brave_id, tavily_id}
        assert [ref.id for ref in execution.allowed_capabilities[:2]] == [
            "tavily_web_search",
            "brave_web_search",
        ]

        run.status = "completed"
        db.flush()
        explicitly_selected = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Use the explicitly selected Brave connector.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
            connector_selections={"brave": brave_id},
        )
        explicit_execution = decode_execution_config(
            explicitly_selected.execution_configuration
        )
        assert isinstance(explicit_execution, ExecutionConfigV3)
        assert explicit_execution.preferred_search_kind == "brave"
    finally:
        db.close()


def test_tool_chat_exact_retry_keeps_pinned_connector_after_default_changes(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    primary = profile_spec("managed-replay-primary")
    primary["capabilities"]["tool_calls"] = True
    primary["adapter_options"]["adapter"] = "openai"
    configure_profiles(monkeypatch, [primary], "managed-replay-primary")
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        user, project, conversation = _identity(db, "chat-replay")
        first_connector = _connector(db, user, monkeypatch)
        second_connector = _connector(db, user, monkeypatch)
        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="brave",
            expected_revision=0,
            connector_id=first_connector,
        )
        request_id = str(uuid.uuid4())
        request = {
            "db": db,
            "user_id": user.id,
            "conversation_id": str(conversation.id),
            "content": "Search using the project default.",
            "client_request_id": request_id,
            "enable_tools": True,
        }
        first = submit_turn(**request)
        db.commit()
        first_config = decode_execution_config(first.execution_configuration)
        assert isinstance(first_config, ExecutionConfigV3)
        assert first_config.connector_refs[0].id == first_connector

        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="brave",
            expected_revision=1,
            connector_id=second_connector,
        )
        db.commit()
        replay = submit_turn(**request)

        assert replay.id == first.id
        replay_config = decode_execution_config(replay.execution_configuration)
        assert isinstance(replay_config, ExecutionConfigV3)
        assert replay_config.connector_refs[0].id == first_connector
        assert second_connector != first_connector
        durable_values = " ".join(
            str(getattr(first, column.name))
            for column in Run.__table__.columns
            if getattr(first, column.name) is not None
        )
        durable_values += " " + " ".join(
            event.payload for event in db.query(Event).filter(Event.run_id == first.id)
        )
        durable_values += " " + " ".join(
            row.payload
            for row in db.query(Outbox).filter(Outbox.conversation_id == conversation.id)
        )
        assert API_KEY not in durable_values
    finally:
        db.close()


def test_tool_command_defaults_keep_the_existing_v1_input_shape():
    command = ToolCommandRequest(
        client_request_id=uuid.uuid4(),
        capability=_capability_ref(),
        arguments={"query": "legacy request", "max_results": 2},
    )
    assert command.connector_selections == {}
