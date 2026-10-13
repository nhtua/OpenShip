"""Recording the bounded generation requests a run made.

An attempt is metadata and an outcome only: no prompt, no provider response
body, and no credential ever belongs here. The recorder writes into the
session it is handed and never commits, so an attempt belongs to the same
transaction as the event and outbox row a worker is writing — a command that
fails leaves neither behind.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from src.openship.providers.configuration import destination_label
from src.openship.providers.contracts import PinnedProviderConfig, TokenUsage
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import ProviderAttempt, Run

OUTCOMES = ("started", "succeeded", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(UTC)


class DatabaseAttemptRecorder:
    """Records one run's attempts under the fence its worker holds.

    ``session_factory`` is called per operation so the recorder can be handed
    either the run's own session (to share a transaction) or a fresh one.
    """

    def __init__(self, session_factory, run_id: uuid.UUID, fence: int) -> None:
        self._session_of = session_factory
        self._run_id = run_id
        self._fence = fence

    def _run(self, session) -> Run:
        """The run, only while this worker still holds its lease."""
        run = session.get(Run, self._run_id)
        if run is None:
            raise ProviderError(
                "configuration_error",
                safe_message=f"run {self._run_id} does not exist in this installation",
            )
        if run.fence != self._fence:
            raise ProviderError(
                "lease_lost",
                safe_message=(
                    f"this worker holds fence {self._fence} of run {self._run_id}, "
                    f"which is now at fence {run.fence}"
                ),
            )
        return run

    def start(self, profile: PinnedProviderConfig, reason: str, reserved_cost_usd=None) -> uuid.UUID:
        """Open an attempt for one bounded request, and return its ID."""
        session = self._session_of()
        self._run(session)

        attempt_id = uuid.uuid4()
        session.add(
            ProviderAttempt(
                id=attempt_id,
                run_id=self._run_id,
                ordinal=self._next_ordinal(session),
                fence=self._fence,
                profile_id=profile.id,
                profile_revision=profile.profile_revision,
                adapter=profile.adapter,
                requested_model=profile.model,
                destination_label=destination_label(profile.endpoint),
                started_at=_now(),
                outcome="started",
                reserved_cost_usd=reserved_cost_usd,
            )
        )
        # Written into the caller's transaction, not committed: an attempt
        # belongs to the same command as the event and outbox row it opened.
        session.flush()
        return attempt_id

    def finish(
        self,
        attempt_id: uuid.UUID,
        outcome: str,
        usage: TokenUsage | None = None,
        error_code: str | None = None,
        actual_model: str | None = None,
    ) -> None:
        """Close an attempt with what it produced — counts, never content."""
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown attempt outcome: {outcome!r}")

        session = self._session_of()
        self._run(session)

        attempt = session.query(ProviderAttempt).filter(
            ProviderAttempt.id == attempt_id
        ).one()
        attempt.outcome = outcome
        attempt.ended_at = _now()
        attempt.error_code = error_code
        if actual_model is not None:
            attempt.actual_model = actual_model
        if usage is not None:
            attempt.usage = json.dumps(usage.model_dump(mode="json"), sort_keys=True)

    def _next_ordinal(self, session) -> int:
        """Attempts are numbered per run, in the order the worker made them."""
        recorded = [
            row[0]
            for row in session.query(ProviderAttempt.ordinal).filter(
                ProviderAttempt.run_id == self._run_id
            ).all()
        ]
        return max(recorded, default=0) + 1
