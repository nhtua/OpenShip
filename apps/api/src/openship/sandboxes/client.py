"""Authenticated OpenSandbox lifecycle request boundary."""

import asyncio
import hashlib
import json
from datetime import datetime
from uuid import uuid4

import httpx

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import canonical_json
from src.openship.providers.control import InvocationControl

from .contracts import (
    ExecutionRequest,
    ExecutionResult,
    PreparedFiles,
    RuntimeRequirements,
    SandboxHandle,
)


class SandboxUnavailable(Exception):
    """Safe provider failure; never includes credentials or provider bodies."""


class OpenSandboxClient:
    def __init__(self, base_url: str, api_key: str, *, http: httpx.AsyncClient):
        parsed = httpx.URL(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
            raise ValueError("invalid OpenSandbox endpoint")
        if not api_key.strip():
            raise ValueError("OpenSandbox key required")
        self.base_url = str(parsed).rstrip("/")
        self._headers = {"OPEN-SANDBOX-API-KEY": api_key, "Accept-Encoding": "identity"}
        self._http = http

    async def create(self, requirements: RuntimeRequirements) -> SandboxHandle:
        body = {
            "image": {"uri": requirements.image},
            "entrypoint": ["/bin/sleep", "infinity"],
            "timeout": 1800,
            "resourceLimits": {"cpu": requirements.cpu, "memory": requirements.memory},
        }
        try:
            response = await self._http.post(
                f"{self.base_url}/v1/sandboxes", json=body, headers=self._headers,
                follow_redirects=False, timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            expires = datetime.fromisoformat(data["expiresAt"])
            if expires.tzinfo is None:
                raise ValueError("missing timezone")
            return SandboxHandle(provider_id=requirements.provider_id, sandbox_id=data["id"],
                                 requirements_key=requirements.key, expires_at=expires)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise SandboxUnavailable("sandbox create interrupted") from None

    async def inspect(self, handle: SandboxHandle) -> SandboxHandle | None:
        try:
            response = await self._http.get(
                f"{self.base_url}/v1/sandboxes/{handle.sandbox_id}",
                headers=self._headers, follow_redirects=False, timeout=30,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            if data["id"] != handle.sandbox_id or data["status"]["state"] != "Running":
                raise ValueError("sandbox not running")
            expires = datetime.fromisoformat(data["expiresAt"])
            if expires.tzinfo is None:
                raise ValueError("missing timezone")
            return handle.model_copy(update={"expires_at": expires})
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise SandboxUnavailable("sandbox inspection unavailable") from None

    async def execute(
        self, handle: SandboxHandle, request: ExecutionRequest,
        files: PreparedFiles, control: InvocationControl,
    ) -> ExecutionResult:
        control.raise_if_stopped()
        task = asyncio.create_task(self._execute(handle, request, files, control))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=min(0.1, control.remaining_seconds()))
                control.raise_if_stopped()
                if control.remaining_seconds() <= 0:
                    raise SandboxUnavailable("sandbox invocation deadline exceeded")
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _execute(
        self, handle: SandboxHandle, request: ExecutionRequest,
        files: PreparedFiles, control: InvocationControl,
    ) -> ExecutionResult:
        control.raise_if_stopped()

        def matches(data: bytes, ref: ArtifactRef) -> bool:
            return len(data) == ref.size_bytes and hashlib.sha256(data).hexdigest() == ref.sha256

        inputs = files.inputs.copy()
        if (not matches(files.source, request.source)
                or set(inputs) != {item.alias for item in request.inputs}
                or any(not matches(inputs[item.alias], item.artifact) for item in request.inputs)):
            raise SandboxUnavailable("invalid prepared files")
        if handle.provider_id != request.requirements.provider_id or handle.requirements_key != request.requirements.key:
            raise SandboxUnavailable("incompatible sandbox")
        try:
            parameters = canonical_json(request.parameters)
        except (TypeError, ValueError, RecursionError):
            raise SandboxUnavailable("invalid parameters") from None
        if len(parameters) > 16 * 1024:
            raise SandboxUnavailable("parameters exceeded limit")
        base = f"{self.base_url}/v1/sandboxes/{handle.sandbox_id}/proxy/44772"
        directory = f"/outputs/{request.operation_id}-{uuid4()}"
        inputs_dir = f"{directory}/inputs"
        outputs_dir = f"{directory}/outputs"
        source_path = f"{directory}/main.py"
        try:
            for attempt in range(30):
                control.raise_if_stopped()
                response = await self._http.get(
                    f"{base}/ping", headers=self._headers,
                    timeout=control.request_timeout_seconds(2), follow_redirects=False,
                )
                if response.status_code == 200:
                    break
                if response.status_code not in (502, 503, 504) or attempt == 29:
                    raise SandboxUnavailable("sandbox execution service unavailable")
                await asyncio.sleep(0.1)
            control.raise_if_stopped()
            response = await self._http.post(
                f"{base}/directories", headers=self._headers,
                json={path: {"owner": "openship", "group": "openship", "mode": 700}
                      for path in (directory, inputs_dir, outputs_dir)},
                timeout=control.request_timeout_seconds(30), follow_redirects=False,
            )
            response.raise_for_status()
            uploads = [(source_path, files.source),
                       (f"{directory}/parameters.json", parameters)]
            uploads.extend((f"{inputs_dir}/{name}", content) for name, content in inputs.items())
            for path, content in uploads:
                control.raise_if_stopped()
                metadata = json.dumps({"path": path, "owner": "openship", "group": "openship", "mode": 600})
                response = await self._http.post(
                    f"{base}/files/upload", headers=self._headers,
                    files=[("metadata", ("metadata.json", metadata, "application/json")),
                           ("file", ("input", content, "application/octet-stream"))],
                    timeout=control.request_timeout_seconds(30), follow_redirects=False,
                )
                response.raise_for_status()
            control.raise_if_stopped()
            body = {"argv": ["python", source_path], "timeout": 30000, "uid": 1000, "gid": 1000,
                    "cwd": directory, "envs": {"OPENSHIP_INPUT_DIR": inputs_dir,
                    "OPENSHIP_OUTPUT_DIR": outputs_dir, "OPENSHIP_PARAMETERS": f"{directory}/parameters.json"}}
            stdout, stderr = [], []
            diagnostic_bytes = 0
            completed = False
            buffer = b""
            async with self._http.stream(
                "POST", f"{base}/command", headers=self._headers, json=body,
                timeout=control.request_timeout_seconds(35), follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise SandboxUnavailable("unsupported sandbox response encoding")
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    control.raise_if_stopped()
                    buffer += chunk
                    # JSON may encode a single diagnostic byte as six ASCII
                    # bytes (for example, a NUL as \\u0000).
                    if len(buffer) > 2 * 1024 * 1024:
                        raise SandboxUnavailable("sandbox diagnostics exceeded limit")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise SandboxUnavailable("invalid sandbox command event")
                        kind = event.get("type")
                        if kind in ("stdout", "stderr"):
                            text = event["text"]
                            if not isinstance(text, str):
                                raise SandboxUnavailable("invalid sandbox command event")
                            diagnostic_bytes += len(text.encode("utf-8"))
                            if diagnostic_bytes > 256 * 1024:
                                raise SandboxUnavailable("sandbox diagnostics exceeded limit")
                            (stdout if kind == "stdout" else stderr).append(text)
                        elif kind == "execution_complete":
                            if completed or type(event.get("exit_code")) is not int or event["exit_code"] != 0:
                                raise SandboxUnavailable("sandbox command failed")
                            completed = True
                        elif kind == "error":
                            raise SandboxUnavailable("sandbox command failed")
            control.raise_if_stopped()
            if buffer.strip() or not completed:
                raise SandboxUnavailable("sandbox command interrupted")
            async with self._http.stream(
                "GET", f"{base}/directories/list", headers=self._headers,
                params={"path": outputs_dir, "depth": 1},
                timeout=control.request_timeout_seconds(30), follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise SandboxUnavailable("unsupported sandbox response encoding")
                listing = bytearray()
                async for chunk in response.aiter_bytes():
                    control.raise_if_stopped()
                    listing.extend(chunk)
                    if len(listing) > 128 * 1024:
                        raise SandboxUnavailable("sandbox output listing exceeded limit")
            entries = json.loads(listing)
            control.raise_if_stopped()
            if not isinstance(entries, list) or len(entries) != len(request.outputs):
                raise SandboxUnavailable("unexpected sandbox output")
            expected = {f"{outputs_dir}/{spec.alias}": spec.alias for spec in request.outputs}
            seen = set()
            sizes = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SandboxUnavailable("invalid sandbox output")
                path = entry.get("path")
                size = entry.get("size")
                if (path not in expected or path in seen or entry.get("type") != "file"
                        or type(size) is not int or size < 0 or size > 4 * 1024 * 1024):
                    raise SandboxUnavailable("invalid sandbox output")
                seen.add(path)
                sizes[path] = size
            outputs = {}
            total = 0
            for path, alias in expected.items():
                control.raise_if_stopped()
                data = bytearray()
                async with self._http.stream(
                    "GET", f"{base}/files/download", headers=self._headers,
                    params={"path": path}, timeout=control.request_timeout_seconds(30),
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise SandboxUnavailable("incomplete sandbox output")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise SandboxUnavailable("unsupported sandbox response encoding")
                    async for chunk in response.aiter_bytes():
                        control.raise_if_stopped()
                        data.extend(chunk)
                        total += len(chunk)
                        if len(data) > 4 * 1024 * 1024 or total > 8 * 1024 * 1024:
                            raise SandboxUnavailable("sandbox output exceeded limit")
                if len(data) != sizes[path]:
                    raise SandboxUnavailable("incomplete sandbox output")
                outputs[alias] = bytes(data)
            control.raise_if_stopped()
            return ExecutionResult(exit_code=0, stdout="".join(stdout), stderr="".join(stderr), outputs=outputs)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise SandboxUnavailable("sandbox execution interrupted") from None
