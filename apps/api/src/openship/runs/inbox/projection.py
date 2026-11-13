"""Bounded inbox projection shared by snapshots and reloads.

An inbox row is the durable admission record for one user message: who it
belongs to, which run or clarification request owns it, and what became of it.
The snapshot projects the same fields the live events carry, so a reload and a
stream tell one story. Rows are ordered by the conversation's durable
admission sequence, never browser time.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ...events.schemas import InboxMessageSummary
from ..tasks.models import TaskDecisionRecord
from .models import ConversationInboxEntry

# The composer only needs the recent queue. A bound keeps the snapshot small
# without hiding anything the message history itself still shows.
INBOX_SNAPSHOT_LIMIT = 500


def read_inbox_summaries(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    limit: int = INBOX_SNAPSHOT_LIMIT,
) -> list[InboxMessageSummary]:
    """The latest bounded inbox states for one conversation, oldest first."""
    if limit < 1:
        return []
    rows = (
        db.query(ConversationInboxEntry, TaskDecisionRecord.request_id)
        .outerjoin(
            TaskDecisionRecord,
            TaskDecisionRecord.id == ConversationInboxEntry.decision_id,
        )
        .filter(ConversationInboxEntry.conversation_id == conversation_id)
        .order_by(ConversationInboxEntry.sequence.desc())
        .limit(limit)
        .all()
    )
    return [
        InboxMessageSummary(
            message_id=entry.message_id,
            disposition=entry.disposition,
            status=entry.status,
            run_id=entry.run_id,
            request_id=request_id,
            sequence=entry.sequence,
            error_code=entry.failure_code,
        )
        for entry, request_id in reversed(rows)
    ]
