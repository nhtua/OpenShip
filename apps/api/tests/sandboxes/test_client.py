import asyncio
import gzip
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.sandboxes.client import (
    OpenSandboxClient,
    SandboxUnavailable,
)
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    InputFile,
    OutputSpec,
    PreparedFiles,
    RuntimeRequirements,
    SandboxHandle,
)


def artifact(data: bytes) -> ArtifactRef:
    return ArtifactRef(
        id=uuid4(), sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), media_type="text/plain",
    )


def requirements() -> RuntimeRequirements:
    return RuntimeRequirements(
        provider_id="local", profile_id="python311", image="python:test",
        dependency_identity="stdlib", cpu="1", memory="512Mi",
    )


def future_expiry() -> str:
    return (datetime.now(UTC) + timedelta(minutes=30)).isoformat()


@pytest.mark.asyncio
async def test_close_preserves_caller_owned_http_client():
    http = httpx.AsyncClient()
    await OpenSandboxClient("http://sandbox.test", "test-key", http=http).aclose()
    assert not http.is_closed
    await http.aclose()


@pytest.mark.asyncio
async def test_close_closes_invocation_owned_http_client():
    http = httpx.AsyncClient()
    await OpenSandboxClient(
        "http://sandbox.test", "test-key", http=http, owns_http=True
    ).aclose()
    assert http.is_closed


def request(**overrides):
    values = {
        "root_run_id": uuid4(), "operation_id": uuid4(), "requirements": requirements(),
        "source": artifact(b"print('hello')"), "inputs": (), "parameters": {}, "outputs": (),
    }
    values.update(overrides)
    return ExecutionRequest(**values)


@pytest.mark.asyncio
async def test_create_requests_provider_owned_thirty_minute_sandbox():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(202, json={
            "id": "sbx-1", "status": {"state": "Running"},
            "expiresAt": future_expiry(),
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        handle = await client.create(requirements())
    body = json.loads(calls[0].content)
    assert body["timeout"] == 1800
    assert body["resourceLimits"] == {"cpu": "1", "memory": "512Mi"}
    assert calls[0].headers["OPEN-SANDBOX-API-KEY"] == "test-key"
    assert handle.sandbox_id == "sbx-1"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_lost_response_is_interrupted_without_retry():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("lost", request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        with pytest.raises(SandboxUnavailable, match="interrupted"):
            await client.create(requirements())
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, {}, {"state": "Pending"}, {"state": "Failed"}])
async def test_create_rejects_missing_or_nonrunning_provider_status(status):
    response = {"id": "sbx-1", "expiresAt": future_expiry()}
    if status is not None:
        response["status"] = status
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(202, json=response)
    )) as http:
        with pytest.raises(SandboxUnavailable, match="interrupted"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).create(requirements())


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox_id, expires", [
    ("../unsafe", "2026-11-03T13:00:00Z"),
    ("sbx-1", "not-a-date"),
    ("sbx-1", "2026-11-03T13:00:00"),
])
async def test_create_rejects_malformed_identity_or_expiration(sandbox_id, expires):
    body = {"id": sandbox_id, "expiresAt": expires, "status": {"state": "Running"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(202, json=body))) as http:
        with pytest.raises(SandboxUnavailable, match="interrupted"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).create(requirements())


@pytest.mark.asyncio
async def test_create_provider_failure_does_not_expose_response_or_key():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(500, text="private-provider-detail test-key")
    )) as http:
        with pytest.raises(SandboxUnavailable) as error:
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).create(requirements())
    assert "test-key" not in str(error.value)
    assert "private-provider-detail" not in str(error.value)


@pytest.mark.parametrize("alias", ["../x", "/tmp/x", "a/b", ".", "a\\b"])
def test_rejects_unsafe_input_aliases(alias):
    with pytest.raises(ValidationError):
        InputFile(alias=alias, artifact=artifact(b"x"))


def test_rejects_oversized_parameters_and_source():
    with pytest.raises(ValidationError):
        request(parameters={"value": "x" * 16384})
    with pytest.raises(ValidationError):
        request(source=artifact(b"x" * (256 * 1024 + 1)))


def test_source_outputs_require_an_expectation():
    with pytest.raises(ValidationError):
        OutputSpec(alias="out.py", media_type="text/plain", kind="source")


def test_rejects_more_than_eight_input_or_output_files():
    inputs = tuple(InputFile(alias=f"in-{index}", artifact=artifact(b"x")) for index in range(9))
    outputs = tuple(OutputSpec(alias=f"out-{index}", media_type="text/plain", kind="data") for index in range(9))
    with pytest.raises(ValidationError):
        request(inputs=inputs)
    with pytest.raises(ValidationError):
        request(outputs=outputs)


def test_rejects_per_input_and_total_input_limits():
    large_ref = artifact(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValidationError):
        request(inputs=(InputFile(alias="large", artifact=large_ref),))
    at_limit = artifact(b"x" * (8 * 1024 * 1024))
    with pytest.raises(ValidationError):
        request(inputs=(InputFile(alias="first", artifact=at_limit),
                        InputFile(alias="second", artifact=at_limit),
                        InputFile(alias="third", artifact=artifact(b"x"))))


def test_rejects_duplicate_aliases_within_each_file_set():
    item = InputFile(alias="same", artifact=artifact(b"x"))
    output = OutputSpec(alias="same", media_type="text/plain", kind="data")
    with pytest.raises(ValidationError):
        request(inputs=(item, item))
    with pytest.raises(ValidationError):
        request(outputs=(output, output))


@pytest.mark.asyncio
@pytest.mark.parametrize("status, absent", [(404, True), (503, False)])
async def test_inspection_distinguishes_absence_from_unavailability(status, absent):
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=requirements().key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(status)
    )) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        if absent:
            assert await client.inspect(handle) is None
        else:
            with pytest.raises(SandboxUnavailable):
                await client.inspect(handle)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {},
    {"id": "another", "status": {"state": "Running"}, "expiresAt": "2026-11-03T13:00:00Z"},
    {"id": "sbx-1", "status": {"state": "Failed"}, "expiresAt": "2026-11-03T13:00:00Z"},
    {"id": "sbx-1", "status": {"state": "Running"}, "expiresAt": "2026-11-03T13:00:00"},
])
async def test_inspection_rejects_malformed_or_nonrunning_responses(body):
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=requirements().key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as http:
        with pytest.raises(SandboxUnavailable, match="inspection"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).inspect(handle)


@pytest.mark.asyncio
async def test_inspection_preserves_identity_and_uses_provider_expiration():
    expiry = datetime.now(UTC) + timedelta(minutes=30)
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=requirements().key,
                           expires_at=expiry)
    body = {"id": "sbx-1", "status": {"state": "Running"}, "expiresAt": expiry.isoformat()}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).inspect(handle)
    assert result is not None
    assert result.sandbox_id == handle.sandbox_id
    assert result.requirements_key == handle.requirements_key
    assert result.expires_at == expiry


@pytest.mark.asyncio
async def test_inspection_treats_expired_running_handle_as_absent():
    expired = datetime.now(UTC) - timedelta(seconds=1)
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=requirements().key, expires_at=expired)
    body = {"id": "sbx-1", "status": {"state": "Running"},
            "expiresAt": expired.isoformat()}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=body)
    )) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).inspect(handle)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [
    PreparedFiles(source=b"wrong", inputs={"log.txt": b"data"}),
    PreparedFiles(source=b"print('hello')", inputs={"log.txt": b"wrong"}),
    PreparedFiles(source=b"print('hello')", inputs={}),
    PreparedFiles(source=b"print('hello')", inputs={"log.txt": b"data", "extra": b"x"}),
])
async def test_execute_rejects_unverified_prepared_files_without_provider_calls(files):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500)

    req = request(inputs=(InputFile(alias="log.txt", artifact=artifact(b"data")),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        with pytest.raises(SandboxUnavailable, match="prepared files"):
            await client.execute(handle, req, files, InvocationControl())
    assert calls == []


@pytest.mark.asyncio
async def test_execute_transfers_current_files_and_runs_fixed_python_argv():
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"stdout","text":"hello\\n"}\n\n'
                                  '{"type":"execution_complete","exit_code":0}\n\n')
        return httpx.Response(200, json=[])

    req = request(inputs=(InputFile(alias="log.txt", artifact=artifact(b"data")),),
                  parameters={"flag": True})
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    files = PreparedFiles(source=b"print('hello')", inputs={"log.txt": b"data"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, files, InvocationControl()
        )
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    uploads = [call for call in calls if call.url.path.endswith("/files/upload")]
    assert len(uploads) == 3
    assert all(b'name="metadata"; filename=' in call.content for call in uploads)
    assert b"print('hello')" in b"".join(call.content for call in uploads)
    assert b"data" in b"".join(call.content for call in uploads)
    command = next(call for call in calls if call.url.path.endswith("/command"))
    body = json.loads(command.content)
    assert body["timeout"] == 30000
    assert body["argv"][0] == "python"
    assert str(req.operation_id) in body["argv"][1]
    assert "command" not in body


@pytest.mark.asyncio
async def test_execute_rejects_undeclared_output_before_returning_success():
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            return httpx.Response(200, json=[{"path": "/outputs/unexpected", "type": "file"}])
        return httpx.Response(200, json={})

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        with pytest.raises(SandboxUnavailable):
            await client.execute(handle, req, PreparedFiles(source=b"print('hello')", inputs={}),
                                 InvocationControl())


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [
    '{"type":"execution_complete"}\n\n',
    '{"type":"execution_complete","exit_code":1}\n\n',
    '{"type":"stdout","text":"partial"}\n\n',
    '{"type":"error","error":{"exit_code":1}}\n\n',
    '[1,2]\n\n',
    '{"type":"stdout","text":12}\n\n',
])
async def test_execute_rejects_missing_or_nonzero_completion_without_retry(stream):
    commands = []

    def handler(req):
        if req.url.path.endswith("/command"):
            commands.append(req)
            return httpx.Response(200, text=stream)
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_missing_completion_keeps_command_handle_uncertain():
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"stdout","text":"working"}\n')
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(
        provider_id="local",
        sandbox_id="sbx-1",
        requirements_key=req.requirements.key,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        with pytest.raises(SandboxUnavailable, match="interrupted"):
            await client.execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}),
                InvocationControl(),
            )
    assert client.command_uncertain


@pytest.mark.asyncio
async def test_execute_collects_declared_regular_output():
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            output_path = req.url.params["path"] + "/report.txt"
            return httpx.Response(200, json=[{"path": output_path, "type": "file", "size": 6}])
        if req.url.path.endswith("/files/download"):
            return httpx.Response(200, content=b"report")
        return httpx.Response(200, json={})

    req = request(outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
        )
    assert result.outputs == {"report.txt": b"report"}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "extra", "symlink", "directory", "too_large", "stream_too_large"])
async def test_execute_rejects_invalid_or_oversized_declared_output(case):
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            path = req.url.params["path"] + "/report.txt"
            entry = {"path": path, "type": "file", "size": 3}
            if case == "missing":
                return httpx.Response(200, json=[])
            if case == "extra":
                entry["path"] += ".unexpected"
            if case in ("symlink", "directory"):
                entry["type"] = case
            if case == "too_large":
                entry["size"] = 4 * 1024 * 1024 + 1
            return httpx.Response(200, json=[entry])
        if req.url.path.endswith("/files/download"):
            return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        return httpx.Response(200, json={})

    req = request(outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1",
                           requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )


@pytest.mark.asyncio
async def test_execute_waits_for_execd_readiness_before_upload():
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.url.path.endswith("/ping"):
            return httpx.Response(502 if sum(p.endswith("/ping") for p in paths) == 1 else 200)
        if req.url.path.endswith("/directories") and sum(p.endswith("/ping") for p in paths) < 2:
            return httpx.Response(502)
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
        )
    assert result.exit_code == 0
    assert sum(path.endswith("/command") for path in paths) == 1


@pytest.mark.asyncio
async def test_cancellation_interrupts_waiting_for_upload_without_dispatch():
    uploading = asyncio.Event()
    stopped = False
    commands = []

    async def handler(req):
        if req.url.path.endswith("/files/upload"):
            uploading.set()
            await asyncio.Event().wait()
        if req.url.path.endswith("/command"):
            commands.append(req)
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        task = asyncio.create_task(OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}),
            InvocationControl(stop=lambda: stopped),
        ))
        await asyncio.wait_for(uploading.wait(), timeout=2)
        stopped = True
        with pytest.raises(ProviderError, match="cancelled"):
            await asyncio.wait_for(task, timeout=1)
    assert commands == []


@pytest.mark.asyncio
async def test_mutated_parameters_cannot_bypass_pretransfer_limit():
    calls = []
    req = request()
    req.parameters["large"] = "x" * 16384
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda call: calls.append(call) or httpx.Response(200, json=[])
    )) as http:
        with pytest.raises(SandboxUnavailable, match="parameters"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )
    assert calls == []


@pytest.mark.asyncio
async def test_execute_rejects_outputs_exceeding_aggregate_byte_limit():
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            root = req.url.params["path"]
            return httpx.Response(200, json=[{"path": f"{root}/{name}", "type": "file",
                                            "size": 1 if name == "c" else 4 * 1024 * 1024}
                                            for name in ("a", "b", "c")])
        if req.url.path.endswith("/files/download"):
            size = 1 if req.url.params["path"].endswith("/c") else 4 * 1024 * 1024
            return httpx.Response(200, content=b"x" * size)
        return httpx.Response(200, json=[])

    req = request(outputs=tuple(OutputSpec(alias=name, media_type="text/plain", kind="data")
                                for name in ("a", "b", "c")))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable, match="output exceeded"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_path", ["/command", "/files/download"])
async def test_cancellation_during_execution_or_download_rejects_success(blocked_path):
    reached = asyncio.Event()
    stopped = False
    commands = []

    async def handler(req):
        if req.url.path.endswith("/command"):
            commands.append(req)
        if req.url.path.endswith(blocked_path):
            reached.set()
            await asyncio.Event().wait()
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            return httpx.Response(200, json=[{"path": req.url.params["path"] + "/report.txt",
                                            "type": "file", "size": 1}])
        return httpx.Response(200, json=[])

    req = request(outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        task = asyncio.create_task(client.execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl(stop=lambda: stopped)
        ))
        await asyncio.wait_for(reached.wait(), timeout=2)
        stopped = True
        with pytest.raises(ProviderError, match="cancelled"):
            await asyncio.wait_for(task, timeout=1)
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_invocation_deadline_interrupts_pending_command():
    async def handler(req):
        if req.url.path.endswith("/command"):
            await asyncio.Event().wait()
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable, match="deadline"):
            await asyncio.wait_for(OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}),
                InvocationControl(deadline=time.monotonic() + 0.1),
            ), timeout=1)


@pytest.mark.asyncio
async def test_combined_diagnostics_over_limit_fail_without_result():
    events = (json.dumps({"type": "stdout", "text": "a" * (128 * 1024)}) + "\n\n" +
              json.dumps({"type": "stderr", "text": "b" * (128 * 1024 + 1)}) + "\n\n" +
              '{"type":"execution_complete","exit_code":0}\n\n')

    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text=events)
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable, match="diagnostics"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )


@pytest.mark.asyncio
async def test_diagnostics_limit_counts_decoded_bytes_not_json_escape_bytes():
    text = "\0" * (128 * 1024)
    events = json.dumps({"type": "stdout", "text": text}) + '\n\n{"type":"execution_complete","exit_code":0}\n\n'

    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text=events)
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
        )
    assert result.stdout == text


@pytest.mark.asyncio
async def test_prepared_input_mutation_cannot_change_bytes_after_verification():
    files = PreparedFiles(source=b"print('hello')", inputs={"log.txt": b"original"})
    uploads = []

    def handler(req):
        if req.url.path.endswith("/ping"):
            files.inputs["log.txt"] = b"tampered"
        if req.url.path.endswith("/files/upload"):
            uploads.append(req.content)
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        return httpx.Response(200, json=[])

    req = request(inputs=(InputFile(alias="log.txt", artifact=artifact(b"original")),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
            handle, req, files, InvocationControl()
        )
    assert b"original" in b"".join(uploads)
    assert b"tampered" not in b"".join(uploads)


@pytest.mark.asyncio
async def test_lost_command_reply_does_not_retry_or_delete_sandbox():
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/command"):
            raise httpx.ReadTimeout("reply lost", request=req)
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable, match="interrupted"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )
    assert sum(call.url.path.endswith("/command") for call in calls) == 1
    assert all(call.method != "DELETE" for call in calls)
    assert all("renew" not in call.url.path for call in calls)


@pytest.mark.asyncio
async def test_cancelled_command_cannot_return_late_success():
    waiting = asyncio.Event()
    release = asyncio.Event()
    stopped = False

    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            waiting.set()
            await release.wait()
            yield b'{"type":"execution_complete","exit_code":0}\n\n'

    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, stream=DelayedStream())
        return httpx.Response(200, json=[])

    req = request()
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient("http://sandbox.test", "test-key", http=http)
        task = asyncio.create_task(client.execute(
            handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl(stop=lambda: stopped)
        ))
        await asyncio.wait_for(waiting.wait(), timeout=2)
        stopped = True
        with pytest.raises(ProviderError, match="cancelled"):
            await asyncio.wait_for(task, timeout=1)
        assert client.command_uncertain
        release.set()


@pytest.mark.asyncio
async def test_download_rejects_compressed_transport_before_decompression():
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            return httpx.Response(200, json=[{"path": req.url.params["path"] + "/report.txt",
                                            "type": "file", "size": 5}])
        if req.url.path.endswith("/files/download"):
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"hello"))
        return httpx.Response(200, json=[])

    req = request(outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable, match="encoding"):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content", [(206, b"abc"), (200, b"abc")])
async def test_output_download_rejects_partial_or_size_mismatch(status, content):
    def handler(req):
        if req.url.path.endswith("/command"):
            return httpx.Response(200, text='{"type":"execution_complete","exit_code":0}\n\n')
        if req.url.path.endswith("/directories/list"):
            return httpx.Response(200, json=[{"path": req.url.params["path"] + "/report.txt",
                                            "type": "file", "size": 6}])
        if req.url.path.endswith("/files/download"):
            return httpx.Response(status, content=content)
        return httpx.Response(200, json=[])

    req = request(outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
    handle = SandboxHandle(provider_id="local", sandbox_id="sbx-1", requirements_key=req.requirements.key,
                           expires_at=datetime(2026, 11, 3, 13, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxUnavailable):
            await OpenSandboxClient("http://sandbox.test", "test-key", http=http).execute(
                handle, req, PreparedFiles(source=b"print('hello')", inputs={}), InvocationControl()
            )
