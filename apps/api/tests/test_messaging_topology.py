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
