"""Bounded source-owned health request/reply contracts."""

import json
import uuid
from types import SimpleNamespace

import pytest


def _reply_body(installation_id, api_instance_id, request_id):
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "worker.health.result",
            "installation_id": str(installation_id),
            "api_instance_id": str(api_instance_id),
            "request_id": str(request_id),
            "source_service": "worker",
            "ready": True,
        },
        separators=(",", ":"),
    ).encode()


class _Subscription:
    def __init__(self, on_unsubscribe=None):
        self.on_unsubscribe = on_unsubscribe or (lambda: None)
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True
        self.on_unsubscribe()


class _RpcClient:
    def __init__(self, reply_factory=None):
        self.reply_factory = reply_factory
        self.events = []
        self.subject = None
        self.callback = None
        self.options = None
        self.request = None
        self.reply_subject = None
        self.subscription = _Subscription()

    async def subscribe(self, subject, *, cb, pending_msgs_limit, pending_bytes_limit):
        self.events.append("subscribe")
        self.subject = subject
        self.callback = cb
        self.options = (pending_msgs_limit, pending_bytes_limit)
        return self.subscription

    async def flush(self, *, timeout):
        assert timeout > 0
        self.events.append("flush")

    async def publish(self, subject, payload, *, reply=""):
        self.events.append("publish")
        self.request = (subject, payload)
        self.reply_subject = reply
        if self.reply_factory is not None:
            for response in self.reply_factory(payload, reply):
                await self.callback(
                    SimpleNamespace(subject=reply, data=response, reply="")
                )


@pytest.mark.asyncio
async def test_api_health_request_subscribes_flushes_and_binds_first_valid_reply():
    from src.openship.messaging import health

    installation_id, api_instance_id = uuid.uuid4(), uuid.uuid4()

    def replies(payload, reply_subject):
        request = json.loads(payload)
        wrong = uuid.uuid4()
        yield _reply_body(installation_id, api_instance_id, wrong)
        yield _reply_body(
            installation_id,
            api_instance_id,
            uuid.UUID(request["request_id"]),
        )

    client = _RpcClient(reply_factory=replies)
    result = await health.check_worker_health(
        client,
        installation_id=installation_id,
        api_instance_id=api_instance_id,
    )

    assert result.ready
    assert client.events == ["subscribe", "flush", "publish", "flush"]
    request_subject, request_body = client.request
    request = json.loads(request_body)
    assert request_subject == f"openship.{installation_id.hex}.v1.rpc.worker.health"
    assert client.subject == client.reply_subject
    assert client.subject == (
        f"openship.{installation_id.hex}.v1.reply."
        f"{api_instance_id.hex}.{request['request_id'].replace('-', '')}"
    )
    assert request == {
        "schema_version": 1,
        "kind": "worker.health",
        "installation_id": str(installation_id),
        "api_instance_id": str(api_instance_id),
        "request_id": request["request_id"],
        "source_service": "api",
    }
    assert client.options == (1, health.MAX_HEALTH_ENVELOPE_BYTES)
    assert client.subscription.unsubscribed


@pytest.mark.asyncio
async def test_api_health_returns_unavailable_for_invalid_or_missing_reply(monkeypatch):
    from src.openship.messaging import health

    monkeypatch.setattr(health, "HEALTH_RPC_TIMEOUT_SECONDS", 0.01)
    installation_id = uuid.uuid4()
    client = _RpcClient(
        reply_factory=lambda _payload, _reply: [
            b'{"schema_version":1,"kind":"worker.health.result"}'
        ]
    )
    result = await health.check_worker_health(
        client,
        installation_id=installation_id,
    )

    assert not result.ready
    assert client.subscription.unsubscribed


@pytest.mark.asyncio
async def test_worker_health_responder_validates_reply_binding_and_only_answers_ready():
    from src.openship.messaging import health

    installation_id, api_instance_id, request_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    client = _RpcClient()
    readiness = []

    async def ready():
        readiness.append(True)
        return True

    subscription = await health.subscribe_worker_health(
        client,
        installation_id=installation_id,
        is_ready=ready,
    )
    request_subject = f"openship.{installation_id.hex}.v1.rpc.worker.health"
    reply_subject = (
        f"openship.{installation_id.hex}.v1.reply."
        f"{api_instance_id.hex}.{request_id.hex}"
    )
    request = {
        "schema_version": 1,
        "kind": "worker.health",
        "installation_id": str(installation_id),
        "api_instance_id": str(api_instance_id),
        "request_id": str(request_id),
        "source_service": "api",
    }

    await client.callback(
        SimpleNamespace(
            subject=request_subject,
            data=json.dumps(request).encode(),
            reply=reply_subject,
        )
    )

    assert client.subject == request_subject
    assert client.options == (1, health.MAX_HEALTH_ENVELOPE_BYTES)
    assert client.events == ["subscribe", "flush", "publish"]
    assert client.request[0] == reply_subject
    assert json.loads(client.request[1]) == {
        "schema_version": 1,
        "kind": "worker.health.result",
        "installation_id": str(installation_id),
        "api_instance_id": str(api_instance_id),
        "request_id": str(request_id),
        "source_service": "worker",
        "ready": True,
    }
    assert readiness == [True]
    await subscription.unsubscribe()


@pytest.mark.asyncio
async def test_worker_health_responder_ignores_wrong_reply_and_unready_worker():
    from src.openship.messaging import health

    installation_id, api_instance_id, request_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    client = _RpcClient()
    readiness = []

    async def not_ready():
        readiness.append(True)
        return False

    await health.subscribe_worker_health(
        client,
        installation_id=installation_id,
        is_ready=not_ready,
    )
    request = {
        "schema_version": 1,
        "kind": "worker.health",
        "installation_id": str(installation_id),
        "api_instance_id": str(api_instance_id),
        "request_id": str(request_id),
        "source_service": "api",
    }
    await client.callback(
        SimpleNamespace(
            subject=f"openship.{installation_id.hex}.v1.rpc.worker.health",
            data=json.dumps(request).encode(),
            reply=(
                f"openship.{installation_id.hex}.v1.reply."
                f"{api_instance_id.hex}.{uuid.uuid4().hex}"
            ),
        )
    )
    assert client.request is None
    assert readiness == []

    await client.callback(
        SimpleNamespace(
            subject=f"openship.{installation_id.hex}.v1.rpc.worker.health",
            data=json.dumps(request).encode(),
            reply=(
                f"openship.{installation_id.hex}.v1.reply."
                f"{api_instance_id.hex}.{request_id.hex}"
            ),
        )
    )
    assert client.request is None
    assert readiness == [True]
