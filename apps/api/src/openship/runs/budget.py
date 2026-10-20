"""Durable cumulative model and tool budgets for a tool-enabled chat run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from ..chat.models import Conversation
from ..providers.attempts import DatabaseAttemptRecorder
from ..providers.errors import ProviderError
from .models import ProviderAttempt, Run
from .queue import Claim
from .transcript import read_transcript


@dataclass(frozen=True)
class BudgetReservation:
    """The attempt identity admitted by the same transaction as its budget."""

    attempt_id: UUID


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RunBudget:
    """Reserve work against one run's pinned limits under its current lease."""

    def __init__(self, session_factory: sessionmaker, claim: Claim, limits, execution):
        self._session_factory = session_factory
        self._claim = claim
        self._limits = limits
        self._execution = execution

    def reserve_generation(
        self,
        profile,
        reason: str,
        model_turn_ordinal: int,
        requested_output_tokens: int,
        reserved_tokens: int | None,
        reserved_cost: Decimal | None,
    ) -> BudgetReservation:
        """Atomically admit one model request and record its reservation."""
        from ..execution.admission import require_live_claim

        loop = self._execution.loop_limits
        output_limit = min(
            self._limits.max_output_tokens,
            loop.max_output_tokens_per_request,
        )
        attempt_limit = min(
            self._limits.max_attempts,
            loop.max_generation_attempts,
        )
        if model_turn_ordinal < 0 or model_turn_ordinal >= loop.max_model_turns:
            raise ProviderError("budget_exceeded")
        if requested_output_tokens < 1 or requested_output_tokens > output_limit:
            raise ProviderError(
                "budget_exceeded",
                safe_message=(
                    f"the request reserves {requested_output_tokens} output tokens, "
                    f"above the {output_limit} per-request limit"
                ),
            )
        if reserved_tokens is not None and reserved_tokens < requested_output_tokens:
            raise ProviderError("invalid_request", safe_message="The model token reservation is invalid.")

        db = self._session_factory()
        try:
            run_identity = db.get(Run, self._claim.run_id)
            if run_identity is None:
                raise ProviderError("lease_lost")
            conversation = (
                db.query(Conversation)
                .filter(Conversation.id == run_identity.conversation_id)
                .with_for_update()
                .first()
            )
            if conversation is None:
                raise ProviderError("lease_lost")
            run = require_live_claim(db, self._claim, lock=True)
            if (
                run.kind != "chat"
                or run.graph_version != 2
                or self._execution.graph_kind != "chat"
                or self._execution.graph_version != 2
            ):
                raise ProviderError("unsupported_capability")

            now = datetime.now(UTC)
            if run.execution_started_at is None and run.execution_deadline_at is None:
                run.execution_started_at = now
                run.execution_deadline_at = now + timedelta(
                    seconds=loop.deadline_seconds
                )
            elif run.execution_started_at is None or run.execution_deadline_at is None:
                raise ProviderError("configuration_error")
            if _utc(run.execution_deadline_at) <= now:
                raise ProviderError("budget_exceeded")

            attempts = (
                db.query(ProviderAttempt)
                .filter(ProviderAttempt.run_id == run.id)
                .all()
            )
            if len(attempts) >= attempt_limit:
                raise ProviderError("budget_exceeded")
            output_reserved = sum(
                row.reserved_output_tokens
                if row.reserved_output_tokens is not None
                else loop.max_output_tokens_per_request
                for row in attempts
            )
            if output_reserved + requested_output_tokens > loop.max_total_output_tokens:
                raise ProviderError("budget_exceeded")

            if self._limits.max_total_tokens is not None:
                if reserved_tokens is None:
                    raise ProviderError(
                        "configuration_error",
                        safe_message="The run cannot reserve its configured token ceiling.",
                    )
                token_reserved = sum(
                    row.reserved_total_tokens
                    if row.reserved_total_tokens is not None
                    else self._limits.max_total_tokens
                    for row in attempts
                )
                if token_reserved + reserved_tokens > self._limits.max_total_tokens:
                    raise ProviderError("budget_exceeded")

            if self._limits.max_cost_usd is not None:
                if reserved_cost is None:
                    raise ProviderError(
                        "configuration_error",
                        safe_message="The run cannot reserve its configured cost ceiling.",
                    )
                cost_reserved = sum(
                    (
                        row.reserved_cost_usd
                        if row.reserved_cost_usd is not None
                        else self._limits.max_cost_usd
                    )
                    for row in attempts
                )
                if cost_reserved + reserved_cost > self._limits.max_cost_usd:
                    raise ProviderError("budget_exceeded")

            recorder = DatabaseAttemptRecorder(
                self._session_factory,
                self._claim.run_id,
                self._claim.fence,
            )
            reserved_input = (
                reserved_tokens - requested_output_tokens
                if reserved_tokens is not None
                else None
            )
            attempt_id = recorder.start(
                profile,
                reason=reason,
                reserved_cost_usd=reserved_cost,
                session=db,
                model_turn_ordinal=model_turn_ordinal,
                reserved_input_tokens=reserved_input,
                reserved_output_tokens=requested_output_tokens,
                reserved_total_tokens=reserved_tokens,
            )
            db.commit()
            return BudgetReservation(attempt_id=attempt_id)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def admit_tool(self, logical_call_id: str) -> None:
        """Admit only a durably recorded model call within the shared tool cap."""
        from ..execution.admission import require_live_claim

        db = self._session_factory()
        try:
            run = require_live_claim(db, self._claim, lock=True)
            if (
                run.kind != "chat"
                or run.graph_version != 2
                or self._execution.graph_kind != "chat"
                or self._execution.graph_version != 2
            ):
                raise ProviderError("unsupported_capability")

            entries = read_transcript(db, run.id)
            call_ids = {
                logical_id
                for entry in entries
                if entry.message.role == "assistant"
                for logical_id in entry.logical_call_ids
            }
            if logical_call_id not in call_ids:
                raise ProviderError(
                    "invalid_request",
                    safe_message="The model tool call was not saved in the run transcript.",
                )
            if len(call_ids) > self._execution.loop_limits.max_tool_calls:
                raise ProviderError("budget_exceeded")
        finally:
            db.close()
