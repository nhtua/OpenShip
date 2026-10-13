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

from src.openship.events.service import append_event
from src.openship.providers.configuration import destination_label
from src.openship.providers.contracts import PinnedProviderConfig, TokenUsage
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import ProviderAttempt, Run

OUTCOMES = ("started", "succeeded", "failed", "cancelled")

#: The three names this build adds to the durable stream. They state the same
#: facts as the attempt rows, in the order a browser reads them; the rows stay
#: the record because they carry what an event cannot - a fence, a reservation,
#: and the ordering a retry is judged under.
ATTEMPT_STARTED = "model.attempt.started"
ATTEMPT_FINISHED = "model.attempt.finished"
FALLBACK_SELECTED = "model.fallback.selected"


def _now() -> datetime:
    return datetime.now(UTC)


class DatabaseAttemptRecorder:
    """Records one run's attempts under the fence its worker holds.

    A row is written the moment its request is admitted - before the network
    call - so the ordering a retry is judged under exists before the answer
    does. The matching **statement** in the durable stream is held back and
    written by ``publish`` at the run's commit point, in the same command as
    the rows and the publication.

    Holding them back is the whole point, not a style choice. Appending an
    event takes the conversation's row lock; taking it when a request is
    admitted would hold that lock for the whole model turn, and a cancellation
    the owner asked would wait behind the turn instead of interrupting it -
    the bounded window this repo claims for local provider I/O. It would also
    ask a second session to write while the worker's transaction is open,
    which SQLite refuses. One writer, one lock, taken once, at the end.

    A run that lost its lease therefore publishes neither rows nor statements:
    the fence promises that a command which is not this worker's leaves no
    record, and a reader never sees an attempt the run's own record denies.
    """

    def __init__(self, session_factory, run_id: uuid.UUID, fence: int) -> None:
        self._session_of = session_factory
        self._run_id = run_id
        self._fence = fence
        #: Statements waiting for the run's commit point, in the order made.
        self._pending: list[tuple[uuid.UUID, str, dict]] = []

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

    def _say(self, run: Run, event_type: str, facts: dict) -> None:
        """Hold the statement of what this recorder just recorded.

        The payload is an attempt's metadata only: a profile, an adapter, a
        destination label, an outcome. No prompt, no answer, no credential -
        none of those belong in a record a browser reads.
        """
        self._pending.append((run.conversation_id, event_type, facts))

    def publish(self, session) -> int:
        """Write the statements this run made, in the order it made them.

        Called from inside the worker's ``if finalized:`` branch, so the
        statements land with the publication they describe and roll back with
        it. Returns how many were written, which is what a caller that wants
        to say "this run asked twice" reads.
        """
        written = 0
        for conversation_id, event_type, facts in self._pending:
            append_event(
                db=session,
                conversation_id=conversation_id,
                run_id=self._run_id,
                event_type=event_type,
                payload=facts,
                actor_id=None,
            )
            written += 1
        self._pending.clear()
        return written

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
    def _tell(self, session, run: Run, event_type: str, facts: dict) -> None:
        """State what this recorder just recorded, on its own transaction.

        The payload is an attempt's metadata only: a profile, an adapter, a
        destination label, an outcome. No prompt, no answer, no credential -
        none of those belong in a record a browser reads.

        When a statement session was supplied the event is committed there, so
        the conversation's lock is taken and released inside one short
        transaction rather than held across the turn. Without one, the
        statement lands in the caller's transaction - which is what a test
        installation with a single session wants.
        """
        stated_separately = self._statement_of is not None
        where = self._statement_of() if stated_separately else session
        append_event(
            db=where,
            conversation_id=run.conversation_id,
            run_id=self._run_id,
            event_type=event_type,
            payload=facts,
            actor_id=None,
        )
        if stated_separately:
            where.commit()
            where.close()

    def start(self, profile: PinnedProviderConfig, reason: str, reserved_cost_usd=None) -> uuid.UUID:
        """Open an attempt for one bounded request, and return its ID."""
        session = self._session_of()
        run = self._run(session)

        ordinal = self._next_ordinal(session)
        attempt_id = uuid.uuid4()
        session.add(
            ProviderAttempt(
                id=attempt_id,
                run_id=self._run_id,
                ordinal=ordinal,
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

        self._say(
            run,
            ATTEMPT_STARTED,
            {
                "profile_id": profile.id,
                "profile_revision": profile.profile_revision,
                "adapter": profile.adapter,
                "requested_model": profile.model,
                "destination_label": destination_label(profile.endpoint),
                "ordinal": ordinal,
                "reason": reason,
            },
        )
        if reason == "fallback":
            # Moving to another profile is a decision, not another request, so
            # it is stated as its own event: the pair a reader needs is which
            # profile a run stopped asking and the one it began asking.
            previous = self._asked(session, ordinal - 1)
            self._say(
                run,
                FALLBACK_SELECTED,
                {
                    "from_profile_id": previous[0] if previous else None,
                    "from_adapter": previous[1] if previous else None,
                    "from_model": previous[2] if previous else None,
                    "to_profile_id": profile.id,
                    "to_adapter": profile.adapter,
                    "to_model": profile.model,
                    "destination_label": destination_label(profile.endpoint),
                },
            )
        return attempt_id

    def _asked(self, session, ordinal: int) -> tuple | None:
        """The profile the previous ordinal was asked for, if any."""
        previous = (
            session.query(ProviderAttempt)
            .filter(
                ProviderAttempt.run_id == self._run_id,
                ProviderAttempt.ordinal == ordinal,
            )
            .first()
        )
        if previous is None:
            return None
        return previous.profile_id, previous.adapter, previous.requested_model

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
        run = self._run(session)

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

        self._say(
            run,
            ATTEMPT_FINISHED,
            {
                "profile_id": attempt.profile_id,
                "profile_revision": attempt.profile_revision,
                "adapter": attempt.adapter,
                "requested_model": attempt.requested_model,
                "actual_model": attempt.actual_model,
                "destination_label": attempt.destination_label,
                "ordinal": attempt.ordinal,
                "outcome": outcome,
                "error_code": error_code,
                "usage": usage.model_dump(mode="json") if usage is not None else None,
            },
        )

    def _next_ordinal(self, session) -> int:
        """Attempts are numbered per run, in the order the worker made them."""
        recorded = [
            row[0]
            for row in session.query(ProviderAttempt.ordinal).filter(
                ProviderAttempt.run_id == self._run_id
            ).all()
        ]
        return max(recorded, default=0) + 1
