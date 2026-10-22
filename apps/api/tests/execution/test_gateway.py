import base64
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import begin_artifact_delete
from src.openship.auth.models import User
from src.openship.capabilities.catalog import (
    list_capability_manifests,
    list_legacy_search_manifests,
)
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityManifestV2,
    CapabilityRef,
    ConnectorRef,
    ExecutionConfig,
    ExecutionConfigV2,
    ToolInvocationContext,
    canonical_digest,
)
from src.openship.capabilities.definitions import (
    TOOL_HANDLER_OVERRIDES,
    ToolDefinitionEntry,
    implementation_digest_for,
    register_tool_definition,
    unregister_tool_definition,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.models import Connector, ConnectorRevision
from src.openship.connectors.service import create_connector
from src.openship.database.session import SessionLocal
from src.openship.events.service import Event
from src.openship.execution.admission import (
    acquire_execution_slot,
    admit_operation,
    release_execution_slot,
)
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ExecutionSlot, ToolAttempt, ToolOperation
from src.openship.execution.projections import project_operation
from src.openship.providers.control import InvocationControl
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.tools.search.contracts import SearchObservation
from src.openship.tools.search.http import SearchToolError
from src.openship.workspace.models import Project

MANAGED_API_KEY = "synthetic-managed-gateway-key"  # rayloc:ignore


def _running_tool(db, *, tool_id="brave_web_search", worker_id="gateway-worker"):
    manifests = (
        list_legacy_search_manifests()
        if tool_id in {"brave_web_search", "tavily_web_search"}
        else list_capability_manifests()
    )
    manifest = next(item for item in manifests if item.id == tool_id)
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    user = User(
        username=f"gateway_{uuid.uuid4().hex[:12]}",
        email=f"gateway_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Gateway test")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="Gateway test"
    )
    db.add(conversation)
    db.flush()
    local = manifest.effect == "read_only_local"
    refs = (ref,)
    if local:
        refs = (ref,) + tuple(
            CapabilityRef(
                id=item.id,
                version=item.version,
                manifest_digest=item.manifest_digest,
            )
            for item in list_capability_manifests()
            if item.id in {"workspace_list_files", "workspace_read_file"}
            and item.id != ref.id
        )
    execution = ExecutionConfig(
        graph_kind="chat" if local else "tool",
        graph_version=2 if local else 1,
        allowed_capabilities=refs,
        credential_binding_refs=(manifest.credential_binding_ref,)
        if getattr(manifest, "credential_binding_ref", None)
        else (),
    )
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind=execution.graph_kind,
        graph_version=execution.graph_version,
        fence=1,
        execution_configuration=execution.model_dump_json(),
    )
    db.add(run)
    db.flush()
    job = RunJob(
        run_id=run.id,
        fence=1,
        owner_id=worker_id,
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=job.lease_until,
        worker_id=worker_id,
        user_id=user.id,
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        ),
        execution=execution,
        credential_resolver=lambda name: (
            f"{tool_id}-test-key" if name == manifest.credential_binding_ref else None
        ),
        session_factory=SessionLocal,
    )
    return user, project, conversation, run, claim, ref, context


def _observation(ref, query="gateway test"):
    return SearchObservation(
        schema_version=1,
        tool=ref,
        query=query,
        observed_at=datetime.now(UTC),
        requested_count=2,
        returned_count=1,
        items=(
            {
                "title": "Gateway result",
                "url": "https://example.com/result",
                "snippet": "A normalized result.",
                "rank": 1,
            },
        ),
        truncated=False,
        provider_metadata={},
    )


class RegistryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    count: int = Field(ge=1, le=3)


class RegistryHandlerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    private_label: str
    object_id: uuid.UUID


class RegistryPublicResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    label: str
    result_id: uuid.UUID


def _registry_manifest() -> CapabilityManifestV2:
    implementation_files = ("tests/execution/test_gateway.py",)
    fields: dict[str, Any] = {
        "schema_version": 2,
        "id": "registry_gateway_tool",
        "version": "1.0.0",
        "implementation_digest": implementation_digest_for(implementation_files),
        "description": "Return a safe summary for one bounded count.",
        "input_schema": RegistryArguments.model_json_schema(),
        "output_schema": RegistryPublicResult.model_json_schema(),
        "effect": "read_only_local",
        "enablement_requirements": (),
        "limits": {"max_model_result_bytes": 1024},
        "outputs": (),
        "connector_kinds": (),
        "operations": (),
        "target_kinds": (),
        "execution_kind": "maintained",
    }
    return CapabilityManifestV2(**fields, manifest_digest=canonical_digest(fields))


@pytest.mark.asyncio
async def test_registered_definition_runs_through_generic_gateway_and_projects_safe_result(
    monkeypatch, tmp_path
):
    manifest = _registry_manifest()
    object_id = uuid.uuid4()

    async def handler(arguments, _context, _control):
        return RegistryHandlerResult(
            private_label=f"result-{arguments.count}", object_id=object_id
        )

    entry = ToolDefinitionEntry(
        manifest=manifest,
        description="Return a safe summary for one bounded count.",
        argument_model=RegistryArguments,
        result_model=RegistryPublicResult,
        handler=handler,
        handler_result_model=RegistryHandlerResult,
        implementation_files=("tests/execution/test_gateway.py",),
        result_projector=lambda value: {
            "label": value["private_label"],
            "result_id": value["object_id"],
        },
    )
    register_tool_definition(entry)
    monkeypatch.setattr(settings, "tool_enabled_ids", manifest.id)
    db = SessionLocal()
    try:
        _user, _project, _conversation, _run, claim, ref, context = _running_tool(
            db, tool_id=manifest.id
        )
        context.artifact_store = LocalArtifactStore(
            tmp_path / "registry-store", uuid.uuid4()
        )
        context.artifact_store.initialize()

        result = await ToolGateway(SessionLocal).execute_tool(
            claim=claim,
            context=context,
            logical_call_id="model:0:registry",
            ref=ref,
            arguments={"count": 2},
            control=InvocationControl(),
        )

        assert result.status == "succeeded", result.error
        assert result.result == {"label": "result-2", "result_id": str(object_id)}
        operation = db.query(ToolOperation).filter_by(run_id=claim.run_id).one()
        assert project_operation(db, operation).result == result.result
    finally:
        db.close()
        unregister_tool_definition(manifest.id)


def _write_workspace_artifact(db, store, project, conversation, content: bytes):
    artifact_id = uuid.uuid4()
    expected = ArtifactRef(
        id=artifact_id,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type="text/plain",
    )
    staged = store.stage(
        artifact_id,
        (content,),
        expected,
        project_id=project.id,
        conversation_id=conversation.id,
    )
    key = f"projects/{project.id}/conversations/{conversation.id}/artifacts/{artifact_id}/content"
    store.publish_file(staged, key)
    artifact = Artifact(
        id=artifact_id,
        project_id=project.id,
        conversation_id=conversation.id,
        kind="upload",
        media_type="text/plain",
        display_name="saved.txt",
        size_bytes=len(content),
        sha256=expected.sha256,
        relative_storage_key=key,
        status="ready",
        mutability="immutable",
        source="upload",
    )
    db.add(artifact)
    db.commit()
    return artifact


@pytest.mark.asyncio
async def test_gateway_runs_workspace_list_and_read_without_credentials_or_new_artifact(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        settings,
        "tool_enabled_ids",
        "workspace_list_files,workspace_read_file",
    )
    db = SessionLocal()
    _user, project, conversation, run, claim, list_ref, context = _running_tool(
        db, tool_id="workspace_list_files"
    )
    store = LocalArtifactStore(tmp_path / "workspace-store", uuid.uuid4())
    store.initialize()
    context.artifact_store = store
    context.credential_resolver = lambda _name: pytest.fail(
        "local workspace tools must not resolve provider credentials"
    )
    artifact = _write_workspace_artifact(
        db, store, project, conversation, "A🌙B".encode()
    )

    listed = await ToolGateway(SessionLocal).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:0:list",
        ref=list_ref,
        arguments={"limit": 5},
        control=InvocationControl(),
    )

    assert listed.status == "succeeded"
    assert [item["id"] for item in listed.result["items"]] == [str(artifact.id)]
    assert listed.artifacts == ()
    list_operation = db.query(ToolOperation).filter_by(run_id=run.id).one()
    assert list_operation.credential_binding_ref is None
    assert list_operation.output_artifact_ids == "[]"
    assert (
        db.query(ToolAttempt).filter_by(operation_id=list_operation.id).one().outcome
        == "succeeded"
    )
    assert project_operation(db, list_operation).result == listed.result

    read_manifest = next(
        item for item in list_capability_manifests() if item.id == "workspace_read_file"
    )
    read_ref = CapabilityRef(
        id=read_manifest.id,
        version=read_manifest.version,
        manifest_digest=read_manifest.manifest_digest,
    )
    read_arguments = {
        "artifact_id": str(artifact.id),
        "offset": 1,
        "max_bytes": 4,
    }
    admit_operation(
        db,
        claim,
        context.authorization,
        "model:1:read",
        read_ref,
        read_arguments,
        (),
    )
    db.commit()
    with pytest.raises(HTTPException) as pinned:
        begin_artifact_delete(db, context.authorization.principal_id, artifact.id)
    assert pinned.value.detail["error"]["code"] == "artifact_is_pinned"
    db.rollback()
    read = await ToolGateway(SessionLocal).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:1:read",
        ref=read_ref,
        arguments=read_arguments,
        control=InvocationControl(),
    )

    assert read.status == "succeeded"
    assert read.result["content"] == "🌙"
    assert read.result["version_id"] == str(artifact.id)
    assert db.query(Artifact).filter_by(conversation_id=conversation.id).count() == 1
    assert db.query(ToolOperation).filter_by(run_id=run.id).count() == 2
    db.close()


@pytest.mark.parametrize(
    ("query", "expected_filename"),
    [
        ("Café: stars & galaxies!", "cafe_stars_galaxies.json"),
        ("🌙", "search-results.json"),
        ("é" * 300, "e" * 250 + ".json"),
    ],
)
@pytest.mark.asyncio
async def test_gateway_dispatches_once_and_publishes_artifact_result_event_atomically(
    monkeypatch, tmp_path, query, expected_filename
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, conversation, run, claim, ref, context = _running_tool(db)
    store = LocalArtifactStore(tmp_path / "artifact-store", uuid.uuid4())
    store.initialize()
    context.artifact_store = store
    calls = []

    async def search(arguments, invocation, control):
        calls.append((arguments, invocation, control))
        return _observation(ref, arguments.query)

    monkeypatch.setitem(
        TOOL_HANDLER_OVERRIDES,
        "brave_web_search",
        search,
    )
    gateway = ToolGateway(SessionLocal)

    result = await gateway.execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": query, "max_results": 2},
        control=InvocationControl(),
    )

    assert result.status == "succeeded"
    assert len(calls) == 1
    assert result.result["tool"]["id"] == "brave_web_search"
    assert len(result.artifacts) == 1
    assert result.result["artifact"]["id"] == str(result.artifacts[0].id)
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    attempt = (
        db.query(ToolAttempt).filter(ToolAttempt.operation_id == operation.id).one()
    )
    artifact = db.query(Artifact).filter_by(id=result.artifacts[0].id).one()
    assert operation.status == "succeeded"
    assert operation.error_code is None
    assert json.loads(operation.output_artifact_ids) == [str(artifact.id)]
    assert attempt.outcome == "succeeded"
    assert attempt.request_digest == operation.arguments_digest
    assert attempt.response_digest == artifact.sha256
    assert artifact.status == "ready"
    assert artifact.originating_run_id == run.id
    assert artifact.originating_operation_id == operation.id
    assert artifact.source == "tool"
    assert artifact.display_name == expected_filename
    with store.open_verified(artifact) as content:
        snapshot = content.read()
    assert hashlib.sha256(snapshot).hexdigest() == artifact.sha256
    assert json.loads(snapshot)["query"] == query
    assert [
        event.type
        for event in db.query(Event)
        .filter(Event.run_id == run.id)
        .order_by(Event.sequence)
    ] == [
        "tool.requested",
        "tool.started",
        "artifact.created",
        "tool.completed",
    ]
    assert (
        db.query(Outbox).filter(Outbox.conversation_id == conversation.id).count() == 4
    )
    for event in db.query(Event).filter(Event.run_id == run.id):
        assert query not in event.payload
        assert "test-key" not in event.payload
    db.close()


@pytest.mark.asyncio
async def test_connector_revoked_after_dispatch_cannot_publish_result(
    monkeypatch, tmp_path
):
    encryption_file = tmp_path / "gateway-encryption.json"
    hmac_file = tmp_path / "gateway-hmac.json"
    for path, key in ((encryption_file, b"e" * 32), (hmac_file, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "gateway-managed-v1",
                    "keys": {
                        "gateway-managed-v1": base64.b64encode(key).decode("ascii")
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_file)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_file)
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")

    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    db = SessionLocal()
    user = User(
        username=f"gateway-managed-{uuid.uuid4().hex[:12]}",
        email=f"gateway-managed-{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Managed gateway")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="Managed gateway"
    )
    db.add(conversation)
    db.flush()
    monkeypatch.setattr(settings, "connector_operator_user_id", user.id)
    connector = create_connector(
        db,
        actor=user,
        visibility="global",
        project_id=None,
        kind="brave",
        name="Managed Brave",
        configuration_raw={"schema_version": 1, "kind": "brave"},
        api_key=MANAGED_API_KEY,
    )
    connector_id = uuid.UUID(connector["id"])
    revision = (
        db.query(ConnectorRevision)
        .filter(ConnectorRevision.connector_id == connector_id)
        .one()
    )
    connector_ref = ConnectorRef(
        id=connector_id,
        kind="brave",
        configuration_revision=revision.revision,
        visibility="global",
        credential_version_id=revision.credential_version_id,
    )
    execution = ExecutionConfigV2(
        graph_kind="tool",
        graph_version=2,
        allowed_capabilities=(ref,),
        connector_refs=(connector_ref,),
    )
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind="tool",
        graph_version=2,
        fence=1,
        execution_configuration=execution.model_dump_json(),
    )
    db.add(run)
    db.flush()
    job = RunJob(
        run_id=run.id,
        fence=1,
        owner_id="managed-gateway-worker",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=job.lease_until,
        worker_id=job.owner_id,
        user_id=user.id,
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        ),
        execution=execution,
        credential_resolver=lambda _name: pytest.fail(
            "managed Connector key must not use the operator resolver"
        ),
    )
    store = LocalArtifactStore(tmp_path / "managed-store", uuid.uuid4())
    store.initialize()
    context.artifact_store = store

    dispatch_trace = []

    async def revoke_after_acquisition(arguments, invocation, _control):
        try:
            acquired = invocation.credential_resolver("BRAVE_SEARCH_API_KEY")
        except Exception as failure:
            dispatch_trace.append(
                (
                    "acquire_failed",
                    type(failure).__name__,
                    getattr(failure, "code", None),
                )
            )
            raise
        dispatch_trace.append(("acquired", acquired == MANAGED_API_KEY))
        assert acquired == MANAGED_API_KEY
        with SessionLocal() as revoke_db:
            row = revoke_db.query(Connector).filter(Connector.id == connector_id).one()
            row.revoked_at = datetime.now(UTC)
            revoke_db.commit()
        return _observation(ref, arguments.query)

    monkeypatch.setitem(
        TOOL_HANDLER_OVERRIDES, "brave_web_search", revoke_after_acquisition
    )
    result = await ToolGateway(SessionLocal).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": "late revoke", "max_results": 2},
        control=InvocationControl(),
    )

    db.expire_all()
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    artifact = (
        db.query(Artifact)
        .filter(Artifact.originating_operation_id == operation.id)
        .one()
    )
    assert result.status == "failed"
    assert dispatch_trace == [("acquired", True)]
    assert result.error.code == "connector_revoked"
    assert operation.status == "failed"
    assert artifact.status == "deleted"
    assert (
        db.query(Event)
        .filter(Event.run_id == run.id, Event.type == "tool.completed")
        .count()
        == 0
    )
    events = db.query(Event).filter(Event.run_id == run.id).all()
    outbox_rows = db.query(Outbox).filter(Outbox.conversation_id == conversation.id).all()
    public_projection = project_operation(db, operation).model_dump_json()
    durable_surfaces = [
        *(str(getattr(run, column.name)) for column in Run.__table__.columns),
        *(str(getattr(operation, column.name)) for column in ToolOperation.__table__.columns),
        *(event.payload for event in events),
        *(row.payload for row in outbox_rows),
        public_projection,
    ]
    assert all(MANAGED_API_KEY not in surface for surface in durable_surfaces)
    db.close()


@pytest.mark.asyncio
async def test_completed_logical_call_replay_returns_recorded_result_without_dispatch(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, _run, claim, ref, context = _running_tool(db)
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    calls = []

    async def search(arguments, _invocation, _control):
        calls.append(arguments.query)
        return _observation(ref, arguments.query)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    gateway = ToolGateway(SessionLocal)
    command = {
        "claim": claim,
        "context": context,
        "logical_call_id": "manual:0",
        "ref": ref,
        "arguments": {"query": "gateway test", "max_results": 2},
        "control": InvocationControl(),
    }

    first = await gateway.execute_tool(**command)
    replay = await gateway.execute_tool(**command)

    assert first == replay
    assert calls == ["gateway test"]
    db.close()


@pytest.mark.asyncio
async def test_uncertain_provider_timeout_is_recorded_and_never_retried(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    calls = []

    async def search(_arguments, _invocation, _control):
        calls.append("sent")
        raise SearchToolError("provider_timeout")

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    gateway = ToolGateway(SessionLocal)
    command = {
        "claim": claim,
        "context": context,
        "logical_call_id": "manual:0",
        "ref": ref,
        "arguments": {"query": "gateway test", "max_results": 2},
        "control": InvocationControl(),
    }

    failed = await gateway.execute_tool(**command)
    replay = await gateway.execute_tool(**command)

    assert failed.status == "failed"
    assert failed.error.code == "provider_timeout"
    assert replay == failed
    assert calls == ["sent"]
    assert (
        db.query(ToolAttempt)
        .join(ToolOperation)
        .filter(ToolOperation.run_id == run.id)
        .count()
        == 1
    )
    assert (
        db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one().status
        == "failed"
    )
    db.close()


@pytest.mark.asyncio
async def test_stale_fence_during_file_publication_cannot_mark_output_ready(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    store.initialize()
    context.artifact_store = store
    calls = []
    publish_file = store.publish_file

    async def search(arguments, _invocation, _control):
        calls.append(arguments.query)

        return _observation(ref, arguments.query)

    def publish_then_replace_owner(staged, relative_key):
        publish_file(staged, relative_key)
        changed = SessionLocal()
        try:
            stale_run = changed.query(Run).filter(Run.id == run.id).one()
            stale_job = changed.query(RunJob).filter(RunJob.run_id == run.id).one()
            stale_run.fence = claim.fence + 1
            stale_job.fence = claim.fence + 1
            stale_job.owner_id = "replacement-worker"
            stale_job.lease_until = datetime.now(UTC) + timedelta(minutes=1)
            changed.commit()
        finally:
            changed.close()

    monkeypatch.setattr(store, "publish_file", publish_then_replace_owner)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    gateway = ToolGateway(SessionLocal)
    command = {
        "claim": claim,
        "context": context,
        "logical_call_id": "manual:0",
        "ref": ref,
        "arguments": {"query": "gateway test", "max_results": 2},
        "control": InvocationControl(),
    }

    result = await gateway.execute_tool(**command)

    db.expire_all()
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    artifact = (
        db.query(Artifact)
        .filter(Artifact.originating_operation_id == operation.id)
        .one()
    )
    assert result.status == "interrupted"
    assert operation.status == "running"
    assert artifact.status == "staging"
    assert operation.output_artifact_ids == "[]"
    assert not (store.root / artifact.relative_storage_key).exists()
    assert (
        db.query(Event)
        .filter(Event.run_id == run.id, Event.type == "tool.completed")
        .count()
        == 0
    )
    assert calls == ["gateway test"]

    with pytest.raises(HTTPException):
        await gateway.execute_tool(**command)
    assert calls == ["gateway test"]
    db.query(ExecutionSlot).filter(ExecutionSlot.run_id == run.id).delete(
        synchronize_session=False
    )
    db.commit()
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_applied", [False, True])
async def test_uncertain_publication_commit_keeps_only_unready_hidden_output(
    monkeypatch, tmp_path, commit_applied
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    store.initialize()
    context.artifact_store = store
    calls = []

    async def search(arguments, _invocation, _control):
        calls.append(arguments.query)
        return _observation(ref, arguments.query)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    original_commit = Session.commit
    commit_count = 0

    def fail_before_fourth_commit(session):
        nonlocal commit_count
        commit_count += 1
        if commit_count == 4:
            if commit_applied:
                original_commit(session)
            raise RuntimeError("simulated lost commit acknowledgement")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_before_fourth_commit)
    gateway = ToolGateway(SessionLocal)
    command = {
        "claim": claim,
        "context": context,
        "logical_call_id": "manual:0",
        "ref": ref,
        "arguments": {"query": "gateway test", "max_results": 2},
        "control": InvocationControl(),
    }

    result = await gateway.execute_tool(**command)

    db.expire_all()
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    artifact = (
        db.query(Artifact)
        .filter(Artifact.originating_operation_id == operation.id)
        .one()
    )
    assert (store.root / artifact.relative_storage_key).is_file()
    assert calls == ["gateway test"]

    if commit_applied:
        assert result.status == "succeeded"
        assert operation.status == "succeeded"
        assert artifact.status == "ready"
        assert (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "tool.completed")
            .count()
            == 1
        )
        assert await gateway.execute_tool(**command) == result
    else:
        assert result.status == "interrupted"
        assert operation.status == "running"
        assert artifact.status == "staging"
        assert (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "tool.completed")
            .count()
            == 0
        )
        with pytest.raises(HTTPException):
            await gateway.execute_tool(**command)
        db.query(ExecutionSlot).filter(ExecutionSlot.run_id == run.id).delete(
            synchronize_session=False
        )
        db.commit()
    assert calls == ["gateway test"]
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["authority", "disabled", "digest", "arguments"])
async def test_invalid_admission_never_dispatches_or_reserves_artifacts(
    monkeypatch, tmp_path, invalid
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    if invalid == "authority":
        context.authorization = AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=context.authorization.project_id,
            conversation_id=context.authorization.conversation_id,
            action="execute_tool",
            target_id=str(run.id),
        )
    elif invalid == "disabled":
        monkeypatch.setattr(settings, "tool_enabled_ids", "")
    elif invalid == "digest":
        ref = CapabilityRef(id=ref.id, version=ref.version, manifest_digest="0" * 64)
    arguments = (
        {"query": "bad\nquery", "max_results": 2}
        if invalid == "arguments"
        else {"query": "gateway test", "max_results": 2}
    )
    called = []

    async def search(*_args):
        called.append(True)
        return _observation(ref)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    gateway = ToolGateway(SessionLocal)

    with pytest.raises(HTTPException):
        await gateway.execute_tool(
            claim=claim,
            context=context,
            logical_call_id="manual:0",
            ref=ref,
            arguments=arguments,
            control=InvocationControl(),
        )

    assert called == []
    assert db.query(ToolOperation).filter(ToolOperation.run_id == run.id).count() == 0
    assert (
        db.query(ToolAttempt)
        .join(ToolOperation)
        .filter(ToolOperation.run_id == run.id)
        .count()
        == 0
    )
    assert db.query(ExecutionSlot).filter(ExecutionSlot.run_id == run.id).count() == 0
    db.close()


@pytest.mark.asyncio
async def test_missing_credential_fails_before_attempt_or_provider_dispatch(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    context.credential_resolver = lambda _name: None
    called = []

    async def search(*_args):
        called.append(True)
        return _observation(ref)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)

    result = await ToolGateway(SessionLocal).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": "gateway test", "max_results": 2},
        control=InvocationControl(),
    )

    assert result.status == "failed"
    assert result.error.code == "missing_credentials"
    assert called == []
    assert (
        db.query(ToolAttempt)
        .join(ToolOperation)
        .filter(ToolOperation.run_id == run.id)
        .count()
        == 0
    )
    db.close()


@pytest.mark.asyncio
async def test_capacity_wait_is_not_a_provider_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "tool_concurrency", 1)
    db = SessionLocal()
    db.query(ExecutionSlot).delete(synchronize_session=False)
    db.commit()
    _user_a, _project_a, _conversation_a, _run_a, claim_a, ref_a, context_a = (
        _running_tool(db)
    )
    _user, _project, _conversation, run, claim, ref, context = _running_tool(
        db, worker_id="gateway-busy"
    )
    # A separate running operation owns the only shared slot.
    blocker = admit_operation(
        db,
        claim_a,
        context_a.authorization,
        "blocker",
        ref_a,
        {"query": "holding slot", "max_results": 1},
        (),
    )
    slot = acquire_execution_slot(db, claim_a, blocker.id)
    assert slot is not None
    db.commit()
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    called = []

    async def search(*_args):
        called.append(True)
        return _observation(ref)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)

    result = await ToolGateway(SessionLocal, capacity_poll_interval=0.005).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": "gateway test", "max_results": 2},
        control=InvocationControl(deadline=time.monotonic() + 0.03),
    )

    assert result.status == "failed"
    assert result.error.code == "capacity_timeout"
    assert called == []
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    assert operation.status == "failed"
    assert (
        db.query(ToolAttempt).filter(ToolAttempt.operation_id == operation.id).count()
        == 0
    )
    assert (
        db.query(ExecutionSlot).filter(ExecutionSlot.id == slot.id).one().operation_id
        == blocker.id
    )
    assert release_execution_slot(db, claim_a, slot.id)
    db.commit()
    db.close()


@pytest.mark.asyncio
async def test_capacity_wait_retries_after_shared_slot_is_released(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "tool_concurrency", 1)
    db = SessionLocal()
    db.query(ExecutionSlot).delete(synchronize_session=False)
    db.commit()
    _user_a, _project_a, _conversation_a, _run_a, claim_a, ref_a, context_a = (
        _running_tool(db)
    )
    _user, _project, _conversation, run, claim, ref, context = _running_tool(
        db, worker_id="gateway-waiter"
    )
    blocker = admit_operation(
        db,
        claim_a,
        context_a.authorization,
        "blocker",
        ref_a,
        {"query": "holding slot", "max_results": 1},
        (),
    )
    slot = acquire_execution_slot(db, claim_a, blocker.id)
    assert slot is not None
    db.commit()
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    called = []

    async def search(arguments, _invocation, _control):
        called.append(arguments.query)
        return _observation(ref, arguments.query)

    async def release_then_yield(_delay):
        release_db = SessionLocal()
        try:
            assert release_execution_slot(release_db, claim_a, slot.id)
            release_db.commit()
        finally:
            release_db.close()

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    result = await ToolGateway(
        SessionLocal, capacity_poll_interval=0.005, sleep=release_then_yield
    ).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": "gateway test", "max_results": 2},
        control=InvocationControl(deadline=time.monotonic() + 1),
    )

    assert result.status == "succeeded"
    assert called == ["gateway test"]
    operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
    assert operation.status == "succeeded"
    db.close()


@pytest.mark.asyncio
async def test_chat_run_cannot_admit_a_model_free_tool_call(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    run.kind = "chat"
    db.commit()
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    called = []

    async def search(*_args):
        called.append(True)
        return _observation(ref)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)

    with pytest.raises(HTTPException):
        await ToolGateway(SessionLocal).execute_tool(
            claim=claim,
            context=context,
            logical_call_id="manual:0",
            ref=ref,
            arguments={"query": "gateway test", "max_results": 2},
            control=InvocationControl(),
        )

    assert called == []
    assert db.query(ToolOperation).filter(ToolOperation.run_id == run.id).count() == 0
    db.close()


@pytest.mark.asyncio
async def test_pinned_non_tool_graph_is_rejected_before_operation_or_attempt(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, claim, ref, context = _running_tool(db)
    mismatched = context.execution.model_copy(update={"graph_kind": "chat"})
    run.execution_configuration = mismatched.model_dump_json()
    db.commit()
    context.artifact_store = LocalArtifactStore(tmp_path / "store", uuid.uuid4())
    context.artifact_store.initialize()
    called = []

    async def search(*_args):
        called.append(True)
        return _observation(ref)

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)

    with pytest.raises(HTTPException) as refused:
        await ToolGateway(SessionLocal).execute_tool(
            claim=claim,
            context=context,
            logical_call_id="manual:0",
            ref=ref,
            arguments={"query": "gateway test", "max_results": 2},
            control=InvocationControl(),
        )

    assert refused.value.status_code == 403
    assert called == []
    assert db.query(ToolOperation).filter(ToolOperation.run_id == run.id).count() == 0
    db.close()
