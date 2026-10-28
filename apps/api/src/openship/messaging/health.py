"""Bounded installation-scoped NATS worker health request/reply."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

logger = logging.getLogger(__name__)

HEALTH_RPC_TIMEOUT_SECONDS = 2.0
MAX_HEALTH_ENVELOPE_BYTES = 16 * 1024
_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "installation_id",
        "api_instance_id",
        "request_id",
        "source_service",
    }
)
_RESPONSE_FIELDS = _REQUEST_FIELDS | {"ready"}


class InvalidHealthMessage(ValueError):
    """A malformed or wrongly bound health request/reply."""


@dataclass(frozen=True, slots=True)
class WorkerHealthResult:
    ready: bool


@dataclass(frozen=True, slots=True)
class _HealthIdentity:
    installation_id: uuid.UUID
    api_instance_id: uuid.UUID
    request_id: uuid.UUID


def health_request_subject(installation_id: uuid.UUID) -> str:
    """Return the single worker health request subject for an installation."""
    return f"openship.{installation_id.hex}.v1.rpc.worker.health"


def health_reply_subject(
    installation_id: uuid.UUID,
    api_instance_id: uuid.UUID,
    request_id: uuid.UUID,
) -> str:
    """Mint the exact API-instance and request-scoped reply subject."""
    return (
        f"openship.{installation_id.hex}.v1.reply."
        f"{api_instance_id.hex}.{request_id.hex}"
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidHealthMessage()
        value[key] = item
    return value


def _canonical_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidHealthMessage()
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise InvalidHealthMessage() from error
    if str(parsed) != value:
        raise InvalidHealthMessage()
    return parsed


def _subject_uuid(value: str) -> uuid.UUID:
    if len(value) != 32:
        raise InvalidHealthMessage()
    try:
        parsed = uuid.UUID(hex=value)
    except ValueError as error:
        raise InvalidHealthMessage() from error
    if parsed.hex != value:
        raise InvalidHealthMessage()
    return parsed


def _reply_identity(subject: str) -> _HealthIdentity:
    parts = subject.split(".")
    if len(parts) != 6 or parts[0] != "openship" or parts[2:4] != ["v1", "reply"]:
        raise InvalidHealthMessage()
    return _HealthIdentity(
        installation_id=_subject_uuid(parts[1]),
        api_instance_id=_subject_uuid(parts[4]),
        request_id=_subject_uuid(parts[5]),
    )


def _decode(body: bytes) -> dict[str, object]:
    if not isinstance(body, bytes) or len(body) > MAX_HEALTH_ENVELOPE_BYTES:
        raise InvalidHealthMessage()
    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise InvalidHealthMessage() from None
    if not isinstance(record, dict):
        raise InvalidHealthMessage()
    return record


def _request_body(identity: _HealthIdentity) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "worker.health",
            "installation_id": str(identity.installation_id),
            "api_instance_id": str(identity.api_instance_id),
            "request_id": str(identity.request_id),
            "source_service": "api",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _response_body(identity: _HealthIdentity) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "worker.health.result",
            "installation_id": str(identity.installation_id),
            "api_instance_id": str(identity.api_instance_id),
            "request_id": str(identity.request_id),
            "source_service": "worker",
            "ready": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_request(
    message: Msg,
    *,
    installation_id: uuid.UUID,
) -> _HealthIdentity:
    if (
        not isinstance(message.subject, str)
        or not isinstance(message.reply, str)
        or message.subject != health_request_subject(installation_id)
    ):
        raise InvalidHealthMessage()
    identity = _reply_identity(message.reply)
    record = _decode(message.data)
    if (
        record.keys() != _REQUEST_FIELDS
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["kind"] != "worker.health"
        or record["source_service"] != "api"
    ):
        raise InvalidHealthMessage()
    request_identity = _HealthIdentity(
        installation_id=_canonical_uuid(record["installation_id"]),
        api_instance_id=_canonical_uuid(record["api_instance_id"]),
        request_id=_canonical_uuid(record["request_id"]),
    )
    if identity != request_identity or identity.installation_id != installation_id:
        raise InvalidHealthMessage()
    return identity


def _is_valid_response(
    message: Msg,
    *,
    subject: str,
    identity: _HealthIdentity,
) -> bool:
    if message.subject != subject:
        return False
    try:
        record = _decode(message.data)
        return (
            record.keys() == _RESPONSE_FIELDS
            and type(record["schema_version"]) is int
            and record["schema_version"] == 1
            and record["kind"] == "worker.health.result"
            and record["source_service"] == "worker"
            and record["ready"] is True
            and _canonical_uuid(record["installation_id"]) == identity.installation_id
            and _canonical_uuid(record["api_instance_id"]) == identity.api_instance_id
            and _canonical_uuid(record["request_id"]) == identity.request_id
        )
    except InvalidHealthMessage:
        return False


async def check_worker_health(
    client: Client,
    *,
    installation_id: uuid.UUID,
    api_instance_id: uuid.UUID | None = None,
) -> WorkerHealthResult:
    """Return ready on a strictly bound response, otherwise unavailable by 2s."""
    api_instance_id = api_instance_id or uuid.uuid4()
    identity = _HealthIdentity(
        installation_id=installation_id,
        api_instance_id=api_instance_id,
        request_id=uuid.uuid4(),
    )
    request_subject = health_request_subject(installation_id)
    reply_subject = health_reply_subject(
        installation_id, api_instance_id, identity.request_id
    )
    loop = asyncio.get_running_loop()
    reply: asyncio.Future[bool] = loop.create_future()
    subscription: Subscription | None = None

    async def receive(message: Msg) -> None:
        if not reply.done() and _is_valid_response(
            message, subject=reply_subject, identity=identity
        ):
            reply.set_result(True)

    try:
        async with asyncio.timeout(HEALTH_RPC_TIMEOUT_SECONDS):
            subscription = await client.subscribe(
                reply_subject,
                cb=receive,
                pending_msgs_limit=1,
                pending_bytes_limit=MAX_HEALTH_ENVELOPE_BYTES,
            )
            await client.flush(timeout=HEALTH_RPC_TIMEOUT_SECONDS)
            await client.publish(
                request_subject,
                _request_body(identity),
                reply=reply_subject,
            )
            await client.flush(timeout=HEALTH_RPC_TIMEOUT_SECONDS)
            await reply
        return WorkerHealthResult(ready=True)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - broker errors become safe unavailable.
        logger.warning("worker_health_rpc_unavailable type=%s", type(error).__name__)
        return WorkerHealthResult(ready=False)
    finally:
        if not reply.done():
            reply.cancel()
        if subscription is not None:
            try:
                await subscription.unsubscribe()
            except Exception as error:  # noqa: BLE001 - cleanup errors are safe to report.
                logger.warning(
                    "worker_health_reply_cleanup_failed type=%s",
                    type(error).__name__,
                )


async def subscribe_worker_health(
    client: Client,
    *,
    installation_id: uuid.UUID,
    is_ready: Callable[[], Awaitable[bool]],
) -> Subscription:
    """Listen on this installation's health subject with bounded pending work."""

    async def answer(message: Msg) -> None:
        try:
            identity = _parse_request(message, installation_id=installation_id)
        except InvalidHealthMessage:
            return
        try:
            if not await is_ready():
                return
            await client.publish(message.reply, _response_body(identity))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate callback failures.
            logger.warning("worker_health_reply_failed type=%s", type(error).__name__)

    subscription = await client.subscribe(
        health_request_subject(installation_id),
        cb=answer,
        pending_msgs_limit=1,
        pending_bytes_limit=MAX_HEALTH_ENVELOPE_BYTES,
    )
    try:
        await client.flush(timeout=HEALTH_RPC_TIMEOUT_SECONDS)
    except BaseException:
        try:
            await subscription.unsubscribe()
        except Exception as error:  # noqa: BLE001 - preserve the original subscribe failure.
            logger.warning(
                "worker_health_subscription_cleanup_failed type=%s",
                type(error).__name__,
            )
        raise
    return subscription
