"""Topology bootstrap lifecycle behavior without a live NATS server."""

import pytest

from src.openship.messaging import topology


@pytest.mark.asyncio
async def test_bootstrap_closes_nats_connection_when_database_identity_lookup_fails(
    monkeypatch,
):
    class Client:
        closed = False

        def jetstream(self, *, timeout):
            return object()

        async def close(self):
            self.closed = True

    client = Client()

    async def connect_nats(**_kwargs):
        return client

    def missing_installation_id():
        raise RuntimeError("messaging state is missing")

    monkeypatch.setattr(topology, "connect_nats", connect_nats)
    monkeypatch.setattr(topology, "_installation_id", missing_installation_id)

    with pytest.raises(RuntimeError, match="messaging state is missing"):
        await topology.ensure_topology(apply=False)

    assert client.closed is True


class MemoryJetStream:
    """External broker boundary, retaining resources across bootstrap calls."""

    def __init__(self, installation_id):
        from types import SimpleNamespace

        self.streams = {
            config.name: SimpleNamespace(config=config)
            for config in topology.stream_configs(installation_id)
        }
        self.consumers = {}

    async def stream_info(self, name):
        return self.streams[name]

    async def consumer_info(self, stream, consumer):
        from nats.js.errors import NotFoundError

        try:
            return self.consumers[stream, consumer]
        except KeyError:
            raise NotFoundError from None

    async def add_consumer(self, stream, *, config):
        from types import SimpleNamespace

        key = stream, config.durable_name
        assert key not in self.consumers, "bootstrap must never rewrite a durable"
        self.consumers[key] = SimpleNamespace(config=config, paused=False)


@pytest.mark.asyncio
async def test_all_streams_present_is_not_ready_until_consumers_are_provisioned():
    import uuid

    installation_id = uuid.uuid4()
    js = MemoryJetStream(installation_id)
    checked = await topology.ensure_topology(
        apply=False, js=js, installation_id=installation_id
    )
    assert checked.ready is False
    assert not js.consumers
    assert [item.status for item in checked.consumers] == ["missing", "missing"]
    applied = await topology.ensure_topology(
        apply=True, js=js, installation_id=installation_id
    )
    assert applied.ready
    run = js.consumers["OS_RUNS", "run-v1"]
    assert run.config.filter_subject == (
        f"openship.{installation_id.hex}.v1.command.run.execute"
    )
    assert run.config.ack_wait == 30
    assert run.config.max_deliver == 10
    assert run.config.max_ack_pending == run.config.max_waiting == 4
    maintenance = js.consumers["OS_MAINTENANCE", "maintenance-v1"]
    assert maintenance.config.max_ack_pending == maintenance.config.max_waiting == 1
    run.config.pause_until = "2100-01-01T00:00:00Z"
    run.paused = True
    reapplied = await topology.ensure_topology(
        apply=True, js=js, installation_id=installation_id
    )
    assert reapplied.ready
    assert js.consumers["OS_RUNS", "run-v1"] is run
    assert reapplied.as_dict()["consumers"][0]["paused"] is True
    assert reapplied.as_dict()["consumers"][0]["pause_until"] == "2100-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "field,value",
    [
        ("filter_subject", "wrong"),
        ("ack_policy", "none"),
        ("deliver_policy", "new"),
        ("replay_policy", "original"),
        ("ack_wait", 31),
        ("max_deliver", 11),
        ("backoff", [1.0]),
        ("max_ack_pending", 5),
        ("max_waiting", 5),
        ("inactive_threshold", 60.0),
        ("num_replicas", 2),
        ("mem_storage", True),
        ("deliver_subject", "push.inbox"),
        ("filter_subjects", ["extra"]),
    ],
)
@pytest.mark.asyncio
async def test_apply_reports_consumer_drift_without_mutation(field, value):
    import uuid
    from dataclasses import replace

    installation_id = uuid.uuid4()
    js = MemoryJetStream(installation_id)
    await topology.ensure_topology(apply=True, js=js, installation_id=installation_id)
    consumer = js.consumers["OS_RUNS", "run-v1"]
    consumer.config = replace(consumer.config, **{field: value})
    checked = await topology.ensure_topology(
        apply=True, js=js, installation_id=installation_id
    )
    assert not checked.ready
    assert field in checked.consumers[0].differences
    assert getattr(consumer.config, field) == value


@pytest.mark.asyncio
async def test_capacity_declaration_change_reports_drift_without_updating_durable(
    monkeypatch,
):
    import uuid

    installation_id = uuid.uuid4()
    js = MemoryJetStream(installation_id)
    await topology.ensure_topology(apply=True, js=js, installation_id=installation_id)
    monkeypatch.setattr(topology.settings, "messaging_run_max_ack_pending", 8)
    report = await topology.ensure_topology(
        apply=True, js=js, installation_id=installation_id
    )
    assert not report.ready
    assert set(report.consumers[0].differences) == {"max_ack_pending", "max_waiting"}
    assert js.consumers["OS_RUNS", "run-v1"].config.max_ack_pending == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["jetstream", "consumer_info", "add_consumer"])
async def test_owned_connection_closes_on_consumer_and_context_failures(
    monkeypatch, failure
):
    import uuid

    installation_id = uuid.uuid4()
    js = MemoryJetStream(installation_id)

    async def failed(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    class Client:
        closed = False

        def jetstream(self, *, timeout):
            if failure == "jetstream":
                raise RuntimeError("broker unavailable")
            setattr(js, failure, failed)
            return js

        async def close(self):
            self.closed = True

    client = Client()

    async def connect_nats(**kwargs):
        return client

    monkeypatch.setattr(topology, "connect_nats", connect_nats)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        await topology.ensure_topology(apply=True, installation_id=installation_id)
    assert client.closed
