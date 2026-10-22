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
    ToolCommandRequest,
    decode_execution_config,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.models import ConnectorRevision
from src.openship.connectors.service import create_connector, set_default
from src.openship.database.session import SessionLocal
from src.openship.events.service import Event
from src.openship.runs.models import Outbox, Run
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


def _connector(db, user, project, *, visibility="project"):
    result = create_connector(
        db,
        actor=user,
        visibility=visibility,
        project_id=project.id if visibility == "project" else None,
        kind="brave",
        name=f"{visibility} Brave",
        configuration_raw={"schema_version": 1, "kind": "brave"},
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
        "kind": "brave",
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
        connector_id = _connector(db, user, project)
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

        changed_default = _connector(db, user, project)
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
        user, project, conversation = _identity(db, "conflict")
        first_connector = _connector(db, user, project)
        second_connector = _connector(db, user, project)
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
        connector_id = _connector(db, user, project)
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
        assert isinstance(execution, ExecutionConfigV2)
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
        first_connector = _connector(db, user, project)
        second_connector = _connector(db, user, project)
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
        assert isinstance(first_config, ExecutionConfigV2)
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
        assert isinstance(replay_config, ExecutionConfigV2)
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
