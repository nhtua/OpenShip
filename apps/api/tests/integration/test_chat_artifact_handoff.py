"""Artifact handoff must not turn model-selected IDs into authority."""

import json
import uuid

import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import Artifact
from src.openship.config import settings
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.providers.control import InvocationControl
from tests.sandboxes.test_tool import Lane
from tests.tools.test_download_gateway import DownloadLane


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
