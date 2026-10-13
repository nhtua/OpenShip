"""The shared deadline and stop signal one model turn obeys.

A control owns no serializable state and no secrets: it is injected into an
invocation, never stored in graph state or a checkpoint.
"""

import time

from src.openship.providers.errors import ProviderError


class InvocationControl:
    """One deadline and one stop signal, shared across every attempt."""

    def __init__(self, *, deadline: float | None = None, clock=None, stop=None):
        self._deadline = deadline
        self._clock = clock or time.monotonic
        self._stop = stop or (lambda: False)

    def remaining_seconds(self) -> float:
        """Time left in the turn's budget."""
        if self._deadline is None:
            return float("inf")
        return max(0.0, self._deadline - self._clock())

    def request_timeout_seconds(self, limit: float) -> float:
        """A per-request timeout that never exceeds the turn's budget."""
        return min(limit, self.remaining_seconds())

    def is_stopped(self) -> bool:
        """Whether a supervisor has decided this turn should stop."""
        return bool(self._stop())

    def raise_if_stopped(self) -> None:
        """Stop before the next request or the next chunk."""
        if self._stop():
            raise ProviderError("cancelled")
