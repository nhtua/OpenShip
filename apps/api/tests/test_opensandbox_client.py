import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from src.openship.sandboxes.client import OpenSandboxClient, SandboxProtocolError


@pytest.mark.asyncio
async def test_create_uses_pinned_profile_and_correlates_attempt():
    attempt = uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/sandboxes"
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "secret"
        body = json.loads(request.content)
        assert body["entrypoint"] == ["tail", "-f", "/dev/null"]
        assert body["timeout"] == 120
        assert body["resourceLimits"] == {"cpu": "1", "memory": "512Mi"}
        assert body["metadata"]["openship-attempt"] == str(attempt)
        assert body["image"]["uri"].endswith("sha256:" + "a" * 64)
        return httpx.Response(202, json={"id": "sandbox-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        assert await client.create(attempt) == "sandbox-1"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_create_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError, match="uncertain"):
            await client.create(uuid4())
    assert len(calls) == 1


def test_rejects_unpinned_image():
    with pytest.raises(ValueError):
        OpenSandboxClient(httpx.AsyncClient(), "http://sandbox:8080", "secret", "python:latest")


@pytest.mark.asyncio
async def test_delete_absent_sandbox_is_idempotent():
    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/v1/sandboxes/sandbox-1"
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        await client.destroy("sandbox-1")


@pytest.mark.asyncio
async def test_command_requires_explicit_zero_exit_and_fixed_argv():
    def handler(request):
        assert request.url.path == "/v1/sandboxes/sandbox-1/proxy/44772/command"
        body = json.loads(request.content)
        assert body["argv"] == ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "/usr/local/bin/python3", "-I", "-B", "/code/main.py"]
        assert body["uid"] == body["gid"] == 1000
        assert body["timeout"] == 30000
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
            b'data: {"type":"stdout","text":"hello"}\n\n'
            b'data: {"type":"execution_complete","exit_code":0}\n\n'
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        result = await client.run_python("sandbox-1")
        assert result.stdout == "hello"
        assert result.stderr == ""


@pytest.mark.asyncio
async def test_missing_completion_is_not_success():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b'data: {"type":"stdout","text":"ok"}\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError, match="uncertain"):
            await client.run_python("sandbox-1")


@pytest.mark.asyncio
async def test_upload_source_checks_digest_before_transfer():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/sandboxes/sandbox-1/proxy/44772/files/upload"
        assert b'/code/main.py' in request.content
        assert b'print(1)' in request.content
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(ValueError):
            await client.upload_source("sandbox-1", b"print(1)", "0" * 64)
        assert requests == []
        await client.upload_source("sandbox-1", b"print(1)", hashlib.sha256(b"print(1)").hexdigest())
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_verify_source_rejects_changed_materialized_bytes():
    def handler(request):
        assert request.url.path == "/v1/sandboxes/sandbox-1/proxy/44772/files/download"
        assert request.url.params["path"] == "/code/main.py"
        return httpx.Response(200, content=b"changed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError, match="mismatch"):
            await client.verify_source("sandbox-1", hashlib.sha256(b"expected").hexdigest(), 8)


@pytest.mark.asyncio
async def test_verify_source_accepts_matching_bounded_bytes():
    def handler(request):
        return httpx.Response(200, content=b"print(1)")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        await client.verify_source("sandbox-1", hashlib.sha256(b"print(1)").hexdigest(), 8)
