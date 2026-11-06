"""Artifact handoff must not turn model-selected IDs into authority."""

import hashlib
import json
import uuid

import httpx
import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ExecutionConfig
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.providers.control import InvocationControl
from tests.sandboxes.test_tool import Lane, Provider, enable_sandbox
from tests.tools.test_download_gateway import DownloadLane

PYTHON_ID = "python_execute"


class PythonLane(Lane):
    """A run pinned to the general python capability."""

    def manifest(self):
        matches = [
            item for item in list_capability_manifests() if item.id == PYTHON_ID
        ]
        assert matches, "the general python capability must be production-registered"
        return matches[0]

    def pin_chat(self, download_ref):
        self.execution = ExecutionConfig(
            graph_kind="chat",
            graph_version=2,
            allowed_capabilities=(self.ref, download_ref),
        )
        self.run.kind = "chat"
        self.run.graph_version = 2
        self.run.execution_configuration = self.execution.model_dump_json()
        self.db.commit()


def _reference(row):
    return ArtifactRef(id=row.id, sha256=row.sha256, size_bytes=row.size_bytes,
                       media_type=row.media_type)


@pytest.mark.asyncio
async def test_download_ref_is_pinned_for_next_call(monkeypatch, tmp_path):
    from src.openship.execution.admission import resolve_chat_inputs
    from src.openship.tools.download import tool

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")

    async def receive(url, control, write):
        write(b"item,value\na,42\n")

    monkeypatch.setattr(tool, "download_stream", receive)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:handoff",
            ref=lane.ref, arguments={"url": "https://example.com/input.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded"
        lane.db.expire_all()
        assert resolve_chat_inputs(lane.db, claim=lane.claim,
                                   requested_ids=(result.artifacts[0].id,), selected=()) == result.artifacts
        changed = lane.db.get(Artifact, result.artifacts[0].id)
        changed.sha256 = "a" * 64
        lane.db.commit()
        with pytest.raises(HTTPException):
            resolve_chat_inputs(lane.db, claim=lane.claim,
                                requested_ids=(changed.id,), selected=())
        changed.sha256 = result.artifacts[0].sha256
        operation = lane.db.get(ToolOperation, result.operation_id)
        result_data = json.loads(operation.result_json)
        result_data["artifact"]["sha256"] = "b" * 64
        changed.sha256 = "b" * 64
        operation.result_json = json.dumps(result_data)
        lane.db.commit()
        with pytest.raises(HTTPException):
            resolve_chat_inputs(lane.db, claim=lane.claim,
                                requested_ids=(changed.id,), selected=())
        changed.sha256 = result.artifacts[0].sha256
        operation.status = "failed"
        lane.db.commit()
        with pytest.raises(HTTPException):
            resolve_chat_inputs(lane.db, claim=lane.claim,
                                requested_ids=(changed.id,), selected=())
    finally:
        lane.db.close()


def test_selected_artifact_is_pinned(tmp_path):
    from src.openship.execution.admission import resolve_chat_inputs

    lane = Lane(tmp_path)
    try:
        artifact = lane.store_artifact(b"selected input")
        ref = _reference(artifact)
        result = resolve_chat_inputs(lane.db, claim=lane.claim,
                                     requested_ids=(artifact.id,), selected=(ref,))
        assert result == (ref,)
    finally:
        lane.db.close()


def test_unselected_conversation_artifact_is_denied(tmp_path):
    from src.openship.execution.admission import resolve_chat_inputs

    lane = Lane(tmp_path)
    try:
        artifact = lane.store_artifact(b"not selected")
        with pytest.raises(HTTPException):
            resolve_chat_inputs(lane.db, claim=lane.claim,
                                requested_ids=(artifact.id,), selected=())
    finally:
        lane.db.close()


@pytest.mark.parametrize("changed", ["digest", "deleted", "missing"])
def test_changed_deleted_or_missing_selected_artifact_is_denied(tmp_path, changed):
    from src.openship.execution.admission import resolve_chat_inputs

    lane = Lane(tmp_path)
    try:
        artifact = lane.store_artifact(b"selected input")
        ref = _reference(artifact)
        if changed == "digest":
            artifact.sha256 = "a" * 64
        elif changed == "deleted":
            artifact.status = "deleted"
        else:
            ref = ref.model_copy(update={"id": uuid.uuid4()})
        lane.db.commit()
        with pytest.raises(HTTPException):
            resolve_chat_inputs(lane.db, claim=lane.claim,
                                requested_ids=(ref.id,), selected=(ref,))
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_other_run_or_conversation_ref_denied(tmp_path):
    """A committed output of another run or conversation never carries over."""
    from src.openship.execution.admission import resolve_chat_inputs

    lane = DownloadLane(tmp_path)
    try:
        other = Conversation(
            user_id=lane.user.id, project_id=lane.project.id, title="Other lane"
        )
        lane.db.add(other)
        lane.db.commit()
        foreign = lane.store_artifact(b"other conversation", conversation_id=other.id)
        with pytest.raises(HTTPException):
            resolve_chat_inputs(
                lane.db, claim=lane.claim, requested_ids=(foreign.id,), selected=()
            )

        own = lane.store_artifact(b"own run")
        row = lane.db.get(Artifact, own.id)
        row.originating_run_id = uuid.uuid4()
        lane.db.commit()
        with pytest.raises(HTTPException):
            resolve_chat_inputs(
                lane.db, claim=lane.claim, requested_ids=(own.id,), selected=()
            )
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_replay_preserves_digest_and_operation_identity(monkeypatch, tmp_path):
    """Re-resolving the same handoff keeps the committed identity, not a new one."""
    from src.openship.execution.admission import resolve_chat_inputs
    from src.openship.tools.download import tool

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")

    async def receive(url, control, write):
        write(b"item,value\na,42\n")

    monkeypatch.setattr(tool, "download_stream", receive)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:handoff",
            ref=lane.ref, arguments={"url": "https://example.com/input.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded"
        row = lane.db.get(Artifact, result.artifacts[0].id)
        with lane.store.open_verified(row) as stored:
            assert stored.read() == b"item,value\na,42\n"
        first = resolve_chat_inputs(
            lane.db, claim=lane.claim, requested_ids=(result.artifacts[0].id,), selected=()
        )
        second = resolve_chat_inputs(
            lane.db, claim=lane.claim, requested_ids=(result.artifacts[0].id,), selected=()
        )
        assert first == second == result.artifacts
        operation = lane.db.get(ToolOperation, result.operation_id)
        assert (
            hashlib.sha256(operation.result_json.encode("utf-8")).hexdigest()
            == operation.result_digest
        )
        operations = lane.db.query(ToolOperation).filter_by(
            run_id=lane.run.id, logical_call_id="download:handoff"
        ).count()
        assert operations == 1, "handoff lookup admitted a second operation"
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_forged_input_id_never_reaches_the_sandbox_provider(monkeypatch, tmp_path):
    """A model-supplied ID that is not authorized stops before any sandbox call."""
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", f"{PYTHON_ID},download_file")
    lane = PythonLane(tmp_path)
    download_manifest = next(
        item for item in list_capability_manifests() if item.id == "download_file"
    )
    download_ref = CapabilityRef(
        id=download_manifest.id,
        version=download_manifest.version,
        manifest_digest=download_manifest.manifest_digest,
    )
    lane.pin_chat(download_ref)
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"stdout","text":"ok\\n"}\n{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: []
    provider.install(monkeypatch)
    gateway = ToolGateway(lane.context().session_factory)
    try:
        with pytest.raises(HTTPException):
            await gateway.execute_tool(
                claim=lane.claim, context=lane.context(), logical_call_id="python:forged",
                ref=lane.ref,
                arguments={"source": "print(1)\n", "input_ids": {"data.csv": str(uuid.uuid4())},
                           "parameters": {}, "output_media": {}},
                inputs=(), control=InvocationControl(),
            )
        assert not provider.commands, "a denied call still dispatched a sandbox command"
        assert provider.created == 0, "a denied call still created a sandbox"
    finally:
        lane.db.close()
