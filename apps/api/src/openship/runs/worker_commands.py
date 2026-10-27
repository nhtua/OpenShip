# ruff: noqa: BLE001, S110
# A maintenance command reports what it found; a health probe that cannot
# reach the database answers "not ok" rather than raising.
"""Worker maintenance commands.

Provides CLI-accessible commands for worker operations including
retention, health checks, and reconciliation.
"""

import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..events.retention import dry_run_retention, run_retention
from ..messaging.maintenance import enqueue_retention_task
from ..messaging.models import RetentionTask
from ..runs.queue import reconcile_expired


def run_retention_command(
    dry_run: bool = False,
    event_days: int = 30,
    checkpoint_days: int = 30,
    messaging_days: int | None = None,
    batch_size: int | None = None,
):
    """Run the retention policy.

    Args:
        dry_run: If True, only report what would be deleted.
        event_days: Event retention period in days.
        checkpoint_days: Checkpoint retention period in days.

    Returns:
        Dict with retention metrics.
    """
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        if dry_run:
            result = dry_run_retention(
                db,
                event_days,
                checkpoint_days,
                dry_run=True,
                messaging_retention_days=messaging_days,
                batch_size=batch_size,
            )
        else:
            result = run_retention(
                db,
                event_days,
                checkpoint_days,
                dry_run=False,
                messaging_retention_days=messaging_days,
                batch_size=batch_size,
            )
        return result
    finally:
        db.close()
        engine.dispose()


def enqueue_retention_command(
    requested_by: str,
    *,
    dry_run: bool = False,
    event_days: int | None = None,
    checkpoint_days: int | None = None,
    messaging_days: int | None = None,
):
    """Persist an operator-requested retention task for JetStream delivery."""
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        task = enqueue_retention_task(
            db,
            requested_by=requested_by,
            event_days=(settings.event_retention_days if event_days is None else event_days),
            checkpoint_days=(
                settings.checkpoint_retention_days
                if checkpoint_days is None
                else checkpoint_days
            ),
            messaging_days=messaging_days,
            dry_run=dry_run,
        )
        result = {
            "task_id": str(task.id),
            "state": task.state,
            "requested_by": task.requested_by,
            "event_retention_days": task.event_retention_days,
            "checkpoint_retention_days": task.checkpoint_retention_days,
            "messaging_retention_days": task.messaging_retention_days,
            "batch_size": task.batch_size,
            "dry_run": task.dry_run,
        }
        db.commit()
        return result
    finally:
        db.close()
        engine.dispose()


def retention_status_command(task_id: str):
    """Read an operator-enqueued retention task's durable state and result."""
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError as error:
        raise ValueError("invalid retention task ID") from error
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        task = db.get(RetentionTask, parsed_id)
        if task is None:
            raise ValueError("retention task was not found")
        return {
            "task_id": str(task.id),
            "state": task.state,
            "requested_by": task.requested_by,
            "event_retention_days": task.event_retention_days,
            "checkpoint_retention_days": task.checkpoint_retention_days,
            "messaging_retention_days": task.messaging_retention_days,
            "batch_size": task.batch_size,
            "dry_run": task.dry_run,
            "attempts": task.attempts,
            "requested_at": task.requested_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "consumer_stream_sequence": task.consumer_stream_sequence,
            "consumer_sequence": task.consumer_sequence,
            "ack_pending_at": task.ack_pending_at,
            "consumer_acked_at": task.consumer_acked_at,
            "error_code": task.error_code,
            "result": json.loads(task.result_json) if task.result_json else None,
        }
    finally:
        db.close()
        engine.dispose()


def run_reconcile_command():
    """Reconcile expired leases.

    Returns:
        Number of leases reclaimed.
    """
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        from ..connectors.blockers import expire_connector_blockers

        expire_connector_blockers(db)
        return reconcile_expired(db)
    finally:
        db.close()
        engine.dispose()


def health_check_command():
    """Run health check and report status.

    Returns:
        Dict with health status.
    """
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    db_ok = False
    try:
        db.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    db.close()
    engine.dispose()

    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


def main():
    """CLI entry point for worker maintenance commands."""
    import argparse

    parser = argparse.ArgumentParser(prog="openship-worker-maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    retention = commands.add_parser("retention", help="run retention directly")
    retention.add_argument("--dry-run", action="store_true")
    retention.add_argument("--event-days", type=int, default=settings.event_retention_days)
    retention.add_argument(
        "--checkpoint-days", type=int, default=settings.checkpoint_retention_days
    )
    retention.add_argument(
        "--messaging-days", type=int, default=settings.messaging_retention_days
    )
    enqueue = commands.add_parser(
        "enqueue-retention", help="enqueue an auditable bounded retention task"
    )
    enqueue.add_argument("--requested-by", required=True)
    enqueue.add_argument("--dry-run", action="store_true")
    enqueue.add_argument("--event-days", type=int, default=settings.event_retention_days)
    enqueue.add_argument(
        "--checkpoint-days", type=int, default=settings.checkpoint_retention_days
    )
    enqueue.add_argument(
        "--messaging-days", type=int, default=settings.messaging_retention_days
    )
    status = commands.add_parser(
        "retention-status", help="inspect an enqueued task by UUID"
    )
    status.add_argument("task_id")
    commands.add_parser("reconcile", help="reconcile expired run leases")
    commands.add_parser("health", help="check the database connection")
    args = parser.parse_args()

    if args.command == "retention":
        result = run_retention_command(
            dry_run=args.dry_run,
            event_days=args.event_days,
            checkpoint_days=args.checkpoint_days,
            messaging_days=args.messaging_days,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "enqueue-retention":
        result = enqueue_retention_command(
            args.requested_by,
            dry_run=args.dry_run,
            event_days=args.event_days,
            checkpoint_days=args.checkpoint_days,
            messaging_days=args.messaging_days,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "retention-status":
        result = retention_status_command(args.task_id)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "reconcile":
        reclaimed = run_reconcile_command()
        print(f"Reclaimed {reclaimed} expired leases")

    elif args.command == "health":
        result = health_check_command()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
