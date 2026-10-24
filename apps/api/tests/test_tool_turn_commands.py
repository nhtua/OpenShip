"""Admission pins exact tool/artifact authority for tool-enabled chat turns."""

import json
import uuid

import pytest
from fastapi import HTTPException

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_legacy_search_manifests
from src.openship.capabilities.contracts import ExecutionConfig, decode_execution_config
from src.openship.capabilities.models import ToolPolicyOverride
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.selection import ConnectorSelectionError
from src.openship.database.session import SessionLocal
from src.openship.runs.agent_graph import _available_tools
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


def _no_managed_search_connectors(monkeypatch):
    def missing(*_args, **_kwargs):
        raise ConnectorSelectionError("connector_required")

    monkeypatch.setattr("src.openship.runs.service.resolve_connector", missing)
    monkeypatch.setattr(
        "src.openship.runs.service._connector_rows_for_project",
        lambda *_args, **_kwargs: 0,
    )


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


def _aws_connector(db, user, project, name="AWS read role"):
    connector_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    configuration = ConnectorConfiguration(
        kind="aws",
        region="us-east-1",
        expected_account_id="123456789012",
        basic_role_arn="arn:aws:iam::123456789012:role/readonly",
    )
    connector = Connector(
        id=connector_id,
        kind="aws",
        visibility="project",
        project_id=project.id,
        name=name,
        current_revision=1,
        enabled=True,
        managed_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    db.add(connector)
    db.flush()
    db.add(
        CredentialVersion(
            id=credential_id,
            connector_id=connector_id,
            visibility="project",
            project_id=project.id,
            kind="aws_bootstrap",
            schema_version=1,
            key_id="test-v1",
            nonce=b"n" * 12,
            ciphertext=b"c" * 32,
            state="active",
        )
    )
    db.flush()
    db.add(
        ConnectorRevision(
            connector_id=connector_id,
            revision=1,
            schema_version=1,
            visibility="project",
            project_id=project.id,
            configuration_json=configuration.canonical_bytes().decode(),
            configuration_digest=configuration.digest(),
            credential_version_id=credential_id,
            created_by_user_id=user.id,
        )
    )
    db.flush()
    return connector


def _enable_policy_tools(db, *capability_ids):
    for capability_id in capability_ids:
        db.merge(ToolPolicyOverride(capability_id=capability_id, enabled=True))
    db.flush()


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
    _no_managed_search_connectors(monkeypatch)
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
        expected = {manifest.id: manifest for manifest in list_legacy_search_manifests()}

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


def test_auto_aws_tools_pin_one_connector_without_resource_bindings(monkeypatch):
    _tool_profiles(monkeypatch)
    monkeypatch.setattr(
        settings,
        "tool_enabled_ids",
        "aws_read_catalog,aws_read",
    )
    db = SessionLocal()
    try:
        user, project, conversation = _owned_conversation(db)
        _enable_policy_tools(db, "aws_read_catalog", "aws_read")
        connector = _aws_connector(db, user, project)
        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="Find readable S3 buckets, then report their size.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
        )
        db.commit()
        payload = json.loads(run.execution_configuration)
        config = decode_execution_config(run.execution_configuration)

        assert run.graph_version == 4
        assert payload["schema_version"] == 4
        assert payload["aws_read_catalog_digest"]
        assert {item["id"] for item in payload["allowed_capabilities"]} == {
            "aws_read_catalog",
            "aws_read",
        }
        assert payload["connector_refs"][0]["id"] == str(connector.id)
        assert "resource_binding_refs" not in payload
        assert "selected_resource_binding_refs" not in payload
        assert payload["loop_limits"] == {
            "deadline_seconds": payload["loop_limits"]["deadline_seconds"],
            "max_tool_calls": 12,
            "max_model_turns": 13,
            "max_generation_attempts": 15,
            "max_output_tokens_per_request": 1000,
            "max_total_output_tokens": 15000,
        }
        enabled_ids = frozenset({"aws_read_catalog", "aws_read"})
        assert {tool.name for tool in _available_tools(config, enabled_ids)} == {
            "aws_read_catalog",
            "aws_read",
        }
    finally:
        db.close()


def test_auto_aws_tools_require_explicit_connector_when_multiple_roles_exist(monkeypatch):
    _tool_profiles(monkeypatch)
    monkeypatch.setattr(settings, "tool_enabled_ids", "aws_read_catalog,aws_read")
    db = SessionLocal()
    try:
        user, project, conversation = _owned_conversation(db)
        _enable_policy_tools(db, "aws_read_catalog", "aws_read")
        _aws_connector(db, user, project, "Role one")
        _aws_connector(db, user, project, "Role two")

        with pytest.raises(HTTPException) as error:
            submit_turn(
                db=db,
                user_id=user.id,
                conversation_id=str(conversation.id),
                content="List the available S3 buckets.",
                client_request_id=str(uuid.uuid4()),
                enable_tools=True,
            )
        assert error.value.status_code == 422
        assert error.value.detail["error"]["code"] == "connector_ambiguous"

        selected = _aws_connector(db, user, project, "Role three")
        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=str(conversation.id),
            content="List the available S3 buckets.",
            client_request_id=str(uuid.uuid4()),
            enable_tools=True,
            connector_selections={"aws": selected.id},
        )
        db.commit()
        payload = json.loads(run.execution_configuration)
        assert [item["id"] for item in payload["connector_refs"]] == [str(selected.id)]
    finally:
        db.close()


def test_aws_binding_selection_is_rejected_for_discovery_chat(monkeypatch):
    _tool_profiles(monkeypatch)
    monkeypatch.setattr(settings, "tool_enabled_ids", "aws_read_catalog,aws_read")
    db = SessionLocal()
    try:
        user, project, conversation = _owned_conversation(db)
        _enable_policy_tools(db, "aws_read_catalog", "aws_read")
        _aws_connector(db, user, project)
        with pytest.raises(HTTPException) as error:
            submit_turn(
                db=db,
                user_id=user.id,
                conversation_id=str(conversation.id),
                content="Inspect the bucket.",
                client_request_id=str(uuid.uuid4()),
                enable_tools=True,
                resource_binding_selections=[uuid.uuid4()],
            )
        assert error.value.status_code == 422
        assert error.value.detail["error"]["code"] == "resource_binding_retired"
    finally:
        db.close()


def test_aws_binding_selection_is_rejected_when_tools_are_disabled(monkeypatch):
    _tool_profiles(monkeypatch)
    db = SessionLocal()
    try:
        user, _project, conversation = _owned_conversation(db)
        with pytest.raises(HTTPException) as error:
            submit_turn(
                db=db,
                user_id=user.id,
                conversation_id=str(conversation.id),
                content="Inspect the bucket.",
                client_request_id=str(uuid.uuid4()),
                enable_tools=False,
                resource_binding_selections=[uuid.uuid4()],
            )
        assert error.value.status_code == 422
        assert error.value.detail["error"]["code"] == "resource_binding_retired"
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
