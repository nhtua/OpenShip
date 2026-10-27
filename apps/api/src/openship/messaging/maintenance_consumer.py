"""Bounded pull consumer for explicitly requested retention maintenance."""

from __future__ import annotations

import asyncio
import logging
import math
import uuid

from nats.errors import TimeoutError as NatsTimeoutError
from sqlalchemy.exc import SQLAlchemyError

from ..database.session import SessionLocal
from .executor import ExecutionPool
from .maintenance import (
    MaintenanceCommandError,
    RetentionClaim,
    claim_retention_task,
    execute_retention_claim,
    reconcile_retention_ack_floor,
    record_retention_ack,
    record_retention_ack_intent,
)
from .models import MessageTransportState
from .topology import TopologyCompatibilityError, ensure_topology

logger = logging.getLogger(__name__)


class _SlotResource:
    """Thread-owned cleanup hook for this session-per-operation consumer."""

    def close(self):
        return None


def _read_gate(_resource, session_factory):
    try:
        with session_factory() as db:
            state = db.get(MessageTransportState, 1)
            return (
                state.installation_id if state else None,
                bool(state and state.active and state.execution_open),
            )
    except SQLAlchemyError:
        return None, False


def _claim(_resource, session_factory, message) -> RetentionClaim:
    with session_factory() as db:
        return claim_retention_task(db, message.data, subject=message.subject)


def _execute(_resource, session_factory, claim):
    return execute_retention_claim(session_factory, claim)


def _record_ack(_resource, session_factory, task_id, *, stream_sequence):
    with session_factory() as db:
        return record_retention_ack(db, task_id, stream_sequence=stream_sequence)


def _record_ack_intent(
    _resource, session_factory, claim, stream_sequence, consumer_sequence
):
    with session_factory() as db:
        return record_retention_ack_intent(
            db,
            claim.task_id,
            message_id=claim.message_id,
            body_digest=claim.body_digest,
            stream_sequence=stream_sequence,
            consumer_sequence=consumer_sequence,
        )


def _reconcile_ack_floor(
    _resource, session_factory, stream_sequence, consumer_sequence
):
    with session_factory() as db:
        return reconcile_retention_ack_floor(
            db,
            stream_sequence=stream_sequence,
            consumer_sequence=consumer_sequence,
        )


def _message_sequences(message):
    try:
        metadata = message.metadata
        sequence = metadata.sequence
    except Exception:  # noqa: BLE001 - missing broker metadata cannot authorize ACK
        return None
    stream_sequence = getattr(sequence, "stream", None)
    consumer_sequence = getattr(sequence, "consumer", None)
    if (
        metadata.stream != "OS_MAINTENANCE"
        or metadata.consumer != "maintenance-v1"
        or type(stream_sequence) is not int
        or stream_sequence < 1
        or type(consumer_sequence) is not int
        or consumer_sequence < 1
    ):
        return None
    return stream_sequence, consumer_sequence


class RetentionConsumer:
    """Consume one bounded maintenance task at a time from the shared durable."""

    def __init__(
        self,
        client,
        *,
        session_factory=SessionLocal,
        fetch_timeout: float = 1.0,
        retry_interval: float = 0.25,
        ack_timeout: float = 2.0,
    ):
        for duration in (fetch_timeout, retry_interval, ack_timeout):
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(
                    "maintenance consumer timing must be positive and finite"
                )
        self._client = client
        self._js = client.jetstream(timeout=ack_timeout)
        self._session_factory = session_factory
        self._fetch_timeout = fetch_timeout
        self._retry_interval = retry_interval
        self._ack_timeout = ack_timeout
        self._installation_id: uuid.UUID | None = None
        self._checked_reconnect: int | None = None
        self._pool = ExecutionPool(1, _SlotResource)
        self._metadata = ExecutionPool(1, _SlotResource)
        self._ready = False
        self._next_ack_reconcile = 0.0

    @property
    def ready(self) -> bool:
        return self._ready

    async def _pause(self, stop: asyncio.Event):
        try:
            await asyncio.wait_for(stop.wait(), self._retry_interval)
        except TimeoutError:
            pass

    async def _fetch_allowed(self) -> bool:
        if not self._client.is_connected:
            self._checked_reconnect = None
            return False
        reconnect = self._client.stats["reconnects"]
        async with self._metadata.reserve() as slot:
            installation_id, execution_open = await slot.run(
                _read_gate, self._session_factory
            )
        if installation_id is None:
            return False
        if self._installation_id not in (None, installation_id):
            raise TopologyCompatibilityError("installation identity changed")
        self._installation_id = installation_id
        if self._checked_reconnect != reconnect:
            report = await ensure_topology(
                apply=False, js=self._js, installation_id=installation_id
            )
            if not report.ready:
                raise TopologyCompatibilityError("maintenance topology is not ready")
            self._checked_reconnect = reconnect
        return (
            execution_open
            and self._client.is_connected
            and self._client.stats["reconnects"] == self._checked_reconnect
        )

    async def _progress(self, message):
        while True:
            try:
                await message.in_progress()
            except Exception as error:  # noqa: BLE001 - broker progress is best effort
                logger.warning(
                    "retention_progress_unavailable type=%s", type(error).__name__
                )
            await asyncio.sleep(10.0)

    async def _settle(self, slot, message, claim: RetentionClaim):
        if claim.action == "ack":
            await self._ack_terminal_task(slot, message, claim)
            return
        if claim.action == "defer":
            await message.nak(delay=5.0)
            return
        confirmed = await slot.run(_execute, self._session_factory, claim)
        if confirmed:
            await self._ack_terminal_task(slot, message, claim)
        else:
            await message.nak(delay=5.0)

    async def _ack_terminal_task(self, slot, message, claim):
        sequences = _message_sequences(message)
        if sequences is None:
            await message.nak(delay=5.0)
            return
        stream_sequence, consumer_sequence = sequences
        if not await slot.run(
            _record_ack_intent,
            self._session_factory,
            claim,
            stream_sequence,
            consumer_sequence,
        ):
            await message.nak(delay=5.0)
            return
        await message.ack_sync(timeout=self._ack_timeout)
        try:
            recorded = await slot.run(
                _record_ack,
                self._session_factory,
                claim.task_id,
                stream_sequence=stream_sequence,
            )
        except Exception as error:  # noqa: BLE001 - ACK already succeeded
            logger.warning(
                "retention_ack_record_unavailable type=%s", type(error).__name__
            )
            return
        if not recorded:
            logger.warning("retention_ack_record_not_saved")

    async def _reconcile_acknowledgements(self):
        now = asyncio.get_running_loop().time()
        if now < self._next_ack_reconcile:
            return
        self._next_ack_reconcile = now + 5.0
        if (
            not self._client.is_connected
            or self._checked_reconnect != self._client.stats["reconnects"]
        ):
            return
        try:
            info = await self._js.consumer_info("OS_MAINTENANCE", "maintenance-v1")
            floor = info.ack_floor
            if floor is None:
                return
            stream_sequence = floor.stream_seq
            consumer_sequence = floor.consumer_seq
            if (
                type(stream_sequence) is not int
                or stream_sequence < 0
                or type(consumer_sequence) is not int
                or consumer_sequence < 0
            ):
                return
            async with self._metadata.reserve() as slot:
                reconciled = await slot.run(
                    _reconcile_ack_floor,
                    self._session_factory,
                    stream_sequence,
                    consumer_sequence,
                )
            if reconciled:
                logger.info("retention_ack_reconciled count=%s", reconciled)
        except Exception as error:  # noqa: BLE001 - retry on next interval
            logger.warning(
                "retention_ack_reconcile_unavailable type=%s", type(error).__name__
            )

    async def _handle(self, slot, message):
        progress = asyncio.create_task(self._progress(message))
        try:
            await asyncio.sleep(0)
            claim = await slot.run(_claim, self._session_factory, message)
            await self._settle(slot, message, claim)
        except MaintenanceCommandError:
            logger.warning("retention_command_invalid")
            try:
                await message.nak(delay=60.0)
            except Exception as error:  # noqa: BLE001 - retain an invalid source
                logger.warning(
                    "retention_invalid_settlement_unconfirmed type=%s",
                    type(error).__name__,
                )
        except Exception as error:  # noqa: BLE001 - keep uncertain tasks recoverable
            logger.warning(
                "retention_delivery_unconfirmed type=%s", type(error).__name__
            )
            try:
                await message.nak(delay=5.0)
            except Exception as settlement_error:  # noqa: BLE001
                logger.warning(
                    "retention_settlement_unconfirmed type=%s",
                    type(settlement_error).__name__,
                )
        finally:
            progress.cancel()
            await asyncio.gather(progress, return_exceptions=True)

    async def run(self, stop: asyncio.Event):
        """Bind only after topology and database gates permit task execution."""
        subscription = None
        try:
            while not stop.is_set():
                try:
                    fetch_allowed = await self._fetch_allowed()
                    await self._reconcile_acknowledgements()
                    if fetch_allowed:
                        break
                except Exception as error:  # noqa: BLE001 - retry readiness safely
                    logger.warning(
                        "retention_consumer_not_ready type=%s", type(error).__name__
                    )
                await self._pause(stop)
            if stop.is_set():
                return
            subscription = await self._js.pull_subscribe_bind(
                stream="OS_MAINTENANCE",
                consumer="maintenance-v1",
                pending_msgs_limit=1,
                pending_bytes_limit=16 * 1024,
            )
            self._ready = True
            while not stop.is_set():
                async with self._pool.reserve() as slot:
                    fetch_allowed = await self._fetch_allowed()
                    await self._reconcile_acknowledgements()
                    if not fetch_allowed:
                        await self._pause(stop)
                        continue
                    try:
                        messages = await subscription.fetch(
                            batch=1, timeout=self._fetch_timeout
                        )
                    except NatsTimeoutError:
                        continue
                    except Exception as error:  # noqa: BLE001 - reconnect rechecks topology
                        self._checked_reconnect = None
                        logger.warning(
                            "retention_fetch_unavailable type=%s", type(error).__name__
                        )
                        await self._pause(stop)
                        continue
                    if messages:
                        await self._handle(slot, messages[0])
        finally:
            self._ready = False
            results = await asyncio.gather(
                *((subscription.unsubscribe(),) if subscription is not None else ()),
                self._pool.close(),
                self._metadata.close(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(
                        "retention_consumer_cleanup_failed type=%s",
                        type(result).__name__,
                    )
