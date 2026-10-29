"""Exercise the portable probe through the real OpenSandbox HTTP adapter."""

import json
from email.parser import BytesParser
from email.policy import default
from uuid import uuid4

import httpx
import pytest

from scripts.qualify_opensandbox import qualify
from src.openship.sandboxes.client import SandboxProtocolError


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "delayed_cleanup", "readback", "legacy_completion", "root_identity", "cleanup"])
async def test_qualification_verifies_protocol_and_cleanup(fault):
    calls = []
    source = None
    deleted = False
    deletion_polls = 0

    def handler(request):
        nonlocal source, deleted, deletion_polls
        calls.append((request.method, request.url.path))
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "fixture-key"
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(202, json={"id": "fixture"})
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(204)
        if request.url.path.endswith("/ping"):
            return httpx.Response(200)
        if request.url.path.endswith("/files/upload"):
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content,
            )
            parts = list(message.iter_parts())
            assert parts[0].get_filename() == "metadata.json"
            assert json.loads(parts[0].get_payload(decode=True))["path"] == "/code/main.py"
            source = parts[1].get_payload(decode=True)
            return httpx.Response(200)
        if request.url.path.endswith("/files/download"):
            return httpx.Response(200, content=b"changed" if fault == "readback" else source)
        if request.url.path.endswith("/command"):
            body = json.loads(request.content)
            assert "command" not in body
            assert body["uid"] == body["gid"] == 1000
            stdout = "openship-compatibility 0 0\n" if fault == "root_identity" else "openship-compatibility 1000 1000\n"
            completion = {"type": "execution_complete"}
            if fault != "legacy_completion":
                completion["exit_code"] = 0
            events = [{"type": "stdout", "text": stdout},
                      {"type": "stderr", "text": "openship-stderr\n"}, completion]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="\n\n".join(json.dumps(event) for event in events) + "\n\n")
        if deleted:
            deletion_polls += 1
            if fault == "delayed_cleanup" and deletion_polls == 1:
                return httpx.Response(200)
            return httpx.Response(503 if fault == "cleanup" else 404)
        return httpx.Response(200, json={"id": "fixture", "status": {"state": "Running"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        kwargs = {"server_url": "http://manager:8080", "api_key": "fixture-key",
                  "image": "python@sha256:" + "a" * 64, "attempt_id": uuid4()}
        if fault not in (None, "delayed_cleanup"):
            with pytest.raises(SandboxProtocolError):
                await qualify(http, **kwargs)
        else:
            receipt = await qualify(http, **kwargs)
            assert receipt["protocol_compatible"] is True
            assert receipt["isolation_qualified"] is False
            assert receipt["manager_deletion_confirmed"] is True
    assert calls.count(("DELETE", "/v1/sandboxes/fixture")) == 1
    command_calls = calls.count(("POST", "/v1/sandboxes/fixture/proxy/44772/command"))
    assert command_calls == (0 if fault == "readback" else 1)
    assert calls[-1] == ("GET", "/v1/sandboxes/fixture")


@pytest.mark.asyncio
async def test_qualification_does_not_retry_uncertain_creation():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SandboxProtocolError, match="uncertain"):
            await qualify(http, server_url="http://manager:8080", api_key="fixture-key",
                          image="python@sha256:" + "a" * 64, attempt_id=uuid4())
    assert len(calls) == 1
