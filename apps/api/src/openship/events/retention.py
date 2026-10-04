"""Event retention and pruning."""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Event


def prune_events(db: Session, conversation_id: str | uuid.UUID, keep_count: int = 100) -> int:
    """Prune oldest events for a conversation, keeping the most recent N.

    Args:
        db: Database session.
        conversation_id: Target conversation ID.
        keep_count: Number of most recent events to keep.

    Returns:
        Number of events pruned.
    """
    if isinstance(conversation_id, str):
        conversation_id = uuid.UUID(conversation_id)

    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        return 0

    # Find the cutoff sequence (oldest to keep)
    total = conv.next_event_sequence
    prune_count = total - keep_count
    if prune_count <= 0:
        return 0

    # Delete events before the cutoff
    result = (
        db.query(Event)
        .filter(
            Event.conversation_id == conversation_id,
            Event.sequence < prune_count,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return result
