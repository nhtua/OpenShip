import asyncio
import hashlib
import uuid

import pytest

from src.openship.sandboxes.client import PythonResult, SandboxProtocolError
from src.openship.sandboxes.executor import execute_python


class FakeClient:
    def __init__(self, *, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    async def create(self, operation_id):
        self.calls.append(("create", operation_id))
        if self.fail_at == "create":
            raise SandboxProtocolError("sandbox create outcome is uncertain")
        return "sandbox-1"

    async def upload_source(self, sandbox_id, source, digest):
        self.calls.append(("upload", sandbox_id, source, digest))
        if self.fail_at == "upload":
            raise SandboxProtocolError("sandbox upload failed")

    async def verify_source(self, sandbox_id, digest, size):
        self.calls.append(("verify", sandbox_id, digest, size))

    async def run_python(self, sandbox_id):
        self.calls.append(("run", sandbox_id))
        if self.fail_at == "run":
            raise SandboxProtocolError("sandbox command outcome is uncertain")
        return PythonResult(stdout="ok", stderr="")

    async def destroy(self, sandbox_id):
        self.calls.append(("destroy", sandbox_id))
        if self.fail_at == "destroy":
            raise SandboxProtocolError("sandbox deletion failed")


class Control:
    def __init__(self, stop_at=None):
        self.stop_at = stop_at
        self.checks = 0

    def raise_if_stopped(self):
        self.checks += 1
        if self.checks == self.stop_at:
            raise RuntimeError("cancelled")


class SlowClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.command_cancelled = False

    async def run_python(self, sandbox_id):
        self.calls.append(("run", sandbox_id))
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.command_cancelled = True
            raise
        return PythonResult(stdout="late", stderr="")


def test_execution_is_single_dispatch_and_destroys_after_success():
    client, attempt_id, source = FakeClient(), uuid.uuid4(), b"print('ok')"
    result = asyncio.run(
        execute_python(
            client,
            attempt_id=attempt_id,
            source=source,
            sha256=hashlib.sha256(source).hexdigest(),
            control=Control(),
        )
    )
    assert result.stdout == "ok"
    assert client.calls[0] == ("create", attempt_id)
    assert [call[0] for call in client.calls] == [
        "create", "upload", "verify", "run", "destroy"
    ]


@pytest.mark.parametrize("fail_at", ["upload", "run"])
def test_execution_destroys_after_any_known_sandbox_failure(fail_at):
    client, source = FakeClient(fail_at=fail_at), b"pass"
    with pytest.raises(SandboxProtocolError):
        asyncio.run(
            execute_python(
                client,
                attempt_id=uuid.uuid4(),
                source=source,
                sha256=hashlib.sha256(source).hexdigest(),
                control=Control(),
            )
        )
    assert client.calls[-1] == ("destroy", "sandbox-1")
    assert sum(call[0] == "run" for call in client.calls) <= 1


def test_uncertain_create_is_not_retried_or_destroyed_without_an_id():
    client, source = FakeClient(fail_at="create"), b"pass"
    with pytest.raises(SandboxProtocolError):
        asyncio.run(
            execute_python(
                client,
                attempt_id=uuid.uuid4(),
                source=source,
                sha256=hashlib.sha256(source).hexdigest(),
                control=Control(),
            )
        )
    assert [call[0] for call in client.calls] == ["create"]


def test_cancellation_after_create_still_destroys_sandbox():
    client, source = FakeClient(), b"pass"
    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            execute_python(
                client,
                attempt_id=uuid.uuid4(),
                source=source,
                sha256=hashlib.sha256(source).hexdigest(),
                control=Control(stop_at=2),
            )
        )
    assert client.calls[-1] == ("destroy", "sandbox-1")


def test_cancellation_while_waiting_stops_command_and_destroys_sandbox():
    client, source = SlowClient(), b"pass"
    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            execute_python(
                client,
                attempt_id=uuid.uuid4(),
                source=source,
                sha256=hashlib.sha256(source).hexdigest(),
                control=Control(stop_at=4),
            )
        )
    assert client.command_cancelled is True
    assert client.calls[-1] == ("destroy", "sandbox-1")


def test_cleanup_failure_is_not_reported_as_success():
    client, source = FakeClient(fail_at="destroy"), b"pass"
    with pytest.raises(SandboxProtocolError, match="cleanup"):
        asyncio.run(
            execute_python(
                client,
                attempt_id=uuid.uuid4(),
                source=source,
                sha256=hashlib.sha256(source).hexdigest(),
                control=Control(),
            )
        )
