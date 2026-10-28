"""Operator verification and crash recovery for local conversation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import (
    _lock_scopes,
    finish_artifact_delete,
    mark_artifact_unavailable,
    release_artifact_reservation,
)
from src.openship.config import settings
from src.openship.events.service import append_event
from src.openship.workspace.maintenance import (
    plan_workspace_purge,
    purge_workspace,
    verify_restored_artifacts,
)


@dataclass(frozen=True)
class ArtifactMaintenanceReport:
    examined: int = 0
    verified: int = 0
    unavailable: int = 0
    reclaimed: int = 0
    deleted: int = 0
    failed: int = 0
    dry_run: bool = False


def configured_store() -> LocalArtifactStore:
    if settings.artifact_storage_id is None:
        raise ArtifactStorageError("artifact storage identity is not configured")
    return LocalArtifactStore(settings.artifact_root, settings.artifact_storage_id)


def _is_older(created_at: datetime, cutoff: datetime) -> bool:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at <= cutoff


def verify_artifacts(db: Session, store: LocalArtifactStore) -> ArtifactMaintenanceReport:
    """Read and digest-check all ready bytes without mutating metadata."""
    store.verify()
    verified = 0
    unavailable = 0
    artifacts = db.query(Artifact).filter(Artifact.status == "ready").all()
    for artifact in artifacts:
        try:
            stream = store.open_verified(artifact)
            stream.close()
            verified += 1
        except ArtifactStorageError:
            unavailable += 1
    return ArtifactMaintenanceReport(
        examined=len(artifacts), verified=verified, unavailable=unavailable
    )


def _append_recovered_delete_event(db: Session, artifact: Artifact) -> None:
    append_event(
        db=db,
        conversation_id=artifact.conversation_id,
        run_id=None,
        event_type="artifact.deleted",
        payload={
            "schema_version": 1,
            "artifact_id": str(artifact.id),
            "sha256": artifact.sha256,
            "status": "deleted",
        },
    )


def reconcile_artifacts(
    db: Session,
    store: LocalArtifactStore,
    *,
    older_than_seconds: int | None = None,
    dry_run: bool = False,
) -> ArtifactMaintenanceReport:
    """Verify ready files and recover expired staging/deleting transitions.

    The caller owns the session. Each repaired row is committed independently so
    one damaged file cannot roll back unrelated recovery work.
    """
    store.verify()
    retention = (
        settings.artifact_staging_retention_seconds
        if older_than_seconds is None
        else older_than_seconds
    )
    if retention < 0:
        raise ValueError("artifact staging retention cannot be negative")
    cutoff = datetime.now(UTC) - timedelta(seconds=retention)
    verified = unavailable = reclaimed = deleted = failed = 0

    ready = db.query(Artifact).filter(Artifact.status == "ready").all()
    for item in ready:
        try:
            stream = store.open_verified(item)
            stream.close()
            verified += 1
        except ArtifactStorageError:
            if dry_run:
                unavailable += 1
                continue
            try:
                current = mark_artifact_unavailable(db, item.id)
                if current.status == "unavailable":
                    db.commit()
                    unavailable += 1
            except (HTTPException, SQLAlchemyError):
                db.rollback()
                failed += 1

    stale_ids = [
        item.id
        for item in db.query(Artifact.id, Artifact.created_at)
        .filter(Artifact.status == "staging")
        .all()
        if _is_older(item.created_at, cutoff)
    ]
    deleting_ids = [
        artifact_id
        for (artifact_id,) in db.query(Artifact.id)
        .filter(Artifact.status == "deleting")
        .all()
    ]

    for artifact_id in stale_ids:
        if dry_run:
            reclaimed += 1
            continue
        try:
            snapshot = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if snapshot is None:
                continue
            _lock_scopes(db, snapshot.conversation_id, snapshot.project_id)
            artifact = (
                db.query(Artifact)
                .filter(Artifact.id == artifact_id)
                .with_for_update()
                .first()
            )
            if artifact is None or artifact.status != "staging" or not _is_older(artifact.created_at, cutoff):
                db.rollback()
                continue
            store.remove_staging(artifact.id, artifact.project_id, artifact.conversation_id)
            expected_hash = artifact.sha256 if artifact.sha256 != "0" * 64 else None
            store.remove_unpublished(
                artifact.relative_storage_key,
                expected_size=artifact.size_bytes if artifact.size_bytes else None,
                expected_sha256=expected_hash,
            )
            release_artifact_reservation(db, artifact.id)
            db.commit()
            reclaimed += 1
        except (ArtifactStorageError, HTTPException, SQLAlchemyError, ValueError):
            db.rollback()
            failed += 1

    for artifact_id in deleting_ids:
        if dry_run:
            deleted += 1
            continue
        try:
            snapshot = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if snapshot is None:
                continue
            _lock_scopes(db, snapshot.conversation_id, snapshot.project_id)
            artifact = (
                db.query(Artifact)
                .filter(Artifact.id == artifact_id)
                .with_for_update()
                .first()
            )
            if artifact is None or artifact.status != "deleting":
                db.rollback()
                continue
            store.remove(artifact)
            deleted_artifact = finish_artifact_delete(db, artifact.id)
            _append_recovered_delete_event(db, deleted_artifact)
            db.commit()
            deleted += 1
        except (ArtifactStorageError, HTTPException, SQLAlchemyError, ValueError):
            db.rollback()
            failed += 1

    return ArtifactMaintenanceReport(
        examined=len(ready) + len(stale_ids) + len(deleting_ids),
        verified=verified,
        unavailable=unavailable,
        reclaimed=reclaimed,
        deleted=deleted,
        failed=failed,
        dry_run=dry_run,
    )


def _run_database_command(action, store: LocalArtifactStore):
    engine = create_engine(settings.database_url)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        report = action(db, store)
        return asdict(report)
    finally:
        db.close()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local OpenShip conversation artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="initialize the configured artifact root explicitly")
    subparsers.add_parser("verify", help="verify all ready artifact bytes")
    retention = subparsers.add_parser("retention", help="recover stale artifact transfers and deletions")
    retention.add_argument("--dry-run", action="store_true")
    retention.add_argument("--older-than-seconds", type=int)
    purge = subparsers.add_parser("purge", help="explicitly purge one conversation workspace")
    purge.add_argument("workspace_id")
    purge.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("verify-restored", help="reconcile artifact metadata after a coordinated restore")
    args = parser.parse_args(argv)

    try:
        store = configured_store()
        if args.command == "init":
            store.initialize()
            result = {"initialized": True, "storage_id": str(store.storage_id)}
        elif args.command == "verify":
            result = _run_database_command(verify_artifacts, store)
        elif args.command == "verify-restored":
            result = _run_database_command(verify_restored_artifacts, store)
        elif args.command == "purge":
            def purge_action(db, configured):
                plan = plan_workspace_purge(db, args.workspace_id)
                report = purge_workspace(db, configured, plan, dry_run=args.dry_run)
                return {"plan": asdict(plan), "report": asdict(report)}

            result = _run_database_command(purge_action, store)
        else:
            result = _run_database_command(
                lambda db, configured: reconcile_artifacts(
                    db,
                    configured,
                    older_than_seconds=args.older_than_seconds,
                    dry_run=args.dry_run,
                ),
                store,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        report = result.get("report", result)
        return 0 if (
            not report.get("failed", 0)
            and not report.get("unavailable", 0)
            and not report.get("workspaces_unavailable", 0)
        ) else 2
    except HTTPException as exc:
        error = (exc.detail or {}).get("error", {}) if isinstance(exc.detail, dict) else {}
        print(
            f"artifact maintenance refused: {error.get('code', 'operation_failed')}",
            file=sys.stderr,
        )
        return 2
    except (ArtifactStorageError, ValueError) as exc:
        print(f"artifact maintenance failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - hide unexpected DB/config details from CLI output.
        print(
            f"artifact maintenance failed due to {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
