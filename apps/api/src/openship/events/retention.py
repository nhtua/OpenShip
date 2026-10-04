"""Event retention and pruning.

Handles durable workspace data retention policies:
- 30-day event retention for completed runs
- 30-day terminal checkpoint retention
- Messages and run summaries kept until explicit delete policy
- Retention as a worker maintenance command with dry-run metrics
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Event, Run

logger = logging.getLogger(__name__)


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


def dry_run_retention(
    db: Session,
    event_retention_days: int = 30,
    checkpoint_retention_days: int = 30,
    dry_run: bool = True,
) -> dict:
    """Calculate retention metrics without making changes.

    Identifies eligible events, checkpoints, and runs for pruning
    based on age and terminal status.

    Args:
        db: Database session.
        event_retention_days: Retention period for events (default 30).
        checkpoint_retention_days: Retention period for checkpoints (default 30).
        dry_run: If True, only calculate; don't delete.

    Returns:
        Dict with eligible counts for each data type.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=event_retention_days)
    checkpoint_cutoff = datetime.now(timezone.utc) - timedelta(days=checkpoint_retention_days)

    # Eligible events: from completed/failed/cancelled runs older than cutoff
    eligible_events = (
        db.query(Event)
        .join(Run, Event.run_id == Run.id)
        .filter(
            Run.status.in_(["completed", "failed", "cancelled", "timed_out"]),
            Run.ended_at < cutoff,
        )
        .count()
    )

    # Eligible checkpoints: from terminal runs older than checkpoint_cutoff
    # (count runs; actual checkpoint deletion uses checkpointer API)
    eligible_checkpoints = (
        db.query(Run)
        .filter(
            Run.status.in_(["completed", "failed", "cancelled", "timed_out"]),
            Run.ended_at < checkpoint_cutoff,
        )
        .count()
    )

    # Eligible runs for summary deletion (older than cutoff, terminal)
    eligible_runs = (
        db.query(Run)
        .filter(
            Run.status.in_(["completed", "failed", "cancelled", "timed_out"]),
            Run.ended_at < cutoff,
        )
        .count()
    )

    return {
        "dry_run": dry_run,
        "cutoff_date": cutoff.isoformat(),
        "eligible_events": eligible_events,
        "eligible_checkpoints": eligible_checkpoints,
        "eligible_runs": eligible_runs,
    }


def run_retention(
    db: Session,
    event_retention_days: int = 30,
    checkpoint_retention_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Execute retention policy, pruning eligible terminal data.

    Only prunes events from terminal runs (completed/failed/cancelled/timed_out)
    that are older than the retention period. Active, waiting, and running runs
    are never pruned. Checkpoints are deleted via the checkpointer API for
    terminal runs.

    Args:
        db: Database session.
        event_retention_days: Retention period for events (default 30).
        checkpoint_retention_days: Retention period for checkpoints (default 30).
        dry_run: If True, only calculate; don't delete.

    Returns:
        Dict with counts of pruned items.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=event_retention_days)
    checkpoint_cutoff = datetime.now(timezone.utc) - timedelta(days=checkpoint_retention_days)
    logger.info("Running retention policy, event cutoff: %s, checkpoint cutoff: %s", cutoff.isoformat(), checkpoint_cutoff.isoformat())

    metrics = dry_run_retention(db, event_retention_days, checkpoint_retention_days, dry_run)

    if dry_run:
        logger.info("Dry run — no deletions performed")
        return metrics

    # Delete eligible events using a subquery to avoid loading all IDs into memory
    from sqlalchemy import delete, select
    eligible_subquery = (
        select(Run.id)
        .where(
            Run.status.in_(["completed", "failed", "cancelled", "timed_out"]),
            Run.ended_at < cutoff,
        )
    )

    events_pruned = db.execute(
        delete(Event).where(Event.run_id.in_(eligible_subquery))
    ).rowcount
    logger.info("Pruned %d events", events_pruned)

    # Delete eligible checkpoints using checkpointer API
    checkpoint_pruned = 0
    terminal_runs = (
        db.query(Run)
        .filter(
            Run.status.in_(["completed", "failed", "cancelled", "timed_out"]),
            Run.ended_at < checkpoint_cutoff,
        )
        .all()
    )
    logger.info("Deleting checkpoints for %d terminal runs", len(terminal_runs))
    for idx, run in enumerate(terminal_runs, 1):
        if run.graph_thread_id:
            try:
                from ..runs.checkpoints import get_checkpointer
                with get_checkpointer() as saver:
                    # Use checkpointer's whole-thread deletion
                    saver.delete_thread(run.graph_thread_id)
                    checkpoint_pruned += 1
                    if idx % 50 == 0 or idx == len(terminal_runs):
                        logger.info("Deleted %d/%d checkpoints so far", idx, len(terminal_runs))
            except Exception as e:
                logger.warning("Failed to delete checkpoint for run %s: %s", run.id, e)

    logger.info("Pruned %d checkpoints", checkpoint_pruned)

    # Update metrics with actual counts
    metrics["pruned_events"] = events_pruned
    metrics["pruned_checkpoints"] = checkpoint_pruned
    metrics["dry_run"] = False

    db.commit()
    return metrics