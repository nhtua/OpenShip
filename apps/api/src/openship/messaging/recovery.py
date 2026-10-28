"""Independent bounded scheduling for worker readiness and DB recovery."""

from __future__ import annotations

import asyncio
import logging
import math
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..connectors.blockers import expire_connector_blockers
from ..database.session import SessionLocal
from ..execution.availability import refresh_tool_availability
from ..runs.queue import reconcile_expired
from .executor import ExecutionPool

logger = logging.getLogger(__name__)


class _RecoveryResource:
    """Short-lived DB work on one maintenance-only execution thread."""

    def __init__(self, worker_id: str, session_factory):
        self.worker_id = worker_id
        self.session_factory = session_factory

    def refresh_tool_availability(self) -> None:
        self._with_session(lambda db: refresh_tool_availability(db, self.worker_id))

    def expire_connector_blockers(self) -> None:
        self._with_session(expire_connector_blockers)

    def reconcile_expired_leases(self) -> None:
        self._with_session(reconcile_expired)

    def _with_session(self, operation) -> None:
        db: Session = self.session_factory()
        try:
            operation(db)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self) -> None:
        """Satisfy the execution-resource lifecycle contract."""


def _run_action(resource: _RecoveryResource, action: str) -> None:
    getattr(resource, action)()


class RecoverySupervisor:
    """Run readiness and recovery timers outside broker run slots.

    Each scheduled action gets its own short-lived session. Failures are
    isolated to that action and retried on its next monotonic deadline.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        session_factory=SessionLocal,
        availability_interval: float = 10.0,
        recovery_interval: float | None = None,
    ):
        if not worker_id or len(worker_id) > 128:
            raise ValueError("recovery worker identity is invalid")
        if recovery_interval is None:
            recovery_interval = getattr(settings, "reconciler_interval", 60)
        if any(
            not math.isfinite(value) or value <= 0
            for value in (availability_interval, recovery_interval)
        ):
            raise ValueError("recovery intervals must be positive and finite")
        self.worker_id = worker_id
        self.session_factory = session_factory
        self.availability_interval = availability_interval
        self.recovery_interval = recovery_interval

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh availability and reconcile while this runtime is alive."""
        pool = ExecutionPool(
            1,
            lambda: _RecoveryResource(self.worker_id, self.session_factory),
        )
        try:
            async with pool.reserve() as slot:
                now = time.monotonic()
                next_availability = now
                next_connector_expiry = now
                next_lease_reconciliation = now
                while not stop.is_set():
                    now = time.monotonic()
                    if now >= next_availability:
                        await self._attempt(slot, "refresh_tool_availability")
                        next_availability = (
                            time.monotonic() + self.availability_interval
                        )
                    if now >= next_connector_expiry:
                        await self._attempt(slot, "expire_connector_blockers")
                        next_connector_expiry = (
                            time.monotonic() + self.recovery_interval
                        )
                    if now >= next_lease_reconciliation:
                        await self._attempt(slot, "reconcile_expired_leases")
                        next_lease_reconciliation = (
                            time.monotonic() + self.recovery_interval
                        )
                    if stop.is_set():
                        break
                    deadline = min(
                        next_availability,
                        next_connector_expiry,
                        next_lease_reconciliation,
                    )
                    timeout = max(0.0, deadline - time.monotonic())
                    try:
                        await asyncio.wait_for(stop.wait(), timeout)
                    except TimeoutError:
                        pass
        finally:
            await pool.close()

    async def _attempt(self, slot, action: str) -> None:
        try:
            await slot.run(_run_action, action)
        except Exception as error:  # noqa: BLE001 - isolate scheduled maintenance actions.
            logger.warning(
                "messaging_recovery_action_failed action=%s type=%s",
                action,
                type(error).__name__,
            )
