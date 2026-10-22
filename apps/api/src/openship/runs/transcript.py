"""Fenced persistence for complete, provider-neutral model/tool messages."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..providers.contracts import ChatMessage
from ..providers.errors import ProviderError
from .models import RunTranscriptRecord
from .queue import Claim


class RunTranscriptEntry(BaseModel):
    """A complete ordered transcript message and its internal operation IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=0)
    model_turn_ordinal: int = Field(ge=0)
    message: ChatMessage
    logical_call_ids: tuple[str, ...] = ()
    logical_call_id: str | None = None
    created_at: datetime


def _decode(row: RunTranscriptRecord) -> RunTranscriptEntry:
    try:
        return RunTranscriptEntry(
            ordinal=row.ordinal,
            model_turn_ordinal=row.model_turn_ordinal,
            message=ChatMessage.model_validate_json(row.message_json),
            logical_call_ids=tuple(json.loads(row.logical_call_ids_json or "[]")),
            logical_call_id=row.logical_call_id,
            created_at=row.created_at,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as invalid:
        raise ProviderError(
            "invalid_response",
            safe_message="The saved model transcript is malformed and cannot be continued safely.",
            cause=invalid,
        ) from invalid


def read_transcript(db: Session, run_id: UUID) -> tuple[RunTranscriptEntry, ...]:
    """Read ordered complete messages without consulting old event history."""
    rows = (
        db.query(RunTranscriptRecord)
        .filter(RunTranscriptRecord.run_id == run_id)
        .order_by(RunTranscriptRecord.ordinal)
        .all()
    )
    return tuple(_decode(row) for row in rows)


def _call_map(entries: tuple[RunTranscriptEntry, ...]):
    calls: dict[tuple[int, str], tuple[str, str, bool]] = {}
    for entry in entries:
        message = entry.message
        if message.role == "assistant" and message.tool_calls:
            for index, call in enumerate(message.tool_calls):
                calls[(entry.model_turn_ordinal, call.provider_call_id)] = (
                    f"model:{entry.model_turn_ordinal}:call:{index}",
                    call.name,
                    False,
                )
        elif message.role == "tool" and message.tool_call_id:
            key = (entry.model_turn_ordinal, message.tool_call_id)
            if key in calls:
                logical_id, name, _ = calls[key]
                calls[key] = (logical_id, name, True)
    return calls


def append_transcript(
    db: Session,
    claim: Claim,
    model_turn_ordinal: int,
    message: ChatMessage,
) -> RunTranscriptEntry:
    """Append one complete normalized message under the current run fence.

    The caller owns the transaction and commit. Locking the run/job validates
    the worker fence and serializes transcript ordinal allocation.
    """
    if model_turn_ordinal < 0:
        raise ProviderError("invalid_request", safe_message="The model turn number is invalid.")
    if not isinstance(message, ChatMessage):
        raise ProviderError("invalid_request", safe_message="Only complete normalized messages can be saved.")

    from ..execution.admission import require_live_claim

    run = require_live_claim(db, claim, lock=True)
    if run.kind != "chat" or run.graph_version not in (2, 3):
        raise ProviderError(
            "unsupported_capability",
            safe_message="This run does not support a tool conversation transcript.",
        )

    rows = (
        db.query(RunTranscriptRecord)
        .filter(RunTranscriptRecord.run_id == run.id)
        .order_by(RunTranscriptRecord.ordinal)
        .all()
    )
    entries = tuple(_decode(row) for row in rows)
    calls = _call_map(entries)

    logical_ids: tuple[str, ...] = ()
    logical_call_id = None
    if message.role == "assistant" and any(
        entry.model_turn_ordinal == model_turn_ordinal and entry.message.role == "assistant"
        for entry in entries
    ):
        raise ProviderError(
            "invalid_request",
            safe_message="This model turn already has an assistant response.",
        )
    if message.role == "assistant" and message.tool_calls:
        if any(not resolved for _, _, resolved in calls.values()):
            raise ProviderError("invalid_request", safe_message="The previous tool call has no saved result.")
        try:
            execution = json.loads(run.execution_configuration or "{}")
            allowed = {item["id"] for item in execution.get("allowed_capabilities", [])}
        except (json.JSONDecodeError, TypeError, KeyError):
            allowed = set()
        names = tuple(call.name for call in message.tool_calls)
        if any(name not in allowed for name in names):
            raise ProviderError(
                "unsupported_capability",
                safe_message="The model requested a tool that this run did not authorize.",
            )
        logical_ids = tuple(
            f"model:{model_turn_ordinal}:call:{index}"
            for index, _ in enumerate(message.tool_calls)
        )
    elif message.role == "tool":
        key = (model_turn_ordinal, message.tool_call_id or "")
        previous = calls.get(key)
        if previous is None or previous[1] != message.name:
            raise ProviderError("invalid_request", safe_message="The tool result has no matching saved tool call.")
        if previous[2]:
            raise ProviderError("invalid_request", safe_message="This tool call already has a result.")
        logical_call_id = previous[0]

    ordinal = rows[-1].ordinal + 1 if rows else 0
    row = RunTranscriptRecord(
        run_id=run.id,
        ordinal=ordinal,
        model_turn_ordinal=model_turn_ordinal,
        message_json=message.model_dump_json(exclude_none=True),
        logical_call_ids_json=json.dumps(logical_ids, separators=(",", ":")),
        logical_call_id=logical_call_id,
    )
    db.add(row)
    db.flush()
    return _decode(row)
