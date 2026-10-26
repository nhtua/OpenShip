"""Broker-only pause/resume primitives for the two shared execution consumers.

Task 5 must commit pending operation records before these sends, serialize and
conflict-fence gate operations, and resolve unknown broker outcomes before any
conflicting action. Never hold an application transaction while awaiting NATS.
These primitives neither read nor mutate the authoritative database gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .topology import EXECUTION_CONSUMERS

# An explicit deadline, accepted by the pinned broker; this is not indefinite.
OPERATOR_PAUSE_UNTIL = "2100-01-01T00:00:00Z"


@dataclass(frozen=True)
class ConsumerGateResult:
    stream: str
    name: str
    confirmed: bool
    paused: bool | None = None
    pause_until: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BrokerGateReport:
    action: str
    consumers: tuple[ConsumerGateResult, ...]

    @property
    def complete(self) -> bool:
        return {(item.stream, item.name) for item in self.consumers} == set(
            EXECUTION_CONSUMERS
        ) and all(item.confirmed for item in self.consumers)

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "complete": self.complete,
            "consumers": [asdict(item) for item in self.consumers],
        }


async def _set_execution_pause(js, *, paused: bool, timeout: float) -> BrokerGateReport:
    results = []
    for stream, name in EXECUTION_CONSUMERS:
        try:
            if paused:
                state = await js.pause_consumer(
                    stream, name, OPERATOR_PAUSE_UNTIL, timeout=timeout
                )
                deadline_matches = datetime.fromisoformat(
                    state.pause_until
                ) == datetime.fromisoformat(OPERATOR_PAUSE_UNTIL)
            else:
                state = await js.resume_consumer(stream, name, timeout=timeout)
                deadline_matches = True
            confirmed = state.paused is paused and deadline_matches
            results.append(
                ConsumerGateResult(
                    stream,
                    name,
                    confirmed,
                    state.paused,
                    state.pause_until,
                    None if confirmed else "UnexpectedPauseState",
                )
            )
        except Exception as error:  # noqa: BLE001 - safe, explicit unknown outcome
            # A timeout does not prove the broker rejected the send. Continue to
            # report each resource, without attempting a conflicting rollback.
            results.append(
                ConsumerGateResult(stream, name, False, error=type(error).__name__)
            )
    return BrokerGateReport("pause" if paused else "resume", tuple(results))


async def pause_execution_consumers(js, *, timeout: float = 5.0) -> BrokerGateReport:
    """Request the explicit 2100 deadline; only broker confirmations complete it."""
    return await _set_execution_pause(js, paused=True, timeout=timeout)


async def resume_execution_consumers(js, *, timeout: float = 5.0) -> BrokerGateReport:
    """Explicitly resume each consumer, reporting partial/unknown outcomes."""
    return await _set_execution_pause(js, paused=False, timeout=timeout)
