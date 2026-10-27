"""Declarative JetStream topology and non-destructive bootstrap checks."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from nats.aio.client import Client
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    DiscardPolicy,
    ReplayPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import BadRequestError, NotFoundError

from ..config import settings
from ..database.session import SessionLocal
from .capacity import ADVISORY_STREAM_MAX_BYTES
from .connection import connect_nats
from .models import MessageTransportState

_MIB = 1024 * 1024
_MAX_MESSAGE_BYTES = 16 * 1024
EXECUTION_CONSUMERS = (("OS_RUNS", "run-v1"), ("OS_MAINTENANCE", "maintenance-v1"))
EXHAUSTION_ADVISORY_SUBJECT = (
    "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_RUNS.run-v1"
)
EXHAUSTION_CONSUMER = ("OS_ADVISORIES", "exhaustion-v1")
REQUIRED_CONSUMERS = (*EXECUTION_CONSUMERS, EXHAUSTION_CONSUMER)


@dataclass(frozen=True)
class StreamResult:
    name: str
    status: str
    differences: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsumerResult:
    stream: str
    name: str
    status: str
    differences: tuple[str, ...] = ()
    paused: bool | None = None
    pause_until: str | None = None


@dataclass(frozen=True)
class TopologyReport:
    installation_id: str
    streams: tuple[StreamResult, ...]
    consumers: tuple[ConsumerResult, ...]

    @property
    def ready(self) -> bool:
        return {(item.stream, item.name) for item in self.consumers} == set(
            REQUIRED_CONSUMERS
        ) and all(item.status == "ready" for item in (*self.streams, *self.consumers))

    def as_dict(self) -> dict:
        return {
            "installation_id": self.installation_id,
            "ready": self.ready,
            "streams": [asdict(item) for item in self.streams],
            "consumers": [asdict(item) for item in self.consumers],
        }


class TopologyCompatibilityError(RuntimeError):
    """A runtime must not use missing or drifted broker resources."""


async def _create_consumer_only(js, stream: str, config: ConsumerConfig) -> None:
    """Create without upserting a concurrently provisioned or paused durable."""
    # nats-py 2.15.0's public add_consumer omits the server's top-level action.
    # Keep this pinned private-client dependency isolated, preserving the context
    # API prefix, request timeout and API error translation. Server 2.15.1 checks
    # action=create atomically before updating an existing differing config.
    version = js._nc.connected_server_version
    if (version.major, version.minor, version.patch) != (2, 15, 1) or (
        version.prerelease or version.build
    ):
        raise TopologyCompatibilityError(
            "consumer creation requires qualified NATS server 2.15.1"
        )
    request = json.dumps(
        {"stream_name": stream, "config": config.as_dict(), "action": "create"}
    ).encode()
    try:
        await js._api_request(
            f"{js._prefix}.CONSUMER.CREATE.{stream}.{config.name}.{config.filter_subject}",
            request,
            timeout=js._timeout,
        )
    except BadRequestError as error:
        # Pinned server JSConsumerAlreadyExists. The caller must reread and
        # compare the winner rather than turning this conflict into an update.
        if error.err_code != 10148:
            raise


def consumer_configs(
    installation_id: uuid.UUID,
) -> tuple[tuple[str, ConsumerConfig], ...]:
    """Shared pull durables; aggregate capacity changes require operator review."""
    namespace = f"openship.{installation_id.hex}.v1.command"
    specifications = (
        ("run.execute", settings.messaging_run_max_ack_pending),
        ("maintenance.retention", settings.messaging_maintenance_max_ack_pending),
    )
    execution = tuple(
        (
            stream,
            ConsumerConfig(
                name=name,
                durable_name=name,
                filter_subject=f"{namespace}.{suffix}",
                ack_policy=AckPolicy.EXPLICIT,
                deliver_policy=DeliverPolicy.ALL,
                replay_policy=ReplayPolicy.INSTANT,
                ack_wait=30.0,
                max_deliver=10,
                max_ack_pending=capacity,
                max_waiting=capacity,
                num_replicas=1,
                mem_storage=False,
                inactive_threshold=0.0,
            ),
        )
        for (stream, name), (suffix, capacity) in zip(
            EXECUTION_CONSUMERS, specifications, strict=True
        )
    )
    observer_capacity = settings.messaging_run_max_ack_pending
    return (
        *execution,
        (
            EXHAUSTION_CONSUMER[0],
            ConsumerConfig(
                name=EXHAUSTION_CONSUMER[1],
                durable_name=EXHAUSTION_CONSUMER[1],
                filter_subject=EXHAUSTION_ADVISORY_SUBJECT,
                ack_policy=AckPolicy.EXPLICIT,
                deliver_policy=DeliverPolicy.ALL,
                replay_policy=ReplayPolicy.INSTANT,
                ack_wait=30.0,
                max_deliver=-1,
                max_ack_pending=observer_capacity,
                max_waiting=observer_capacity,
                num_replicas=1,
                mem_storage=False,
                inactive_threshold=0.0,
            ),
        ),
    )


def _compare_consumer(
    expected: ConsumerConfig, actual: ConsumerConfig
) -> tuple[str, ...]:
    differences = []
    # PauseUntil is dynamic operator state and must never become bootstrap drift.
    for field in (
        "name",
        "durable_name",
        "filter_subject",
        "filter_subjects",
        "ack_policy",
        "deliver_policy",
        "replay_policy",
        "ack_wait",
        "max_deliver",
        "max_ack_pending",
        "max_waiting",
        "backoff",
        "inactive_threshold",
        "num_replicas",
        "mem_storage",
        "deliver_subject",
        "deliver_group",
        "opt_start_seq",
        "opt_start_time",
        "headers_only",
        "rate_limit_bps",
        "flow_control",
        "idle_heartbeat",
    ):
        wanted, found = getattr(expected, field), getattr(actual, field)
        if field in {"backoff", "filter_subjects"}:
            equal = (wanted or []) == (found or [])
        elif field in {
            "inactive_threshold",
            "mem_storage",
            "headers_only",
            "rate_limit_bps",
            "flow_control",
            "idle_heartbeat",
        }:
            equal = (wanted or 0) == (found or 0)
        else:
            equal = wanted == found
        if not equal:
            differences.append(field)
    return tuple(differences)


def stream_configs(installation_id: uuid.UUID) -> tuple[StreamConfig, ...]:
    """Return the immutable initial stream contract for one installation."""
    namespace = f"openship.{installation_id.hex}.v1"
    common = {
        "storage": StorageType.FILE,
        "num_replicas": 1,
        "max_msg_size": _MAX_MESSAGE_BYTES,
        "duplicate_window": 120.0,
    }
    return (
        StreamConfig(
            name="OS_RUNS",
            subjects=[f"{namespace}.command.run.execute"],
            retention=RetentionPolicy.WORK_QUEUE,
            max_bytes=512 * _MIB,
            max_age=0.0,
            discard=DiscardPolicy.NEW,
            deny_delete=False,
            **common,
        ),
        StreamConfig(
            name="OS_MAINTENANCE",
            subjects=[f"{namespace}.command.maintenance.retention"],
            retention=RetentionPolicy.WORK_QUEUE,
            max_bytes=128 * _MIB,
            max_age=0.0,
            discard=DiscardPolicy.NEW,
            **common,
        ),
        StreamConfig(
            name="OS_EVENTS",
            subjects=[f"{namespace}.event.>"],
            retention=RetentionPolicy.LIMITS,
            max_bytes=256 * _MIB,
            max_age=24 * 60 * 60.0,
            discard=DiscardPolicy.OLD,
            **common,
        ),
        StreamConfig(
            name="OS_CONTROLS",
            subjects=[f"{namespace}.control.>"],
            retention=RetentionPolicy.LIMITS,
            max_bytes=64 * _MIB,
            max_age=24 * 60 * 60.0,
            discard=DiscardPolicy.OLD,
            **common,
        ),
        StreamConfig(
            name="OS_FAILURES",
            subjects=[f"{namespace}.failure.>"],
            retention=RetentionPolicy.LIMITS,
            max_bytes=128 * _MIB,
            max_age=7 * 24 * 60 * 60.0,
            discard=DiscardPolicy.OLD,
            **common,
        ),
        StreamConfig(
            name="OS_ADVISORIES",
            subjects=[EXHAUSTION_ADVISORY_SUBJECT],
            retention=RetentionPolicy.WORK_QUEUE,
            max_bytes=ADVISORY_STREAM_MAX_BYTES,
            max_age=0.0,
            discard=DiscardPolicy.NEW,
            **common,
        ),
    )


def _installation_id() -> uuid.UUID:
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        if state is None:
            raise RuntimeError(
                "messaging state is missing; apply database migrations first"
            )
        return state.installation_id


def _compare_config(expected: StreamConfig, actual: StreamConfig) -> tuple[str, ...]:
    differences: list[str] = []
    fields = (
        "subjects",
        "retention",
        "max_consumers",
        "max_msgs",
        "max_bytes",
        "max_age",
        "max_msgs_per_subject",
        "discard",
        "discard_new_per_subject",
        "storage",
        "num_replicas",
        "max_msg_size",
        "duplicate_window",
        "no_ack",
        "sealed",
        "deny_delete",
        "deny_purge",
        "allow_rollup_hdrs",
    )
    for field in fields:
        expected_value = getattr(expected, field)
        actual_value = getattr(actual, field)
        if field == "subjects":
            equal = sorted(expected_value or []) == sorted(actual_value or [])
        elif field == "max_age":
            equal = float(expected_value or 0) == float(actual_value or 0)
        elif field in {"max_consumers", "max_msgs", "max_msgs_per_subject"}:
            # NATS represents an unlimited stream limit as either -1 or an
            # omitted value depending on the server/client API response.
            equal = (expected_value if expected_value is not None else -1) == (
                actual_value if actual_value is not None else -1
            )
        else:
            equal = expected_value == actual_value
        if not equal:
            differences.append(field)
    return tuple(differences)


async def ensure_topology(
    *,
    apply: bool,
    js=None,
    installation_id: uuid.UUID | None = None,
) -> TopologyReport:
    """Check resources or create missing ones, never rewriting existing config."""
    own_connection: Client | None = None
    if js is None:
        own_connection = await connect_nats(
            name="openship-messaging-bootstrap",
            allow_reconnect=False,
        )
    try:
        if own_connection is not None:
            js = own_connection.jetstream(timeout=5)
        selected_installation_id = installation_id or _installation_id()
        results: list[StreamResult] = []
        for expected in stream_configs(selected_installation_id):
            try:
                info = await js.stream_info(expected.name)
            except NotFoundError:
                if not apply:
                    results.append(StreamResult(expected.name, "missing"))
                    continue
                await js.add_stream(config=expected)
                info = await js.stream_info(expected.name)
            differences = _compare_config(expected, info.config)
            results.append(
                StreamResult(
                    expected.name,
                    "drift" if differences else "ready",
                    differences,
                )
            )
        consumers = []
        stream_status = {item.name: item.status for item in results}
        for stream, expected in consumer_configs(selected_installation_id):
            name = expected.durable_name
            try:
                info = await js.consumer_info(stream, name)
            except NotFoundError:
                if not apply or stream_status[stream] != "ready":
                    consumers.append(ConsumerResult(stream, name, "missing"))
                    continue
                await _create_consumer_only(js, stream, expected)
                info = await js.consumer_info(stream, name)
            differences = _compare_consumer(expected, info.config)
            consumers.append(
                ConsumerResult(
                    stream,
                    name,
                    "drift" if differences else "ready",
                    differences,
                    info.paused,
                    info.config.pause_until,
                )
            )
        report = TopologyReport(
            str(selected_installation_id), tuple(results), tuple(consumers)
        )
        return report
    finally:
        if own_connection is not None:
            await own_connection.close()
