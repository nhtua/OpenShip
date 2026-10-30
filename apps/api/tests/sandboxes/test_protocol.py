"""Explicitly selected, real OpenSandbox protocol checks with trusted code."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from src.openship.sandboxes.client import OpenSandboxClient, SandboxProtocolError

pytestmark = [pytest.mark.sandbox, pytest.mark.asyncio]


class SandboxSettings(tuple):
    def __repr__(self):
        return "SandboxSettings(<redacted>)"


@pytest_asyncio.fixture
async def settings():
    names = ("OPENSHIP_SANDBOX_SERVER_URL", "OPENSHIP_SANDBOX_API_KEY", "OPENSHIP_SANDBOX_PYTHON_IMAGE")
    values = tuple(os.environ.get(name, "") for name in names)
    if not all(values):
        pytest.fail("real sandbox protocol tests require " + ", ".join(names))
    try:
        async with asyncio.timeout(30), httpx.AsyncClient(trust_env=False) as http:
            while True:
                try:
                    response = await http.get(values[0].rstrip("/") + "/health", timeout=2, follow_redirects=False)
                    if response.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.25)
    except TimeoutError:
        pytest.fail("sandbox manager did not become ready")
    return SandboxSettings(values)


@asynccontextmanager
async def sandbox(client):
    attempt = uuid4()
    sandbox_id = await client.create(attempt)
    try:
        yield sandbox_id, attempt
    finally:
        await client.destroy(sandbox_id)
        assert await client.inspect(sandbox_id) is None


async def test_create_waits_for_ready_before_transfer(settings):
    async with httpx.AsyncClient(trust_env=False) as http:
        client = OpenSandboxClient(http, *settings)
        async with sandbox(client) as (sandbox_id, attempt):
            await client.wait_ready(sandbox_id)
            assert await client.inspect(sandbox_id) == "Running"
            assert sandbox_id in (await client.list_attempt(attempt)).ids
            source = b'print("trusted-source")\n'
            digest = hashlib.sha256(source).hexdigest()
            await client.upload_source(sandbox_id, source, digest)
            await client.verify_source(sandbox_id, digest, len(source))
            assert (await client.run_python(sandbox_id)).stdout == "trusted-source\n"


async def test_proxy_requires_authentication(settings):
    async with httpx.AsyncClient(trust_env=False) as http:
        client = OpenSandboxClient(http, *settings)
        async with sandbox(client) as (sandbox_id, _):
            await client.wait_ready(sandbox_id)
            url = settings[0].rstrip("/") + "/v1/sandboxes/" + sandbox_id + "/proxy/44772/ping"
            for headers in ({}, {"OPEN-SANDBOX-API-KEY": "invalid-fixture-key"}):
                lifecycle = await http.get(settings[0].rstrip("/") + "/v1/sandboxes", headers=headers)
                assert lifecycle.status_code in (401, 403)
                response = await http.get(url, headers=headers, follow_redirects=False)
                assert response.status_code in (401, 403)
                ws_url = url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
                with pytest.raises(InvalidStatus) as error:
                    async with connect(ws_url, additional_headers=headers, proxy=None, open_timeout=5):
                        pytest.fail("unauthenticated WebSocket proxy was accepted")
                assert error.value.response.status_code == 403


async def test_unexpected_endpoint_is_refused(settings):
    async with httpx.AsyncClient(trust_env=False) as http:
        with pytest.raises(ValueError):
            OpenSandboxClient(http, settings[0].rstrip("/") + "/unexpected", *settings[1:])


async def test_fixed_argv_uses_nonroot_clean_environment(settings):
    source = (b'import json, os\nprint(json.dumps({"uid":os.getuid(),"gid":os.getgid(),'
              b'"env":sorted(os.environ)}))\n')
    async with httpx.AsyncClient(trust_env=False) as http:
        client = OpenSandboxClient(http, *settings)
        async with sandbox(client) as (sandbox_id, _):
            await client.wait_ready(sandbox_id)
            digest = hashlib.sha256(source).hexdigest()
            await client.upload_source(sandbox_id, source, digest)
            await client.verify_source(sandbox_id, digest, len(source))
            result = await client.run_python(sandbox_id)
            value = json.loads(result.stdout)
            assert value["uid"] == value["gid"] == 1000
            assert set(value["env"]) <= {"PATH", "LC_CTYPE"}
            assert result.stderr == ""


async def test_command_requires_explicit_zero_exit_completion(settings):
    source = b'raise SystemExit(7)\n'
    async with httpx.AsyncClient(trust_env=False) as http:
        client = OpenSandboxClient(http, *settings)
        async with sandbox(client) as (sandbox_id, _):
            await client.wait_ready(sandbox_id)
            digest = hashlib.sha256(source).hexdigest()
            await client.upload_source(sandbox_id, source, digest)
            await client.verify_source(sandbox_id, digest, len(source))
            with pytest.raises(SandboxProtocolError):
                await client.run_python(sandbox_id)
