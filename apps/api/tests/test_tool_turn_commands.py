"""Admission pins exact tool/artifact authority for tool-enabled chat turns."""

import json
import uuid

import pytest
from fastapi import HTTPException

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import ExecutionConfig
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Run
from src.openship.runs.schemas import TurnSubmitRequest
from src.openship.runs.service import submit_turn
from src.openship.workspace.models import Project
from tests.conftest import configure_profiles, profile_spec


def _tool_profiles(monkeypatch, *, fallback_tools=True, adapter="openai"):
    primary = profile_spec("primary-fixture", allowed_fallbacks=["secondary-fixture"])
    fallback = profile_spec("secondary-fixture")
    for spec, supports_tools in ((primary, True), (fallback, fallback_tools)):
        spec["adapter"] = adapter
        spec["adapter_options"]["adapter"] = adapter
        spec["capabilities"]["tool_calls"] = supports_tools
    configure_profiles(monkeypatch, [primary, fallback], "primary-fixture")
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search,tavily_web_search")


def _owned_conversation(db):
    user = User(
        username=f"tool_chat_{uuid.uuid4().hex[:12]}",
        email=f"tool_chat_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Tool chat project")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Tool chat")
    db.add(conversation)
    db.commit()
    return user, project, conversation


def test_turn_request_carries_only_tool_enablement_and_selected_artifact_ids():
    artifact_id = uuid.uuid4()
    request = TurnSubmitRequest(
        content="Look through these notes and search if needed.",
        client_request_id=str(uuid.uuid4()),
        enable_tools=True,
        artifact_ids=[artifact_id],
    )

    assert request.enable_tools is True
    assert request.artifact_ids == [artifact_id]
    assert not hasattr(request, "credential_ref")
    assert not hasattr(request, "provider_endpoint")


def test_tool_chat_pins_exact_enabled_refs_and_ready_artifacts(monkeypatch):
    _tool_profiles(monkeypatch)
    db = SessionLocal()
    try:
        user, project, conversation = _owned_conversation(db)
        artifact = Artifact(
            id=uuid.uuid4(),
            project_id=project.id,
            conversation_id=conversation.id,
            kind="upload",
            media_type="text/plain",
            display_name="notes.txt",
            size_bytes=12,
            sha256="c" * 64,
            relative_storage_key=f"artifacts/{uuid.uuid4()}/content",
            status="ready",
            mutability="immutable",
            source="upload",
        )
        db.add(artifact)
        db.commit()

        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Find the relevant documentation.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
            artifact_ids=[artifact.id],
        )
        db.commit()
        config = ExecutionConfig.model_validate_json(run.execution_configuration)
        expected = {manifest.id: manifest for manifest in list_capability_manifests()}

        assert run.kind == "chat"
        assert run.graph_version == 2
        assert [ref.id for ref in config.allowed_capabilities] == [
            "brave_web_search", "tavily_web_search"
        ]
        assert all(ref.manifest_digest == expected[ref.id].manifest_digest for ref in config.allowed_capabilities)
        assert config.selected_artifacts[0].id == artifact.id
        assert config.selected_artifacts[0].sha256 == artifact.sha256
        assert run.tool_command_json is None
        assert "credential_ref" not in json.dumps(config.model_dump(mode="json"))
    finally:
        db.close()


def test_changed_tool_mode_or_selected_artifact_conflicts_on_request_replay(monkeypatch):
    _tool_profiles(monkeypatch)
    db = SessionLocal()
    try:
        user, project, conversation = _owned_conversation(db)
        artifact = Artifact(
            id=uuid.uuid4(),
            project_id=project.id,
            conversation_id=conversation.id,
            kind="upload",
            media_type="text/plain",
            display_name="notes.txt",
            size_bytes=12,
            sha256="d" * 64,
            relative_storage_key=f"artifacts/{uuid.uuid4()}/content",
            status="ready",
            mutability="immutable",
            source="upload",
        )
        db.add(artifact)
        db.commit()
        request_id = str(uuid.uuid4())
        submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Find the relevant documentation.",
            client_request_id=request_id,
            enable_tools=True,
            artifact_ids=[artifact.id],
        )
        db.commit()

        for mode, ids in ((False, [artifact.id]), (True, [])):
            with pytest.raises(HTTPException) as conflict:
                submit_turn(
                    db=db,
                    user_id=user.id,
                    conversation_id=str(conversation.id),
                    content="Find the relevant documentation.",
                    client_request_id=request_id,
                    enable_tools=mode,
                    artifact_ids=ids,
                )
            assert conflict.value.status_code == 409
    finally:
        db.close()


def test_unsupported_profile_fallback_or_unowned_artifact_fails_before_queue(monkeypatch):
    _tool_profiles(monkeypatch, fallback_tools=False)
    db = SessionLocal()
    try:
        user, _project, conversation = _owned_conversation(db)
        with pytest.raises(HTTPException) as error:
            submit_turn(
                db=db,
                user_id=user.id,
                conversation_id=str(conversation.id),
                content="Search for this.",
                client_request_id=str(uuid.uuid4()),
                fallback_profile_ids=["secondary-fixture"],
                enable_tools=True,
            )
        assert error.value.status_code == 422
        assert error.value.detail["error"]["code"] == "unsupported_capability"
        assert db.query(Run).filter(Run.conversation_id == conversation.id).count() == 0

        with pytest.raises(HTTPException) as artifact_error:
            submit_turn(
                db=db,
                user_id=user.id,
                conversation_id=str(conversation.id),
                content="Read the selected file.",
                client_request_id=str(uuid.uuid4()),
                artifact_ids=[uuid.uuid4()],
            )
        assert artifact_error.value.status_code == 404
        assert db.query(Run).filter(Run.conversation_id == conversation.id).count() == 0
    finally:
        db.close()
