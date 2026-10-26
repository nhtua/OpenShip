"""Broker-only gate primitives preserve confirmed and unknown partial outcomes."""

from types import SimpleNamespace

import pytest
from nats.js.api import ConsumerPause


class PauseBroker:
    def __init__(self, *, fail=None, mismatched=False):
        self.states = {}
        self.fail = fail
        self.mismatched = mismatched

    async def pause_consumer(self, stream, consumer, pause_until, *, timeout):
        assert timeout == 0.25
        self.states[stream, consumer] = pause_until
        if consumer == self.fail:
            # The send may have succeeded even if the response is lost.
            raise TimeoutError("private broker details")
        return ConsumerPause(paused=not self.mismatched, pause_until=pause_until)

    async def resume_consumer(self, stream, consumer, *, timeout):
        assert timeout == 0.25
        self.states[stream, consumer] = None
        if consumer == self.fail:
            raise TimeoutError("private broker details")
        return ConsumerPause(paused=False)


@pytest.mark.asyncio
async def test_pause_and_resume_confirm_both_exact_resources_without_database(
    monkeypatch,
):
    from src.openship.messaging import gates, topology

    def forbidden_database():
        pytest.fail("broker primitives must not access DB gate state")

    monkeypatch.setattr(topology, "SessionLocal", forbidden_database)
    broker = PauseBroker()
    paused = await gates.pause_execution_consumers(broker, timeout=0.25)
    assert paused.complete
    assert broker.states == {
        ("OS_RUNS", "run-v1"): "2100-01-01T00:00:00Z",
        ("OS_MAINTENANCE", "maintenance-v1"): "2100-01-01T00:00:00Z",
    }
    assert all(item.paused and item.confirmed for item in paused.consumers)
    assert paused.as_dict()["consumers"][0]["pause_until"] == "2100-01-01T00:00:00Z"
    resumed = await gates.resume_execution_consumers(broker, timeout=0.25)
    assert resumed.complete
    assert all(item.confirmed and item.paused is False for item in resumed.consumers)
    assert all(value is None for value in broker.states.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "resume"])
async def test_timeout_is_incomplete_without_undoing_any_confirmed_success(action):
    from src.openship.messaging import gates

    broker = PauseBroker(fail="maintenance-v1")
    operation = getattr(gates, f"{action}_execution_consumers")
    report = await operation(broker, timeout=0.25)
    assert report.complete is False
    run, maintenance = report.consumers
    assert run.confirmed
    assert maintenance.confirmed is False
    assert maintenance.paused is None
    assert maintenance.error == "TimeoutError"
    assert "private" not in str(report.as_dict())
    assert broker.states["OS_RUNS", "run-v1"] == (
        "2100-01-01T00:00:00Z" if action == "pause" else None
    )


@pytest.mark.asyncio
async def test_broker_reply_with_wrong_state_or_deadline_is_not_confirmation():
    from src.openship.messaging import gates

    report = await gates.pause_execution_consumers(
        PauseBroker(mismatched=True), timeout=0.25
    )
    assert not report.complete
    assert all(not item.confirmed for item in report.consumers)

    async def wrong_deadline(*args, **kwargs):
        return ConsumerPause(paused=True, pause_until="2099-01-01T00:00:00Z")

    report = await gates.pause_execution_consumers(
        SimpleNamespace(pause_consumer=wrong_deadline), timeout=0.25
    )
    assert not report.complete
