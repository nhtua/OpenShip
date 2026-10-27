"""Background run recovery stays independent from broker run slots."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from src.openship.database.session import SessionLocal


@pytest.mark.asyncio
async def test_recovery_scheduler_is_off_loop_and_keeps_running_after_failure(
    monkeypatch,
):
    from src.openship.messaging import recovery

    loop_thread = threading.get_ident()
    calls = {"availability": [], "connectors": [], "leases": []}
    wall_clock_calls = []

    monkeypatch.setattr(
        recovery,
        "time",
        SimpleNamespace(
            monotonic=time.monotonic,
            time=lambda: wall_clock_calls.append(True) or time.monotonic(),
        ),
    )

    def record(action):
        def run(*_args):
            calls[action].append(threading.get_ident())
            if action == "availability" and len(calls[action]) == 1:
                raise RuntimeError("synthetic refresh failure")

        return run

    monkeypatch.setattr(
        recovery,
        "refresh_tool_availability",
        record("availability"),
    )
    monkeypatch.setattr(recovery, "expire_connector_blockers", record("connectors"))
    monkeypatch.setattr(recovery, "reconcile_expired", record("leases"))

    supervisor = recovery.RecoverySupervisor(
        worker_id="recovery-test",
        session_factory=SessionLocal,
        availability_interval=0.01,
        recovery_interval=0.02,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(supervisor.run(stop))
    try:
        async with asyncio.timeout(2):
            while (
                len(calls["availability"]) < 2
                or not calls["connectors"]
                or not calls["leases"]
            ):
                await asyncio.sleep(0.005)
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)

    assert calls["availability"]
    assert all(
        thread_id != loop_thread
        for action_calls in calls.values()
        for thread_id in action_calls
    )
    assert wall_clock_calls == []


@pytest.mark.asyncio
async def test_recovery_shutdown_closes_its_dedicated_execution_resource(monkeypatch):
    from src.openship.messaging import recovery

    closed = []
    original_resource = recovery._RecoveryResource

    class TrackedResource(original_resource):
        def close(self):
            closed.append(True)

    monkeypatch.setattr(recovery, "_RecoveryResource", TrackedResource)
    monkeypatch.setattr(recovery, "refresh_tool_availability", lambda *_args: None)
    monkeypatch.setattr(recovery, "expire_connector_blockers", lambda *_args: None)
    monkeypatch.setattr(recovery, "reconcile_expired", lambda *_args: None)
    supervisor = recovery.RecoverySupervisor(
        worker_id="recovery-close-test",
        session_factory=SessionLocal,
        availability_interval=10,
        recovery_interval=10,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(supervisor.run(stop))
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, 2)

    assert closed == [True]
