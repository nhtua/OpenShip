# ruff: noqa: BLE001
# Pruning is best-effort per run: one checkpoint this installation cannot
# delete must not stop the rest, and the reason is logged rather than raised.
"""Event retention and pruning.

Handles durable workspace data retention policies:
- 30-day event retention for completed runs
- 30-day terminal checkpoint retention
- Messages and run summaries kept until explicit delete policy
- Retention as a worker maintenance command with dry-run metrics
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..config import settings
from ..messaging.models import (
    MessageDisposition,
    MessageExhaustion,
    MessageFailure,
    MessageFailureConflict,
    RetentionTask,
)
from ..runs.models import Event, Outbox, Run

logger = logging.getLogger(__name__)
_TERMINAL_RUN_STATES = ("completed", "failed", "cancelled", "timed_out")


def _event_has_unpublished_outbox(event_id):
    return (
        select(Outbox.id)
        .where(Outbox.event_id == event_id, Outbox.processed_at.is_(None))
        .exists()
    )


def _message_disposition_retention_filter(cutoff):
    outbox_exists = select(Outbox.id).where(
        Outbox.message_id == MessageDisposition.message_id
    ).exists()
    terminal_run_exists = (
        select(Outbox.id)
        .join(Run, Run.id == Outbox.run_id)
        .where(
            Outbox.message_id == MessageDisposition.message_id,
            Outbox.destination.in_(("command", "control")),
            Outbox.processed_at < cutoff,
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < cutoff,
            MessageDisposition.disposition.in_(
                ("completed", "disposed", "quarantined")
            ),
        )
        .exists()
    )
    acknowledged_maintenance_exists = (
        select(Outbox.id)
        .join(RetentionTask, RetentionTask.id == Outbox.maintenance_task_id)
        .where(
            Outbox.message_id == MessageDisposition.message_id,
            Outbox.destination == "maintenance",
            RetentionTask.state.in_(("completed", "failed")),
            MessageDisposition.disposition == "completed",
            RetentionTask.consumer_acked_at < cutoff,
        )
        .exists()
    )
    settled_event_notice_exists = (
        select(Outbox.id)
        .where(
            Outbox.message_id == MessageDisposition.message_id,
            Outbox.destination == "event",
            Outbox.processed_at < cutoff,
            MessageDisposition.disposition.in_(
                ("completed", "disposed", "quarantined")
            ),
        )
        .exists()
    )
    return and_(
        MessageDisposition.observed_at < cutoff,
        or_(
            ~outbox_exists,
            terminal_run_exists,
            acknowledged_maintenance_exists,
            settled_event_notice_exists,
        ),
    )


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
    *,
    messaging_retention_days: int | None = None,
    batch_size: int | None = None,
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
    cutoff = datetime.now(UTC) - timedelta(days=event_retention_days)
    checkpoint_cutoff = datetime.now(UTC) - timedelta(days=checkpoint_retention_days)
    messaging_days = (
        settings.messaging_retention_days
        if messaging_retention_days is None
        else messaging_retention_days
    )
    if any(
        type(value) is not int or not 1 <= value <= 36_500
        for value in (event_retention_days, checkpoint_retention_days, messaging_days)
    ):
        raise ValueError("retention days must be between 1 and 36500")
    if batch_size is not None and (
        type(batch_size) is not int or not 1 <= batch_size <= 10_000
    ):
        raise ValueError("batch size must be between 1 and 10000")
    messaging_cutoff = datetime.now(UTC) - timedelta(days=messaging_days)

    # Eligible events: from completed/failed/cancelled runs older than cutoff
    eligible_events = (
        db.query(Event)
        .join(Run, Event.run_id == Run.id)
        .filter(
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < cutoff,
            ~_event_has_unpublished_outbox(Event.id),
        )
        .count()
    )

    # Eligible checkpoints: from terminal runs older than checkpoint_cutoff
    # (count runs; actual checkpoint deletion uses checkpointer API)
    eligible_checkpoints = (
        db.query(Run)
        .filter(
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < checkpoint_cutoff,
        )
        .count()
    )

    # Eligible runs for summary deletion (older than cutoff, terminal)
    eligible_runs = (
        db.query(Run)
        .filter(
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < cutoff,
        )
        .count()
    )

    eligible_message_dispositions = (
        db.query(MessageDisposition)
        .filter(_message_disposition_retention_filter(messaging_cutoff))
        .count()
    )
    eligible_message_exhaustions = (
        db.query(MessageExhaustion)
        .filter(
            MessageExhaustion.state.in_(("resolved", "source_missing")),
            func.coalesce(
                MessageExhaustion.resolved_at, MessageExhaustion.updated_at
            )
            < messaging_cutoff,
        )
        .count()
    )
    eligible_message_failure_conflicts = (
        db.query(MessageFailureConflict)
        .filter(MessageFailureConflict.created_at < messaging_cutoff)
        .count()
    )
    eligible_message_failures = (
        db.query(MessageFailure)
        .join(Outbox, Outbox.failure_id == MessageFailure.id)
        .filter(
            Outbox.destination == "failure",
            Outbox.processed_at < messaging_cutoff,
            MessageFailure.created_at < messaging_cutoff,
        )
        .count()
    )
    eligible_retention_tasks = (
        db.query(RetentionTask)
        .join(Outbox, Outbox.maintenance_task_id == RetentionTask.id)
        .filter(
            RetentionTask.state.in_(("completed", "failed")),
            RetentionTask.completed_at < messaging_cutoff,
            RetentionTask.consumer_acked_at < messaging_cutoff,
            Outbox.destination == "maintenance",
            Outbox.processed_at < messaging_cutoff,
        )
        .count()
    )
    eligible_run_dispatch_outbox = (
        db.query(Outbox)
        .join(Run, Run.id == Outbox.run_id)
        .filter(
            Outbox.destination.in_(("command", "control")),
            Outbox.processed_at < messaging_cutoff,
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < messaging_cutoff,
        )
        .count()
    )
    eligible_event_outbox = (
        db.query(Outbox)
        .filter(
            Outbox.destination == "event",
            Outbox.processed_at < messaging_cutoff,
        )
        .count()
    )

    bounded_counts = (
        eligible_events,
        eligible_checkpoints,
        eligible_message_dispositions,
        eligible_message_exhaustions,
        eligible_message_failure_conflicts,
        eligible_message_failures,
        eligible_retention_tasks,
        eligible_run_dispatch_outbox,
        eligible_event_outbox,
    )
    return {
        "dry_run": dry_run,
        "cutoff_date": cutoff.isoformat(),
        "messaging_cutoff_date": messaging_cutoff.isoformat(),
        "eligible_events": eligible_events,
        "eligible_checkpoints": eligible_checkpoints,
        "eligible_runs": eligible_runs,
        "eligible_message_dispositions": eligible_message_dispositions,
        "eligible_message_exhaustions": eligible_message_exhaustions,
        "eligible_message_failures": eligible_message_failures,
        "eligible_message_failure_conflicts": eligible_message_failure_conflicts,
        "eligible_retention_tasks": eligible_retention_tasks,
        "eligible_run_dispatch_outbox": eligible_run_dispatch_outbox,
        "eligible_event_outbox": eligible_event_outbox,
        "batch_size": batch_size,
        "limit_reached": batch_size is not None
        and any(count > batch_size for count in bounded_counts),
    }


def run_retention(
    db: Session,
    event_retention_days: int = 30,
    checkpoint_retention_days: int = 30,
    dry_run: bool = False,
    *,
    messaging_retention_days: int | None = None,
    batch_size: int | None = None,
    commit: bool = True,
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
    if any(
        type(value) is not int or not 1 <= value <= 36_500
        for value in (event_retention_days, checkpoint_retention_days)
    ):
        raise ValueError("retention days must be between 1 and 36500")
    messaging_days = (
        settings.messaging_retention_days
        if messaging_retention_days is None
        else messaging_retention_days
    )
    if type(messaging_days) is not int or not 1 <= messaging_days <= 36_500:
        raise ValueError("messaging retention days must be between 1 and 36500")
    if batch_size is not None and (
        type(batch_size) is not int or not 1 <= batch_size <= 10_000
    ):
        raise ValueError("batch size must be between 1 and 10000")

    cutoff = datetime.now(UTC) - timedelta(days=event_retention_days)
    checkpoint_cutoff = datetime.now(UTC) - timedelta(days=checkpoint_retention_days)
    messaging_cutoff = datetime.now(UTC) - timedelta(days=messaging_days)
    logger.info("Running retention policy, event cutoff: %s, checkpoint cutoff: %s", cutoff.isoformat(), checkpoint_cutoff.isoformat())

    metrics = dry_run_retention(
        db,
        event_retention_days,
        checkpoint_retention_days,
        dry_run,
        messaging_retention_days=messaging_days,
        batch_size=batch_size,
    )

    if dry_run:
        logger.info("Dry run — no deletions performed")
        return metrics

    # Delete eligible events using a subquery to avoid loading all IDs into memory
    from sqlalchemy import delete

    event_query = (
        select(Event.id)
        .join(Run, Event.run_id == Run.id)
        .where(
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < cutoff,
            ~_event_has_unpublished_outbox(Event.id),
        )
        .order_by(Event.created_at.asc(), Event.id.asc())
    )
    if batch_size is not None:
        event_query = event_query.limit(batch_size)
    events_pruned = db.execute(
        delete(Event).where(Event.id.in_(event_query))
    ).rowcount
    logger.info("Pruned %d events", events_pruned)

    # Delete eligible checkpoints using checkpointer API
    checkpoint_pruned = 0
    terminal_runs_query = (
        db.query(Run)
        .filter(
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < checkpoint_cutoff,
        )
        .order_by(Run.ended_at.asc(), Run.id.asc())
    )
    if batch_size is not None:
        terminal_runs_query = terminal_runs_query.limit(batch_size)
    terminal_runs = terminal_runs_query.all()
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

    def delete_limited(id_column, model, *criteria, order_by):
        selected_ids = select(id_column).where(*criteria).order_by(*order_by)
        if batch_size is not None:
            selected_ids = selected_ids.limit(batch_size)
        result = db.execute(delete(model).where(id_column.in_(selected_ids)))
        return result.rowcount or 0

    dispositions_pruned = delete_limited(
        MessageDisposition.message_id,
        MessageDisposition,
        _message_disposition_retention_filter(messaging_cutoff),
        order_by=(MessageDisposition.observed_at, MessageDisposition.message_id),
    )
    exhaustions_pruned = delete_limited(
        MessageExhaustion.id,
        MessageExhaustion,
        MessageExhaustion.state.in_(("resolved", "source_missing")),
        func.coalesce(
            MessageExhaustion.resolved_at, MessageExhaustion.updated_at
        )
        < messaging_cutoff,
        order_by=(MessageExhaustion.updated_at, MessageExhaustion.id),
    )
    conflicts_pruned = delete_limited(
        MessageFailureConflict.id,
        MessageFailureConflict,
        MessageFailureConflict.created_at < messaging_cutoff,
        order_by=(MessageFailureConflict.created_at, MessageFailureConflict.id),
    )

    failure_ids_query = (
        select(MessageFailure.id)
        .join(Outbox, Outbox.failure_id == MessageFailure.id)
        .where(
            Outbox.destination == "failure",
            Outbox.processed_at < messaging_cutoff,
            MessageFailure.created_at < messaging_cutoff,
        )
        .order_by(MessageFailure.created_at, MessageFailure.id)
    )
    if batch_size is not None:
        failure_ids_query = failure_ids_query.limit(batch_size)
    failure_ids = list(db.execute(failure_ids_query).scalars())
    failure_notices_pruned = 0
    failures_pruned = 0
    if failure_ids:
        failure_notices_pruned = (
            db.execute(
                delete(Outbox).where(
                    Outbox.destination == "failure",
                    Outbox.failure_id.in_(failure_ids),
                )
            ).rowcount
            or 0
        )
        failures_pruned = (
            db.execute(
                delete(MessageFailure).where(MessageFailure.id.in_(failure_ids))
            ).rowcount
            or 0
        )

    dispatch_ids_query = (
        select(Outbox.id)
        .join(Run, Run.id == Outbox.run_id)
        .where(
            Outbox.destination.in_(("command", "control")),
            Outbox.processed_at < messaging_cutoff,
            Run.status.in_(_TERMINAL_RUN_STATES),
            Run.ended_at < messaging_cutoff,
        )
        .order_by(Outbox.processed_at, Outbox.id)
    )
    if batch_size is not None:
        dispatch_ids_query = dispatch_ids_query.limit(batch_size)
    dispatch_ids = db.execute(dispatch_ids_query).scalars().all()
    dispatch_outbox_pruned = 0
    if dispatch_ids:
        dispatch_outbox_pruned = (
            db.execute(delete(Outbox).where(Outbox.id.in_(dispatch_ids))).rowcount
            or 0
        )

    event_outbox_ids_query = (
        select(Outbox.id)
        .where(
            Outbox.destination == "event",
            Outbox.processed_at < messaging_cutoff,
        )
        .order_by(Outbox.processed_at, Outbox.id)
    )
    if batch_size is not None:
        event_outbox_ids_query = event_outbox_ids_query.limit(batch_size)
    event_outbox_ids = db.execute(event_outbox_ids_query).scalars().all()
    event_outbox_pruned = 0
    if event_outbox_ids:
        event_outbox_pruned = (
            db.execute(delete(Outbox).where(Outbox.id.in_(event_outbox_ids))).rowcount
            or 0
        )

    task_ids_query = (
        select(RetentionTask.id)
        .join(Outbox, Outbox.maintenance_task_id == RetentionTask.id)
        .where(
            RetentionTask.state.in_(("completed", "failed")),
            RetentionTask.completed_at < messaging_cutoff,
            RetentionTask.consumer_acked_at < messaging_cutoff,
            Outbox.destination == "maintenance",
            Outbox.processed_at < messaging_cutoff,
        )
        .order_by(RetentionTask.completed_at, RetentionTask.id)
    )
    if batch_size is not None:
        task_ids_query = task_ids_query.limit(batch_size)
    task_ids = db.execute(task_ids_query).scalars().all()
    task_outbox_pruned = 0
    tasks_pruned = 0
    if task_ids:
        task_outbox_pruned = (
            db.execute(
                delete(Outbox).where(
                    Outbox.destination == "maintenance",
                    Outbox.maintenance_task_id.in_(task_ids),
                )
            ).rowcount
            or 0
        )
        tasks_pruned = (
            db.execute(
                delete(RetentionTask).where(RetentionTask.id.in_(task_ids))
            ).rowcount
            or 0
        )

    # Update metrics with actual counts
    metrics["pruned_events"] = events_pruned
    metrics["pruned_checkpoints"] = checkpoint_pruned
    metrics["pruned_message_dispositions"] = dispositions_pruned
    metrics["pruned_message_exhaustions"] = exhaustions_pruned
    metrics["pruned_message_failures"] = failures_pruned
    metrics["pruned_message_failure_notices"] = failure_notices_pruned
    metrics["pruned_message_failure_conflicts"] = conflicts_pruned
    metrics["pruned_retention_tasks"] = tasks_pruned
    metrics["pruned_retention_task_outbox"] = task_outbox_pruned
    metrics["pruned_run_dispatch_outbox"] = dispatch_outbox_pruned
    metrics["pruned_event_outbox"] = event_outbox_pruned
    metrics["dry_run"] = False

    if commit:
        db.commit()
    if batch_size is not None:
        remaining = dry_run_retention(
            db,
            event_retention_days=event_retention_days,
            checkpoint_retention_days=checkpoint_retention_days,
            messaging_retention_days=messaging_days,
            dry_run=False,
            batch_size=batch_size,
        )
        metrics["limit_reached"] = remaining["limit_reached"]
    return metrics
