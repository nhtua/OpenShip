"""Downloaded file publication through the real fenced tool gateway."""

import hashlib

import pytest

from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.config import settings
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.execution.projections import project_operation
from src.openship.providers.control import InvocationControl
from tests.sandboxes.test_tool import Lane


class DownloadLane(Lane):
    def manifest(self):
        return next(item for item in list_capability_manifests() if item.id == "download_file")


@pytest.mark.asyncio
async def test_success_publishes_exact_bytes(monkeypatch, tmp_path):
    from src.openship.tools.download import tool

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")
    body = b"name,value\na,1\n"
    fetches = []

    async def fetch(url, control, write):
        fetches.append(url)
        write(body)

    monkeypatch.setattr(tool, "download_stream", fetch)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:1",
            ref=lane.ref, arguments={"url": "https://example.com/data.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded"
        assert len(result.artifacts) == 1
        ref = result.artifacts[0]
        assert ref.sha256 == hashlib.sha256(body).hexdigest()
        assert result.result["artifact"]["id"] == str(ref.id)
        artifact = lane.db.get(Artifact, ref.id)
        assert artifact.status == "ready"
        with lane.store.open_verified(artifact) as content:
            assert content.read() == body
        replay = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:1",
            ref=lane.ref, arguments={"url": "https://example.com/data.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert replay.status == "succeeded"
        assert replay.artifacts == result.artifacts
        assert len(fetches) == 1
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_gateway_streams_into_artifact_staging(monkeypatch, tmp_path):
    from src.openship.tools.download import tool

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")
    streamed = []

    async def receive(url, control, write):
        write(b"name,value\n")
        streamed.append(True)
        write(b"a,1\n")

    monkeypatch.setattr(tool, "download_stream", receive, raising=False)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:stream",
            ref=lane.ref, arguments={"url": "https://example.com/data.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded"
        assert streamed == [True]
        assert result.artifacts[0].sha256 == hashlib.sha256(b"name,value\na,1\n").hexdigest()
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_failed_download_does_not_publish_artifact(monkeypatch, tmp_path):
    from src.openship.tools.download import tool
    from src.openship.tools.download.transport import DownloadError

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")

    async def fetch(url, control, write):
        raise DownloadError()

    monkeypatch.setattr(tool, "download_stream", fetch)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:failure",
            ref=lane.ref, arguments={"url": "https://example.com/data.csv?secret=private"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "failed"
        assert result.artifacts == ()
        assert "private" not in result.model_dump_json()
        lane.db.expire_all()
        assert not lane.db.query(Artifact).filter(
            Artifact.originating_run_id == lane.run.id, Artifact.status == "ready"
        ).count()
        operation = lane.db.query(ToolOperation).filter_by(run_id=lane.run.id).one()
        assert "private" not in project_operation(lane.db, operation).model_dump_json()
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_unsupported_file_name_returns_safe_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:bad-format",
            ref=lane.ref, arguments={"url": "https://example.com/file.zip?secret=private"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "failed"
        assert "private" not in result.model_dump_json()
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_invalid_download_is_not_published_or_left_staged(monkeypatch, tmp_path):
    from src.openship.tools.download import tool

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")

    async def receive(url, control, write):
        write(b'{"not-json"')

    monkeypatch.setattr(tool, "download_stream", receive)
    lane = DownloadLane(tmp_path)
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:invalid",
            ref=lane.ref, arguments={"url": "https://example.com/file.json"},
            inputs=(), control=InvocationControl(),
        )
        assert result.status == "failed"
        lane.db.expire_all()
        artifacts = lane.db.query(Artifact).filter_by(originating_run_id=lane.run.id).all()
        assert all(artifact.status != "ready" for artifact in artifacts)
        assert not list(lane.store.root.rglob(".staging/*/content"))
    finally:
        lane.db.close()
