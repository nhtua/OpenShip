"""Opt-in bounded run pulls; the production Worker entry remains unchanged.

Each loop binds its own private inbox to the existing shared durable. Admission,
execution and settlement share one reserved thread-owned resource. Transport
progress runs on the event loop and never renews a PostgreSQL lease.
"""

# Worker/provider and injected durable-handler exceptions are open boundaries;
# retain delivery and report safe types rather than allowing arbitrary payloads
# into exception logs.
# ruff: noqa: BLE001

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from nats.errors import TimeoutError as NatsTimeoutError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import settings
from ..database.session import SessionLocal
from ..runs.worker import Supervision, Worker
from .delivery import (
    DeliveryDisposition,
    PreparedDelivery,
    prepare_run_delivery,
    settle_run_delivery,
)
from .executor import ExecutionPool
from .health import subscribe_worker_health
from .models import MessageTransportState
from .recovery import RecoverySupervisor
from .topology import TopologyCompatibilityError, ensure_topology

logger = logging.getLogger(__name__)

# A True result promises a committed durable disposition/handoff and any
# required confirmed failure publication. An absent/uncertain handler must not
# release the original delivery. B2e supplies the production implementations.
ConfirmedHandler = Callable[[Session, PreparedDelivery, DeliveryDisposition], bool]


@dataclass(frozen=True)
class _Gate:
    installation_id: uuid.UUID | None
    open: bool


def _read_gate(db: Session) -> _Gate:
    try:
        db.rollback()
        state = db.get(MessageTransportState, 1)
        return _Gate(
            state.installation_id if state else None,
            bool(state and state.active and state.execution_open),
        )
    except SQLAlchemyError:
        return _Gate(None, False)
    finally:
        db.rollback()


class _RunResource:
    def __init__(self, worker_factory):
        self.worker = worker_factory()
        try:
            self.db = self.worker.Session()
        except BaseException:
            self.worker.close()
            raise

    def close(self):
        try:
            self.db.close()
        finally:
            self.worker.close()


def _prepare(resource, message, lease_seconds):
    return prepare_run_delivery(
        resource.db,
        message.data,
        subject=message.subject,
        worker_id=resource.worker.worker_id,
        lease_seconds=lease_seconds,
    )


def _execute(resource, prepared, supervision):
    try:
        # A returned boolean is deliberately ignored. Even raised execution
        # may have committed a result; settlement is the only ACK authority.
        claim = prepared.decision.claim
        try:
            ownership_lost = resource.worker._ownership_lost(
                resource.db, claim.run_id, claim.fence
            )
        finally:
            # The ownership query must not hold a transaction open for the
            # duration of a model turn on this slot's long-lived session.
            resource.db.rollback()
        if ownership_lost:
            supervision.deny_lease()
        else:
            resource.worker.process_job(claim, supervision=supervision)
    except Exception as error:
        logger.warning("run_execution_failed type=%s", type(error).__name__)
    return settle_run_delivery(resource.db, prepared.command, prepared.decision.claim)


def _confirm(resource, handler, prepared, disposition):
    try:
        return handler(resource.db, prepared, disposition) is True
    except Exception as error:
        logger.warning("run_handoff_unconfirmed type=%s", type(error).__name__)
        return False
    finally:
        # A confirmed handler must have committed already. Uncertain handlers
        # must not leave staged writes/locks open while we retain delivery.
        resource.db.rollback()


class RunConsumer:
    """Non-production orchestration over a caller-owned connected NATS client.

    Connection liveness and the client's monotonic reconnect counter prevent
    pulls before reconnect topology/gate validation. The caller owns connection
    shutdown. This consumer only unsubscribes its private inboxes, never deletes
    or modifies a shared stream/consumer.
    """

    def __init__(
        self,
        client,
        *,
        concurrency: int | None = None,
        session_factory=SessionLocal,
        worker_factory=None,
        lease_seconds: int = 30,
        progress_interval: float = 10.0,
        fetch_timeout: float = 1.0,
        retry_interval: float = 0.25,
        ack_timeout: float = 2.0,
        handlers: Mapping[str, ConfirmedHandler] | None = None,
        recovery_supervisor: RecoverySupervisor | None = None,
    ):
        self.concurrency = (
            settings.worker_run_concurrency if concurrency is None else concurrency
        )
        if (
            type(self.concurrency) is not int
            or not 1 <= self.concurrency <= settings.messaging_run_max_ack_pending
        ):
            raise ValueError("run slots must fit the provisioned aggregate capacity")
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("lease must be positive")
        for duration in (progress_interval, fetch_timeout, retry_interval, ack_timeout):
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("consumer timing must be positive and finite")
        self._client = client
        self._js = client.jetstream(timeout=ack_timeout)
        self._worker_id = f"worker-{uuid.uuid4().hex}"
        self._lease_seconds = lease_seconds
        self._progress_interval, self._fetch_timeout = progress_interval, fetch_timeout
        self._retry_interval, self._ack_timeout = retry_interval, ack_timeout
        self._handlers = dict(handlers or {})
        owner_id = self._worker_id
        if worker_factory is None:

            def worker_factory():
                worker = Worker(
                    worker_id=owner_id,
                    session_factory=session_factory,
                    refresh_availability_during_run=False,
                )
                if worker.checkpointer is None:
                    worker.close()
                    raise RuntimeError("run consumer checkpointer is unavailable")
                return worker

        self._pool = ExecutionPool(
            self.concurrency, lambda: _RunResource(worker_factory)
        )
        self._recovery = recovery_supervisor or RecoverySupervisor(
            worker_id=owner_id,
            session_factory=session_factory,
        )
        self._recovery_task: asyncio.Task | None = None
        self._consumer_tasks: tuple[asyncio.Task, ...] = ()
        self._intake_ready = False
        # Metadata has independent bounded capacity; never queues behind graphs
        # or uses the application's shared/default executor.
        self._metadata = ExecutionPool(1, session_factory)
        self._check_lock = asyncio.Lock()
        self._checked_reconnect = None
        self._installation_id = None

    async def _pause(self, stop):
        try:
            await asyncio.wait_for(stop.wait(), self._retry_interval)
        except TimeoutError:
            pass

    async def _fetch_allowed(self):
        if not self._client.is_connected:
            self._checked_reconnect = None
            return False
        async with self._check_lock:
            reconnect = self._client.stats["reconnects"]
            async with self._metadata.reserve() as slot:
                gate = await slot.run(_read_gate)
            if gate.installation_id is None:
                return False
            if self._installation_id not in (None, gate.installation_id):
                raise TopologyCompatibilityError("installation identity changed")
            self._installation_id = gate.installation_id
            if self._checked_reconnect != reconnect:
                report = await ensure_topology(
                    apply=False, js=self._js, installation_id=gate.installation_id
                )
                if not report.ready:
                    raise TopologyCompatibilityError("run topology is not ready")
                self._checked_reconnect = reconnect
            return (
                gate.open
                and self._client.is_connected
                and self._client.stats["reconnects"] == self._checked_reconnect
            )

    def _health_runtime_ready(self, stop: asyncio.Event) -> bool:
        recovery_task = self._recovery_task
        return (
            not stop.is_set()
            and self._intake_ready
            and bool(self._consumer_tasks)
            and all(not task.done() for task in self._consumer_tasks)
            and recovery_task is not None
            and not recovery_task.done()
        )

    async def _health_ready(self, stop: asyncio.Event) -> bool:
        """Answer only while intake, recovery, and current broker checks are ready."""
        if not self._health_runtime_ready(stop):
            return False
        try:
            if not await self._fetch_allowed():
                return False
            return self._health_runtime_ready(stop)
        except Exception as error:
            logger.warning("worker_health_check_failed type=%s", type(error).__name__)
            return False

    async def run(self, stop: asyncio.Event):
        """Drain owned work on stop; retain unclaimed/unconfirmed deliveries."""
        subscriptions = []
        tasks = []
        recovery_task = None
        health_subscription = None
        try:
            recovery_task = asyncio.create_task(self._recovery.run(stop))
            while not stop.is_set():
                if await self._fetch_allowed():
                    break
                await self._pause(stop)
            if stop.is_set():
                return
            self._recovery_task = recovery_task
            health_subscription = await subscribe_worker_health(
                self._client,
                installation_id=self._installation_id,
                is_ready=lambda: self._health_ready(stop),
            )
            for _ in range(self.concurrency):
                subscriptions.append(
                    await self._js.pull_subscribe_bind(
                        stream="OS_RUNS",
                        consumer="run-v1",
                        pending_msgs_limit=1,
                        pending_bytes_limit=16 * 1024,
                    )
                )
            started = [asyncio.Event() for _ in subscriptions]
            tasks = [
                asyncio.create_task(self._consume(sub, stop, ready))
                for sub, ready in zip(subscriptions, started, strict=True)
            ]
            self._consumer_tasks = tuple(tasks)
            await asyncio.gather(*(ready.wait() for ready in started))
            self._intake_ready = not stop.is_set() and all(
                not task.done() for task in tasks
            )
            await asyncio.gather(*tasks)
        finally:
            self._intake_ready = False
            self._consumer_tasks = ()
            # On peer failure/cancellation, join synchronous work before closing
            # its thread-owned session/Worker and release every private inbox.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if recovery_task is not None:
                if not stop.is_set() and not recovery_task.done():
                    recovery_task.cancel()
                await asyncio.gather(recovery_task, return_exceptions=True)
            results = await asyncio.gather(
                *(sub.unsubscribe() for sub in subscriptions),
                *(
                    (health_subscription.unsubscribe(),)
                    if health_subscription is not None
                    else ()
                ),
                self._pool.close(),
                self._metadata.close(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(
                        "run_consumer_cleanup_failed type=%s", type(result).__name__
                    )

    async def _consume(self, subscription, stop, started: asyncio.Event | None = None):
        if started is not None:
            started.set()
        while not stop.is_set():
            async with self._pool.reserve() as slot:
                if not await self._fetch_allowed():
                    await self._pause(stop)
                    continue
                if stop.is_set():
                    return
                try:
                    messages = await subscription.fetch(
                        batch=1, timeout=self._fetch_timeout
                    )
                except NatsTimeoutError:
                    continue
                except Exception as error:
                    logger.warning(
                        "run_fetch_unavailable type=%s", type(error).__name__
                    )
                    self._checked_reconnect = None
                    await self._pause(stop)
                    continue
                if messages:
                    await self._handle(slot, messages[0], stop)

    async def _subscribe_control(self, prepared, supervision):
        command = prepared.command
        if (
            command is None
            or prepared.decision is None
            or prepared.decision.claim is None
        ):
            raise ValueError("control subscription requires an admitted run claim")
        subject = (
            f"openship.{command.installation_id.hex}.v1.control.{command.run_id.hex}"
        )

        async def wake_owner(_message):
            # The notice only wakes the owner thread; PostgreSQL decides whether
            # this claim is still current before it interrupts model/tool work.
            supervision.wake()

        subscription = await self._client.subscribe(
            subject,
            cb=wake_owner,
            pending_msgs_limit=1,
            pending_bytes_limit=16 * 1024,
        )
        try:
            await self._client.flush(timeout=self._ack_timeout)
        except BaseException:
            await self._unsubscribe_control(subscription)
            raise
        return subscription

    async def _unsubscribe_control(self, subscription):
        try:
            await subscription.unsubscribe()
        except Exception as error:
            # Closing the connection already removes its subscriptions. Preserve
            # durable run settlement if that happens while an execution drains.
            logger.warning(
                "run_control_unsubscribe_unavailable type=%s",
                type(error).__name__,
            )

    async def _execute_controlled(self, slot, prepared):
        supervision = Supervision()
        subscription = await self._subscribe_control(prepared, supervision)
        try:
            # The synchronous path's first action is an authoritative check,
            # now that cancellation notices for this run are being received.
            return await slot.run(
                _execute,
                prepared,
                supervision,
                stop=supervision.interrupt,
            )
        finally:
            await self._unsubscribe_control(subscription)

    async def _progress(self, message):
        while True:
            try:
                await message.in_progress()
            except Exception as error:
                logger.warning("run_progress_unavailable type=%s", type(error).__name__)
            await asyncio.sleep(self._progress_interval)

    async def _handle(self, slot, message, stop):
        progress = asyncio.create_task(self._progress(message))
        try:
            # Let progress start before lazy resource/Worker construction.
            await asyncio.sleep(0)
            while not stop.is_set():
                prepared = await slot.run(_prepare, message, self._lease_seconds)
                disposition = prepared.disposition
                if disposition is None:
                    disposition = await self._execute_controlled(slot, prepared)
                if disposition.action == "hold":
                    while not stop.is_set() and not await self._fetch_allowed():
                        await self._pause(stop)
                    continue
                if disposition.action in {"quarantine", "reconcile"}:
                    handler = self._handlers.get(disposition.action)
                    confirmed = handler is not None and await slot.run(
                        _confirm, handler, prepared, disposition
                    )
                    if not confirmed:
                        logger.warning(
                            "run_handoff_unavailable code=%s", disposition.code
                        )
                        await stop.wait()
                        return
                    disposition = DeliveryDisposition("ack", "durable_handoff")
                try:
                    if disposition.action == "ack":
                        await message.ack_sync(timeout=self._ack_timeout)
                    elif disposition.action == "defer":
                        await message.nak(
                            delay=min(60.0, max(1.0, disposition.delay or 1.0))
                        )
                except Exception as error:
                    # A lost confirmed ACK never changes the committed DB result;
                    # redelivery repeats only durable preparation, not an effect.
                    logger.warning(
                        "run_settlement_unconfirmed type=%s", type(error).__name__
                    )
                return
        finally:
            progress.cancel()
            await asyncio.gather(progress, return_exceptions=True)
