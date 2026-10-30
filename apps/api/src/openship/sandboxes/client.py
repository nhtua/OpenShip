"""Bounded lifecycle calls to a separately configured OpenSandbox Server.

This client does not qualify isolation or grant permission to execute code.
Callers must persist dispatch intent and enforce application authorization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class SandboxProtocolError(RuntimeError):
    """Safe error for a failed or uncertain sandbox lifecycle request."""


@dataclass(frozen=True)
class PythonResult:
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SandboxPage:
    ids: tuple[str, ...]
    has_next: bool


class OpenSandboxClient:
    def __init__(self, http: httpx.AsyncClient, server_url: str, api_key: str, image: str):
        url = urlsplit(server_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("invalid OpenSandbox server URL")
        if not api_key or not api_key.strip():
            raise ValueError("OpenSandbox API key is required")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("OpenSandbox image must be digest pinned")
        self._http = http
        self._base = server_url.rstrip("/")
        self._key = api_key
        self._image = image

    async def create(self, attempt_id: UUID) -> str:
        """Create once; a lost response is uncertain and must be reconciled."""
        if not isinstance(attempt_id, UUID):
            raise TypeError("attempt ID must be a UUID")
        body = {
            "image": {"uri": self._image},
            "entrypoint": ["tail", "-f", "/dev/null"],
            "timeout": 120,
            "resourceLimits": {"cpu": "1", "memory": "512Mi"},
            "metadata": {"openship-attempt": str(attempt_id)},
        }
        try:
            response = await self._http.post(
                self._base + "/v1/sandboxes", json=body,
                headers={"OPEN-SANDBOX-API-KEY": self._key}, timeout=30,
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise SandboxProtocolError("sandbox create outcome is uncertain") from None
        if response.status_code != 202:
            raise SandboxProtocolError("sandbox create failed or outcome is uncertain")
        try:
            sandbox_id = response.json()["id"]
            self._validate_id(sandbox_id)
        except (KeyError, ValueError, TypeError):
            raise SandboxProtocolError("sandbox create outcome is uncertain") from None
        return sandbox_id

    @staticmethod
    def _validate_id(sandbox_id: str) -> None:
        if not isinstance(sandbox_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sandbox_id):
            raise ValueError("invalid sandbox ID")

    async def inspect(self, sandbox_id: str) -> str | None:
        """Return manager lifecycle state, or None for manager-reported absence."""
        self._validate_id(sandbox_id)
        try:
            response = await self._http.get(
                self._base + "/v1/sandboxes/" + sandbox_id,
                headers={"OPEN-SANDBOX-API-KEY": self._key}, timeout=5, follow_redirects=False,
            )
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise SandboxProtocolError("sandbox inspection failed")
            body = response.json()
            state = body["status"]["state"]
            if body["id"] != sandbox_id or not isinstance(state, str) or not state:
                raise ValueError
            return state
        except (httpx.RequestError, ValueError, KeyError, TypeError):
            raise SandboxProtocolError("sandbox inspection failed") from None

    async def list_attempt(self, attempt_id: UUID, *, page: int = 1) -> SandboxPage:
        """List one bounded page scoped to an attempt; absence is not cleanup proof."""
        if not isinstance(attempt_id, UUID):
            raise TypeError("attempt ID must be a UUID")
        if type(page) is not int or not 1 <= page <= 10000:
            raise ValueError("invalid page")
        try:
            response = await self._http.get(
                self._base + "/v1/sandboxes",
                params={"metadata": f"openship-attempt={attempt_id}", "page": page, "pageSize": 20},
                headers={"OPEN-SANDBOX-API-KEY": self._key}, timeout=5, follow_redirects=False,
            )
            if response.status_code != 200:
                raise SandboxProtocolError("sandbox listing failed")
            body = response.json()
            items = body["items"]
            pagination = body["pagination"]
            total = pagination["totalItems"]
            pages = pagination["totalPages"]
            has_next = pagination["hasNextPage"]
            if (not isinstance(items, list) or len(items) > 20
                    or type(total) is not int or total < 0 or type(pages) is not int
                    or pagination["page"] != page or type(pagination["page"]) is not int
                    or pagination["pageSize"] != 20 or type(pagination["pageSize"]) is not int
                    or type(has_next) is not bool or pages != (total + 19) // 20
                    or len(items) != min(20, max(0, total - (page - 1) * 20))
                    or has_next != (page < pages)):
                raise ValueError
            ids = []
            for item in items:
                sandbox_id = item["id"]
                self._validate_id(sandbox_id)
                if item["metadata"]["openship-attempt"] != str(attempt_id) or sandbox_id in ids:
                    raise ValueError
                ids.append(sandbox_id)
            return SandboxPage(ids=tuple(ids), has_next=has_next)
        except (httpx.RequestError, ValueError, KeyError, TypeError, AttributeError):
            raise SandboxProtocolError("sandbox listing failed") from None

    async def wait_ready(self, sandbox_id: str, *, timeout: float = 30) -> None:
        """Wait for a running sandbox and its execd proxy, within a fixed deadline."""
        self._validate_id(sandbox_id)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError("invalid readiness timeout")
        base = self._base + "/v1/sandboxes/" + sandbox_id
        headers = {"OPEN-SANDBOX-API-KEY": self._key}
        try:
            async with asyncio.timeout(timeout):
                while True:
                    response = await self._http.get(base, headers=headers, timeout=5, follow_redirects=False)
                    if response.status_code != 200:
                        raise SandboxProtocolError("sandbox readiness inspection failed")
                    try:
                        body = response.json()
                        state = body["status"]["state"]
                        if body["id"] != sandbox_id or not isinstance(state, str):
                            raise ValueError
                    except (ValueError, KeyError, TypeError):
                        raise SandboxProtocolError("invalid sandbox readiness response") from None
                    if state == "Running":
                        response = await self._http.get(
                            base + "/proxy/44772/ping", headers=headers, timeout=5, follow_redirects=False,
                        )
                        if response.status_code == 200:
                            return
                        if response.status_code not in (502, 503, 504):
                            raise SandboxProtocolError("sandbox proxy readiness failed")
                    elif state not in ("Pending",):
                        raise SandboxProtocolError("sandbox is not ready")
                    await asyncio.sleep(0.25)
        except (httpx.RequestError, TimeoutError):
            raise SandboxProtocolError("sandbox readiness failed or timed out") from None

    async def upload_source(self, sandbox_id: str, source: bytes, sha256: str) -> None:
        """Transfer a bounded source capture; callers must verify materialization."""
        self._validate_id(sandbox_id)
        if not isinstance(source, bytes) or len(source) > 256 * 1024:
            raise ValueError("invalid source size")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or hashlib.sha256(source).hexdigest() != sha256:
            raise ValueError("source digest mismatch")
        metadata = json.dumps({"path": "/code/main.py", "mode": 444})
        try:
            response = await self._http.post(
                self._base + "/v1/sandboxes/" + sandbox_id + "/proxy/44772/files/upload",
                headers={"OPEN-SANDBOX-API-KEY": self._key},
                files=[("metadata", ("metadata.json", metadata, "application/json")),
                       ("file", ("main.py", source, "application/octet-stream"))],
                timeout=30, follow_redirects=False,
            )
        except httpx.RequestError:
            raise SandboxProtocolError("source upload outcome is uncertain") from None
        if response.status_code != 200:
            raise SandboxProtocolError("source upload failed")

    async def verify_source(self, sandbox_id: str, sha256: str, size: int) -> None:
        """Verify materialized source before allowing the workload to start."""
        self._validate_id(sandbox_id)
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 256 * 1024:
            raise ValueError("invalid source reference")
        digest = hashlib.sha256()
        total = 0
        try:
            async with asyncio.timeout(30):
                async with self._http.stream(
                    "GET", self._base + "/v1/sandboxes/" + sandbox_id + "/proxy/44772/files/download",
                    params={"path": "/code/main.py"},
                    headers={"OPEN-SANDBOX-API-KEY": self._key},
                    timeout=30, follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise SandboxProtocolError("source verification failed")
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > size:
                            raise SandboxProtocolError("source verification mismatch")
                        digest.update(chunk)
        except (httpx.RequestError, TimeoutError):
            raise SandboxProtocolError("source verification failed") from None
        if total != size or digest.hexdigest() != sha256:
            raise SandboxProtocolError("source verification mismatch")

    async def destroy(self, sandbox_id: str) -> None:
        """Request deletion; 204 is accepted deletion, not proof of cleanup."""
        self._validate_id(sandbox_id)
        try:
            response = await self._http.delete(
                self._base + "/v1/sandboxes/" + sandbox_id,
                headers={"OPEN-SANDBOX-API-KEY": self._key}, timeout=10,
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise SandboxProtocolError("sandbox deletion outcome is uncertain") from None
        if response.status_code not in (204, 404):
            raise SandboxProtocolError("sandbox deletion failed")

    async def run_python(self, sandbox_id: str) -> PythonResult:
        """Execute fixed argv once. The caller must destroy on any failure."""
        self._validate_id(sandbox_id)
        url = self._base + "/v1/sandboxes/" + sandbox_id + "/proxy/44772/command"
        body = {
            "argv": ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "/usr/local/bin/python3", "-I", "-B", "/code/main.py"],
            "uid": 1000, "gid": 1000, "timeout": 30000, "background": False,
            "envs": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
        }
        data = bytearray()
        try:
            async with asyncio.timeout(40):
                async with self._http.stream(
                    "POST", url, json=body,
                    headers={"OPEN-SANDBOX-API-KEY": self._key},
                    timeout=35, follow_redirects=False,
                ) as response:
                    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("text/event-stream"):
                        raise SandboxProtocolError("sandbox command failed or outcome is uncertain")
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 512 * 1024:
                            raise SandboxProtocolError("sandbox command output limit exceeded")
        except (httpx.RequestError, TimeoutError):
            raise SandboxProtocolError("sandbox command outcome is uncertain") from None

        stdout, stderr = [], []
        complete = False
        try:
            for block in bytes(data).decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                parts = [line[5:].lstrip(" ") for line in block.split("\n") if line.startswith("data:")]
                if parts:
                    payload = "\n".join(parts)
                elif block.lstrip().startswith("{"):
                    # execd also emits bare JSON objects separated by blank lines.
                    payload = block
                else:
                    continue
                event = json.loads(payload)
                if event.get("type") == "error":
                    raise SandboxProtocolError("sandbox program failed")
                if event.get("type") == "execution_complete":
                    exit_code = event.get("exit_code")
                    if type(exit_code) is not int or exit_code != 0:
                        raise SandboxProtocolError("sandbox command outcome is uncertain")
                    complete = True
                if event.get("type") in ("stdout", "stderr"):
                    target = stdout if event["type"] == "stdout" else stderr
                    target.append(event["text"])
        except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
            raise SandboxProtocolError("sandbox command outcome is uncertain") from None
        if not complete:
            raise SandboxProtocolError("sandbox command outcome is uncertain")
        out, err = "".join(stdout), "".join(stderr)
        if len(out.encode()) + len(err.encode()) > 256 * 1024:
            raise SandboxProtocolError("sandbox command output limit exceeded")
        return PythonResult(stdout=out, stderr=err)
