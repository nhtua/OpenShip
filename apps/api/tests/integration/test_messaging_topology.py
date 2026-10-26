"""Real-server qualification for the pinned NATS Python client contract."""

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import nats
import pytest
import pytest_asyncio
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig
from nats.js.errors import NotFoundError

from src.openship.messaging.topology import ensure_topology

pytestmark = [pytest.mark.jetstream, pytest.mark.asyncio]


@dataclass(repr=False)
class _RoleCredentials:
    user: str
    password: str = field(repr=False)


@dataclass(repr=False)
class _BrokerCredentials:
    url: str
    admin: _RoleCredentials
    restricted: _RoleCredentials

    def __repr__(self) -> str:
        return f"_BrokerCredentials(url={self.url!r}, credentials=<redacted>)"


@pytest.fixture(scope="module")
def broker_credentials():
    auth_file = os.environ.get("TEST_NATS_AUTH_FILE")
    config_file = os.environ.get("TEST_NATS_SERVER_CONFIG")
    url = os.environ.get("TEST_NATS_URL")
    if not auth_file or not config_file or not url:
        pytest.fail(
            "JetStream qualification requires TEST_NATS_AUTH_FILE and "
            "TEST_NATS_SERVER_CONFIG from the disposable broker launcher"
        )

    auth_path = Path(auth_file)
    assert auth_path.stat().st_mode & 0o077 == 0, "test credentials must be mode 0600"
    credentials = json.loads(auth_path.read_text())
    assert url.startswith("nats://127.0.0.1:")
    assert credentials["admin"]["user"] and credentials["admin"]["password"]
    assert credentials["restricted"]["user"] and credentials["restricted"]["password"]

    config = Path(config_file).read_text()
    assert re.search(r"sync_interval\s*[:=]\s*\"?100ms\"?", config)
    return _BrokerCredentials(
        url=url,
        admin=_RoleCredentials(**credentials["admin"]),
        restricted=_RoleCredentials(**credentials["restricted"]),
    )


@pytest_asyncio.fixture
async def broker(broker_credentials):
    nc = await nats.connect(
        broker_credentials.url,
        user=broker_credentials.admin.user,
        password=broker_credentials.admin.password,
        name="openship-jetstream-qualification",
        connect_timeout=2,
    )
    js = nc.jetstream()
    token = uuid.uuid4().hex.upper()
    stream = f"OS_TEST_{token}"
    subject = f"openship.test.{token.lower()}.>"
    await js.add_stream(
        config=StreamConfig(
            name=stream,
            subjects=[subject],
            storage=StorageType.FILE,
            max_age=300.0,
            duplicate_window=120.0,
        )
    )
    try:
        yield nc, js, stream, subject, token.lower()
    finally:
        try:
            await js.delete_stream(stream)
        except NotFoundError:
            pass
        await nc.close()


async def _pull(js, stream, subject, durable, *, filter_subject=None, ack_wait=1.0):
    return await js.pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=ConsumerConfig(
            durable_name=durable,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=ack_wait,
            max_deliver=5,
            max_ack_pending=1,
            filter_subject=filter_subject,
        ),
    )


async def test_file_storage_confirmed_publish_and_duplicate_identity(broker):
    _, js, stream, _, token = broker
    info = await js.stream_info(stream)
    assert info.config.storage == StorageType.FILE

    message_id = f"qualification-{token}"
    first = await js.publish(
        f"openship.test.{token}.events",
        b"first",
        headers={"Nats-Msg-Id": message_id},
    )
    duplicate = await js.publish(
        f"openship.test.{token}.events",
        b"first",
        headers={"Nats-Msg-Id": message_id},
    )
    assert first.stream == stream
    assert first.seq > 0
    assert duplicate.duplicate is True
    assert duplicate.seq == first.seq


async def test_progress_ack_extends_ack_wait_and_double_ack_confirms(broker):
    nc, js, stream, _, token = broker
    subject = f"openship.test.{token}.commands.run"
    subscription = await _pull(js, stream, subject, f"progress_{token}", ack_wait=1.0)
    await js.publish(subject, b"one")
    (message,) = await subscription.fetch(batch=1, timeout=1)
    await asyncio.sleep(0.4)
    await message.in_progress()
    await nc.flush()
    await asyncio.sleep(0.7)
    with pytest.raises(NatsTimeoutError):
        await subscription.fetch(batch=1, timeout=0.1)
    await message.ack_sync(timeout=1)


async def test_delayed_nak_and_consumer_filter(broker):
    _, js, stream, _, token = broker
    allowed = f"openship.test.{token}.commands.allowed"
    ignored = f"openship.test.{token}.events.ignored"
    subscription = await _pull(
        js,
        stream,
        allowed,
        f"filtered_{token}",
        filter_subject=allowed,
    )
    await js.publish(ignored, b"must-not-match")
    await js.publish(allowed, b"retry-me")
    (first_delivery,) = await subscription.fetch(batch=1, timeout=1)
    assert first_delivery.subject == allowed
    await first_delivery.nak(delay=0.4)
    await broker[0].flush()
    with pytest.raises(NatsTimeoutError):
        await subscription.fetch(batch=1, timeout=0.15)
    (redelivery,) = await subscription.fetch(batch=1, timeout=1)
    assert redelivery.subject == allowed
    assert redelivery.metadata.num_delivered == 2
    await redelivery.ack_sync(timeout=1)


async def test_restricted_user_cannot_create_stream(broker_credentials):
    restricted = broker_credentials.restricted
    permission_errors = []

    async def on_error(error):
        permission_errors.append(str(error))

    nc = await nats.connect(
        broker_credentials.url,
        user=restricted.user,
        password=restricted.password,
        error_cb=on_error,
        connect_timeout=2,
    )
    try:
        stream = f"DENIED_{uuid.uuid4().hex.upper()}"
        js = nc.jetstream(timeout=1)
        with pytest.raises(asyncio.TimeoutError):
            await js.add_stream(
                config=StreamConfig(
                    name=stream,
                    subjects=[f"openship.denied.{uuid.uuid4().hex}.>"],
                    storage=StorageType.FILE,
                )
            )
        assert any(
            "permissions violation" in error.lower() for error in permission_errors
        )

        admin = await nats.connect(
            broker_credentials.url,
            user=broker_credentials.admin.user,
            password=broker_credentials.admin.password,
            connect_timeout=2,
        )
        try:
            with pytest.raises(NotFoundError):
                await admin.jetstream().stream_info(stream)
        finally:
            await admin.close()
    finally:
        await nc.close()


async def test_bootstrap_check_and_apply_are_idempotent(broker):
    nc, js, _stream, _subject, _token = broker
    installation_id = uuid.uuid4()

    report = await ensure_topology(
        apply=False,
        js=js,
        installation_id=installation_id,
    )
    expected_names = {item.name for item in report.streams}
    assert len(expected_names) == 5
    assert {item.status for item in report.streams} == {"missing"}
    try:
        applied = await ensure_topology(
            apply=True,
            js=js,
            installation_id=installation_id,
        )
        assert {item.status for item in applied.streams} == {"ready"}

        checked = await ensure_topology(
            apply=False,
            js=js,
            installation_id=installation_id,
        )
        assert {item.status for item in checked.streams} == {"ready"}
    finally:
        for name in expected_names:
            try:
                await nc.jetstream().delete_stream(name)
            except NotFoundError:
                pass


async def test_bootstrap_apply_reports_drift_without_rewriting_stream(broker):
    nc, js, _stream, _subject, _token = broker
    installation_id = uuid.uuid4()
    from src.openship.messaging.topology import stream_configs

    expected = stream_configs(installation_id)[0]
    drifted = StreamConfig(
        name=expected.name,
        subjects=["openship.wrong.v1.command.run.execute"],
        storage=StorageType.MEMORY,
        max_bytes=1,
        max_consumers=3,
        max_msgs=1,
        max_msgs_per_subject=5,
    )
    await js.add_stream(config=drifted)
    try:
        report = await ensure_topology(
            apply=True,
            js=js,
            installation_id=installation_id,
        )
        first = next(item for item in report.streams if item.name == expected.name)
        assert first.status == "drift"
        assert "max_msgs" in first.differences
        assert "max_msgs_per_subject" in first.differences
        assert "max_consumers" in first.differences
        info = await js.stream_info(expected.name)
        assert info.config.subjects == drifted.subjects
        assert info.config.storage == StorageType.MEMORY
    finally:
        for config in stream_configs(installation_id):
            try:
                await nc.jetstream().delete_stream(config.name)
            except NotFoundError:
                pass


@pytest_asyncio.fixture
async def execution_topology(broker):
    """Disposable exact resources; no production gate or runtime is activated."""
    from src.openship.messaging.topology import stream_configs

    _, js, *_ = broker
    installation_id = uuid.uuid4()
    try:
        assert (
            await ensure_topology(apply=True, js=js, installation_id=installation_id)
        ).ready
        yield js, installation_id
    finally:
        for config in stream_configs(installation_id):
            try:
                await js.delete_stream(config.name)
            except NotFoundError:
                pass


async def test_shared_durables_reapply_preserves_pending_ack_progress_and_pause(
    execution_topology,
):
    from src.openship.messaging.gates import pause_execution_consumers

    js, installation_id = execution_topology
    subject = f"openship.{installation_id.hex}.v1.command.run.execute"
    subscription = await js.pull_subscribe_bind(stream="OS_RUNS", durable="run-v1")
    for body in (b"one", b"two", b"three"):
        await js.publish(subject, body)
    first, second = await subscription.fetch(batch=2, timeout=1)
    await first.ack_sync(timeout=1)
    before = await js.consumer_info("OS_RUNS", "run-v1")
    assert before.num_ack_pending == 1
    assert before.num_pending == 1
    assert before.config.ack_wait == 30
    assert before.config.max_deliver == 10
    assert before.config.max_ack_pending == before.config.max_waiting == 4
    assert before.config.num_replicas == 1
    assert not before.config.mem_storage
    assert not before.config.inactive_threshold
    assert not before.config.backoff
    assert (await pause_execution_consumers(js)).complete
    for apply in (False, True, True):
        report = await ensure_topology(
            apply=apply, js=js, installation_id=installation_id
        )
        assert report.ready
        assert all(item.paused for item in report.consumers)
        assert all(
            item.pause_until == "2100-01-01T00:00:00Z" for item in report.consumers
        )
        after = await js.consumer_info("OS_RUNS", "run-v1")
        assert after.created == before.created
        assert after.delivered == before.delivered
        assert after.ack_floor == before.ack_floor
        assert after.num_ack_pending == 1
        assert after.num_pending == 1
    await second.ack_sync(timeout=1)
    await subscription.unsubscribe()


async def test_paused_consumer_blocks_delivery_and_redelivery_until_explicit_resume(
    execution_topology,
):
    from dataclasses import replace

    from src.openship.messaging.gates import (
        pause_execution_consumers,
        resume_execution_consumers,
    )

    js, installation_id = execution_topology
    subject = f"openship.{installation_id.hex}.v1.command.run.execute"
    original = await js.consumer_info("OS_RUNS", "run-v1")
    assert original.config.ack_wait == 30
    # Short AckWait only in this dedicated disposable transport fixture.
    await js.add_consumer("OS_RUNS", config=replace(original.config, ack_wait=0.25))
    subscription = await js.pull_subscribe_bind(stream="OS_RUNS", durable="run-v1")
    await js.publish(subject, b"existing")
    (first,) = await subscription.fetch(batch=1, timeout=1)
    paused = await pause_execution_consumers(js)
    assert paused.complete
    before = await js.consumer_info("OS_RUNS", "run-v1")
    await js.publish(subject, b"new")
    with pytest.raises(NatsTimeoutError):
        await subscription.fetch(batch=2, timeout=0.7)
    after = await js.consumer_info("OS_RUNS", "run-v1")
    assert after.paused
    assert after.delivered == before.delivered
    assert after.num_redelivered == before.num_redelivered == 0
    assert after.num_ack_pending == 1
    assert after.num_pending == 1
    resumed = await resume_execution_consumers(js)
    assert resumed.complete
    assert all(item.paused is False for item in resumed.consumers)
    messages = await subscription.fetch(batch=2, timeout=1)
    old = next(item for item in messages if item.data == b"existing")
    assert old.metadata.sequence.stream == first.metadata.sequence.stream
    assert old.metadata.num_delivered == 2
    assert {item.data for item in messages} == {b"existing", b"new"}
    for message in messages:
        await message.ack_sync(timeout=1)
    final = await js.consumer_info("OS_RUNS", "run-v1")
    assert final.created == original.created
    assert final.num_ack_pending == 0
    await subscription.unsubscribe()


async def test_missing_consumer_detected_with_all_streams_present(execution_topology):
    js, installation_id = execution_topology
    await js.delete_consumer("OS_MAINTENANCE", "maintenance-v1")
    checked = await ensure_topology(apply=False, js=js, installation_id=installation_id)
    assert not checked.ready
    assert all(item.status == "ready" for item in checked.streams)
    assert checked.consumers[1].status == "missing"
    with pytest.raises(NotFoundError):
        await js.consumer_info("OS_MAINTENANCE", "maintenance-v1")
    assert (
        await ensure_topology(apply=True, js=js, installation_id=installation_id)
    ).ready


async def test_runtime_role_binds_fetches_acks_but_cannot_mutate_topology_or_pause(
    execution_topology,
    broker_credentials,
):
    from dataclasses import replace

    js, installation_id = execution_topology
    auth = json.loads(Path(os.environ["TEST_NATS_AUTH_FILE"]).read_text())["runtime"]
    permission_errors = []

    async def on_error(error):
        permission_errors.append(str(error))

    nc = await nats.connect(
        broker_credentials.url,
        user=auth["user"],
        password=auth["password"],
        inbox_prefix=b"_INBOX.runtime",
        error_cb=on_error,
        connect_timeout=2,
    )
    runtime = nc.jetstream(timeout=0.2)
    try:
        assert (
            await ensure_topology(
                apply=False, js=runtime, installation_id=installation_id
            )
        ).ready
        for stream, durable, suffix in (
            ("OS_RUNS", "run-v1", "run.execute"),
            ("OS_MAINTENANCE", "maintenance-v1", "maintenance.retention"),
        ):
            # Bind only: pull_subscribe would attempt implicit creation.
            subscription = await runtime.pull_subscribe_bind(
                stream=stream, durable=durable
            )
            peer = await runtime.pull_subscribe_bind(stream=stream, durable=durable)
            subject = f"openship.{installation_id.hex}.v1.command.{suffix}"
            await js.publish(subject, b"role-check")
            (message,) = await subscription.fetch(batch=1, timeout=1)
            await message.ack_sync(timeout=1)
            info = await js.consumer_info(stream, durable)
            assert info.num_ack_pending == 0
            assert info.ack_floor.stream_seq == message.metadata.sequence.stream
            before = info
            attempts = (
                partial(
                    runtime.add_consumer,
                    stream,
                    config=replace(info.config, ack_wait=31),
                ),
                partial(
                    runtime.add_consumer,
                    stream,
                    config=ConsumerConfig(
                        durable_name="unapproved",
                        filter_subject=subject,
                    ),
                ),
                partial(runtime.delete_consumer, stream, durable),
                partial(
                    runtime.pause_consumer, stream, durable, "2100-01-01T00:00:00Z"
                ),
                partial(runtime.resume_consumer, stream, durable),
                partial(runtime.delete_stream, stream),
                partial(
                    runtime.update_stream, config=StreamConfig(name=stream, max_bytes=1)
                ),
                partial(
                    runtime.add_stream,
                    config=StreamConfig(
                        name="RUNTIME_DENIED", subjects=["openship.denied.>"]
                    ),
                ),
            )
            for attempt in attempts:
                with pytest.raises(NatsTimeoutError):
                    await attempt()
            after = await js.consumer_info(stream, durable)
            assert after.created == before.created
            assert after.config.ack_wait == 30
            assert not after.paused
            await subscription.unsubscribe()
            await peer.unsubscribe()
        assert len(permission_errors) >= 16
        with pytest.raises(NotFoundError):
            await js.stream_info("RUNTIME_DENIED")
    finally:
        await nc.close()
