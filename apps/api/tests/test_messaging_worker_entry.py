"""Production worker entry lifecycle uses the JetStream run consumer."""

import asyncio

import pytest

from src.openship.messaging import worker as messaging_worker
from src.openship.runs import worker as run_worker_module


@pytest.mark.asyncio
async def test_run_worker_uses_consumer_and_closes_connected_client():
    stop = asyncio.Event()
    client = _Client()
    observed = {}

    async def connect(**kwargs):
        observed["connection"] = kwargs
        return client

    class Consumer:
        def __init__(self, connected_client, *, worker_id):
            observed["client"] = connected_client
            observed["worker_id"] = worker_id

        async def run(self, received_stop):
            observed["stop"] = received_stop

    await messaging_worker.run_worker(
        stop,
        worker_id="phase6a-worker",
        client_factory=connect,
        consumer_factory=Consumer,
    )

    assert observed["connection"]["name"] == "openship-run-worker"
    assert observed["client"] is client
    assert observed["worker_id"] == "phase6a-worker"
    assert observed["stop"] is stop
    assert client.closed is True


@pytest.mark.asyncio
async def test_run_worker_closes_connected_client_when_consumer_fails():
    client = _Client()

    async def connect(**_kwargs):
        return client

    class Consumer:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _stop):
            raise RuntimeError("consumer stopped unexpectedly")

    with pytest.raises(RuntimeError, match="consumer stopped unexpectedly"):
        await messaging_worker.run_worker(
            asyncio.Event(),
            client_factory=connect,
            consumer_factory=Consumer,
        )

    assert client.closed is True


class _Client:
    closed = False

    async def close(self):
        self.closed = True


def test_runs_worker_module_delegates_to_jetstream_entry(monkeypatch):
    called = []
    monkeypatch.setattr(messaging_worker, "main", lambda: called.append(True))

    run_worker_module.main()

    assert called == [True]


def test_worker_cli_preserves_worker_identity_argument(monkeypatch):
    observed = []

    async def record(worker_id):
        observed.append(worker_id)

    monkeypatch.setattr(messaging_worker, "_run", record)

    messaging_worker.main(["--worker-id", "configured-worker", "--log-level", "ERROR"])

    assert observed == ["configured-worker"]
