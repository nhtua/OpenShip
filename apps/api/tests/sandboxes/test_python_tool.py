"""Contract tests for the general Python sandbox capability."""

import uuid

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ExecutionConfig
from src.openship.config import settings
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from tests.sandboxes.test_tool import Lane, Provider, enable_sandbox
from tests.tools.test_download_gateway import DownloadLane


def test_stdout_only_without_inputs_or_outputs():
    from src.openship.sandboxes.python_tool import PythonArguments

    args = PythonArguments(source="print(42)", input_ids={}, parameters={}, output_media={})
    assert args.source == "print(42)"
    assert args.input_ids == {}
    assert args.output_media == {}


def test_requires_exactly_one_source():
    from src.openship.sandboxes.python_tool import PythonArguments

    with pytest.raises(ValidationError):
        PythonArguments(input_ids={}, parameters={}, output_media={})
    with pytest.raises(ValidationError):
        PythonArguments(source="print(42)", source_id=uuid.uuid4(),
                        input_ids={}, parameters={}, output_media={})


def test_model_cannot_choose_runtime():
    from src.openship.sandboxes.python_tool import PythonArguments

    with pytest.raises(ValidationError):
        PythonArguments(source="print(42)", image="attacker/image", input_ids={},
                        parameters={}, output_media={})


def test_source_and_input_bounds():
    from src.openship.sandboxes.python_tool import PythonArguments

    with pytest.raises(ValidationError):
        PythonArguments(source="x" * (256 * 1024 + 1), input_ids={}, parameters={}, output_media={})
    with pytest.raises(ValidationError):
        PythonArguments(source="print(42)", input_ids={"../bad": uuid.uuid4()},
                        parameters={}, output_media={})


def test_source_capture_is_immutable_and_operation_scoped(monkeypatch, tmp_path):
    from src.openship.sandboxes.python_tool import capture_python_source

    monkeypatch.setattr(settings, "tool_enabled_ids", "download_file")
    lane = DownloadLane(tmp_path)
    try:
        gateway = ToolGateway(lane.context().session_factory)
        operation_id, _ = gateway._admit(
            lane.claim, lane.context(), "capture:test", lane.ref,
            {"url": "https://example.com/input.csv"}, (),
        )
        lane.db.expire_all()
        operation = lane.db.get(ToolOperation, operation_id)
        operation.status = "running"
        operation.capability_id = "python_execute"
        lane.db.commit()
        context = lane.context()
        source = b"print('hello')\n"
        ref = capture_python_source(context, lane.claim, operation_id, source)
        lane.db.expire_all()
        row = lane.db.get(Artifact, ref.id)
        assert row.display_name.endswith(".py")
        assert row.originating_operation_id == operation_id
        with lane.store.open_verified(row) as file:
            assert file.read() == source
        assert capture_python_source(context, lane.claim, operation_id, source) == ref
        with pytest.raises(ProviderError):
            capture_python_source(context, lane.claim, operation_id, b"print('changed')")
    finally:
        lane.db.close()


class PythonLane(Lane):
    def manifest(self):
        return next(item for item in list_capability_manifests() if item.id == "python_execute")


@pytest.mark.asyncio
async def test_python_stdout_runs_once_and_retains_source(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute")
    lane = PythonLane(tmp_path)
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"stdout","text":"42\\n"}\n{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: []
    provider.install(monkeypatch)
    args = {"source": "print(42)\n", "input_ids": {}, "parameters": {}, "output_media": {}}
    try:
        gateway = ToolGateway(lane.context().session_factory)
        result = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:stdout",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded", result.error
        assert result.result["stdout"] == "42\n"
        assert result.result["exit_code"] == 0
        assert result.result["outputs"] == {}
        source_id = uuid.UUID(result.result["source"]["id"])
        lane.db.expire_all()
        row = lane.db.get(Artifact, source_id)
        with lane.store.open_verified(row) as file:
            assert file.read() == b"print(42)\n"
        replay = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:stdout",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert replay == result
        assert len(provider.commands) == 1
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_python_transfers_selected_input_and_publishes_declared_output(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute")
    lane = PythonLane(tmp_path)
    input_file = lane.store_artifact(b"current-input-42\n")

    class RecordingProvider(Provider):
        def __init__(self):
            super().__init__()
            self.uploads = []

        def route(self, request):
            if request.url.path.endswith("/files/upload"):
                self.uploads.append(request.content)
            return super().route(request)

    provider = RecordingProvider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: [{"path": f"{path}/report.txt", "size": 6, "type": "file"}]
    provider.download = lambda path: b"result"
    provider.install(monkeypatch)
    args = {"source": "print('ok')\n", "input_ids": {"input.txt": str(input_file.id)},
            "parameters": {}, "output_media": {"report.txt": "text/plain"}}
    try:
        result = await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:input",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded", result.error
        assert any(b"current-input-42" in upload for upload in provider.uploads)
        row = lane.db.get(Artifact, uuid.UUID(result.result["outputs"]["report.txt"]["id"]))
        with lane.store.open_verified(row) as file:
            assert file.read() == b"result"
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_chat_handoff_uses_only_committed_same_run_artifacts(monkeypatch, tmp_path):
    from src.openship.tools.download import tool as download_tool

    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute,download_file")
    lane = PythonLane(tmp_path)
    manifest = next(item for item in list_capability_manifests() if item.id == "download_file")
    download_ref = CapabilityRef(id=manifest.id, version=manifest.version,
                                 manifest_digest=manifest.manifest_digest)
    lane.execution = ExecutionConfig(graph_kind="chat", graph_version=2,
                                     allowed_capabilities=(lane.ref, download_ref))
    lane.run.kind = "chat"
    lane.run.graph_version = 2
    lane.run.execution_configuration = lane.execution.model_dump_json()
    lane.db.commit()

    async def receive(url, control, write):
        write(b"same-run-input\n")

    monkeypatch.setattr(download_tool, "download_stream", receive)
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: []
    provider.install(monkeypatch)
    gateway = ToolGateway(lane.context().session_factory)
    try:
        download = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="download:input",
            ref=download_ref, arguments={"url": "https://example.com/input.csv"},
            inputs=(), control=InvocationControl(),
        )
        assert download.status == "succeeded"
        unrelated = lane.store_artifact(b"unselected")
        args = {"source": "print('ok')", "input_ids": {"input.csv": str(unrelated.id)},
                "parameters": {}, "output_media": {}}
        with pytest.raises(HTTPException):
            await gateway.execute_tool(
                claim=lane.claim, context=lane.context(), logical_call_id="python:denied",
                ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
            )
        assert not provider.commands
        args["input_ids"]["input.csv"] = str(download.artifacts[0].id)
        result = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:authorized",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert result.status == "succeeded", result.error
        assert len(provider.commands) == 1

        with pytest.raises(HTTPException):
            await gateway.execute_tool(
                claim=lane.claim, context=lane.context(), logical_call_id="python:unreviewed-source",
                ref=lane.ref,
                arguments={"source_id": str(download.artifacts[0].id), "input_ids": {},
                           "parameters": {}, "output_media": {}},
                inputs=(), control=InvocationControl(),
            )
        assert len(provider.commands) == 1
    finally:
        lane.db.close()


@pytest.mark.asyncio
async def test_lost_python_command_reply_does_not_redispatch(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute")
    lane = PythonLane(tmp_path)
    provider = Provider()

    def lost(_body):
        raise httpx.ReadError("response lost")

    provider.command_response = lost
    provider.install(monkeypatch)
    args = {"source": "print(42)", "input_ids": {}, "parameters": {}, "output_media": {}}
    try:
        gateway = ToolGateway(lane.context().session_factory)
        first = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:lost",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert first.status != "succeeded"
        replay = await gateway.execute_tool(
            claim=lane.claim, context=lane.context(), logical_call_id="python:lost",
            ref=lane.ref, arguments=args, inputs=(), control=InvocationControl(),
        )
        assert replay.status != "succeeded"
        assert len(provider.commands) == 1
    finally:
        lane.db.close()
