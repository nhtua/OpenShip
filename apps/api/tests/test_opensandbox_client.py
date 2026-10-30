import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from src.openship.sandboxes.client import OpenSandboxClient, SandboxProtocolError


@pytest.mark.asyncio
async def test_inspect_returns_state_and_absence_without_following_redirects():
    def handler(request):
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "secret"
        if request.url.path.endswith("missing"):
            return httpx.Response(404)
        return httpx.Response(200, json={"id": "sandbox-1", "status": {"state": "Running"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        assert await client.inspect("sandbox-1") == "Running"
        assert await client.inspect("missing") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "http://elsewhere/"}),
    httpx.Response(200, json={"id": "wrong", "status": {"state": "Running"}}),
    httpx.Response(200, json={"id": "sandbox-1", "status": {"state": None}}),
    httpx.Response(500),
])
async def test_inspect_rejects_invalid_or_redirected_response(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError):
            await client.inspect("sandbox-1")


@pytest.mark.asyncio
async def test_list_attempt_is_scoped_and_exposes_pagination():
    attempt = uuid4()

    def handler(request):
        assert request.url.params["metadata"] == f"openship-attempt={attempt}"
        assert request.url.params["page"] == "2"
        assert request.url.params["pageSize"] == "20"
        return httpx.Response(200, json={
            "items": [{"id": "sandbox-1", "metadata": {"openship-attempt": str(attempt)},
                       "status": {"state": "Running"}}],
            "pagination": {"page": 2, "pageSize": 20, "totalItems": 21,
                           "totalPages": 2, "hasNextPage": False},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        page = await client.list_attempt(attempt, page=2)
        assert page.ids == ("sandbox-1",)
        assert page.has_next is False


@pytest.mark.asyncio
async def test_list_attempt_rejects_unscoped_or_incomplete_results():
    attempt = uuid4()
    for body in (
        {"items": [{"id": "wrong", "metadata": {"openship-attempt": str(uuid4())}}],
         "pagination": {"page": 1, "pageSize": 20, "totalItems": 1, "totalPages": 1, "hasNextPage": False}},
        {"items": [], "pagination": {"page": 1, "hasNextPage": "false"}},
        {"items": [], "pagination": {"page": 1, "pageSize": 20, "totalItems": 1,
                                     "totalPages": 1, "hasNextPage": False}},
        {"items": [{"id": "same", "metadata": {"openship-attempt": str(attempt)}}] * 2,
         "pagination": {"page": 1, "pageSize": 20, "totalItems": 2, "totalPages": 1, "hasNextPage": False}},
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request, body=body: httpx.Response(200, json=body))) as http:
            client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
            with pytest.raises(SandboxProtocolError):
                await client.list_attempt(attempt)


@pytest.mark.asyncio
async def test_wait_ready_checks_lifecycle_and_authenticated_proxy():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "secret"
        if request.url.path.endswith("/proxy/44772/ping"):
            return httpx.Response(200)
        return httpx.Response(200, json={"id": "sandbox-1", "status": {"state": "Running"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        await client.wait_ready("sandbox-1", timeout=1)
    assert calls == ["/v1/sandboxes/sandbox-1", "/v1/sandboxes/sandbox-1/proxy/44772/ping"]


@pytest.mark.asyncio
async def test_wait_ready_rejects_terminal_state_and_redirect():
    for response in (httpx.Response(200, json={"id": "sandbox-1", "status": {"state": "Failed"}}),
                     httpx.Response(302, headers={"location": "https://unexpected.example/"})):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request, response=response: response)) as http:
            client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
            with pytest.raises(SandboxProtocolError):
                await client.wait_ready("sandbox-1", timeout=1)


@pytest.mark.asyncio
async def test_wait_ready_times_out_if_proxy_never_becomes_available():
    def handler(request):
        if request.url.path.endswith("/ping"):
            return httpx.Response(503)
        return httpx.Response(200, json={"id": "sandbox-1", "status": {"state": "Running"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError, match="timed out"):
            await client.wait_ready("sandbox-1", timeout=0.05)


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
async def test_command_accepts_execd_json_records_with_explicit_exit_code():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
            b'{"type":"stdout","text":"hello"}\n\n'
            b'{"type":"execution_complete","exit_code":0}\n\n'
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        result = await client.run_python("sandbox-1")
    assert result.stdout == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [b"", b"data: "])
@pytest.mark.parametrize("exit_code", [None, True, False, 1, -1, 0.0, "0"])
async def test_command_rejects_non_integer_or_unsuccessful_exit_code(prefix, exit_code):
    completion = json.dumps({"type": "execution_complete", "exit_code": exit_code}).encode()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=prefix + completion + b"\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenSandboxClient(http, "http://sandbox:8080", "secret", "image@sha256:" + "a" * 64)
        with pytest.raises(SandboxProtocolError, match="uncertain"):
            await client.run_python("sandbox-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    b'{"type":"execution_complete","execution_time":1}\n\n',
    b'{"type":"stdout","text":"partial"}\n\n',
    b'{"type":"execution_complete","exit_code":0}\n\n{malformed}\n\n',
])
async def test_command_rejects_incomplete_or_invalid_json_records(payload):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

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
        assert b'name="metadata"; filename="metadata.json"' in request.content
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
