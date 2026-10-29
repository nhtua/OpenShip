"""Single-dispatch orchestration for one admitted Python attempt."""

from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import suppress
from uuid import UUID

from .client import OpenSandboxClient, PythonResult, SandboxProtocolError

_SOURCE_LIMIT = 256 * 1024
_CLEANUP_TIMEOUT = 15


async def execute_python(
    client: OpenSandboxClient,
    *,
    attempt_id: UUID,
    source: bytes,
    sha256: str,
    control,
) -> PythonResult:
    """Run one digest-pinned source once, then destroy its known sandbox.

    An uncertain create has no known ID to delete and is surfaced for external
    reconciliation. Command requests are never retried.
    """
    if not isinstance(attempt_id, UUID):
        raise TypeError("attempt ID must be a UUID")
    if (
        not isinstance(source, bytes)
        or len(source) > _SOURCE_LIMIT
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or hashlib.sha256(source).hexdigest() != sha256
    ):
        raise ValueError("source digest or size is invalid")

    sandbox_id = None
    try:
        control.raise_if_stopped()
        sandbox_id = await client.create(attempt_id)
        control.raise_if_stopped()
        await client.wait_ready(sandbox_id)
        control.raise_if_stopped()
        await client.upload_source(sandbox_id, source, sha256)
        await client.verify_source(sandbox_id, sha256, len(source))
        control.raise_if_stopped()
        return await _run_interruptibly(client, sandbox_id, control)
    finally:
        if sandbox_id is not None:
            await _destroy(client, sandbox_id)


async def _run_interruptibly(client, sandbox_id, control) -> PythonResult:
    command = asyncio.create_task(client.run_python(sandbox_id))
    try:
        while not command.done():
            await asyncio.wait((command,), timeout=0.1)
            control.raise_if_stopped()
        control.raise_if_stopped()
        return command.result()
    except BaseException:
        command.cancel()
        with suppress(Exception, asyncio.CancelledError):
            await command
        raise


async def _destroy(client: OpenSandboxClient, sandbox_id: str) -> None:
    cleanup = asyncio.create_task(client.destroy(sandbox_id))
    try:
        await asyncio.wait_for(asyncio.shield(cleanup), timeout=_CLEANUP_TIMEOUT)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(cleanup, timeout=_CLEANUP_TIMEOUT)
        except (SandboxProtocolError, TimeoutError):
            raise SandboxProtocolError("sandbox cleanup failed; reconciliation required") from None
        raise
    except (SandboxProtocolError, TimeoutError):
        raise SandboxProtocolError("sandbox cleanup failed; reconciliation required") from None
