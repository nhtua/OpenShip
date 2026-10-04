"""Worker maintenance commands.

Provides CLI-accessible commands for worker operations including
retention, health checks, and reconciliation.
"""

import json
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..events.retention import dry_run_retention, run_retention
from ..runs.queue import reconcile_expired


def run_retention_command(dry_run: bool = False, event_days: int = 30, checkpoint_days: int = 30):
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
            result = dry_run_retention(db, event_days, checkpoint_days, dry_run=True)
        else:
            result = run_retention(db, event_days, checkpoint_days, dry_run=False)
        return result
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
    except Exception as e:
        pass

    db.close()
    engine.dispose()

    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


def main():
    """CLI entry point for worker maintenance commands."""
    if len(sys.argv) < 2:
        print("Usage: worker_commands.py <command> [args]")
        print("Commands: retention, reconcile, health")
        sys.exit(1)

    command = sys.argv[1]

    if command == "retention":
        dry_run = "--dry-run" in sys.argv
        result = run_retention_command(dry_run=dry_run)
        print(json.dumps(result, indent=2, default=str))

    elif command == "reconcile":
        reclaimed = run_reconcile_command()
        print(f"Reclaimed {reclaimed} expired leases")

    elif command == "health":
        result = health_check_command()
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()