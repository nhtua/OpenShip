"""Explicit workspace purge and storage restore verification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import (
    _lock_scopes,
    begin_artifact_delete,
    finish_artifact_delete,
    require_artifact_not_pinned,
)
from src.openship.chat.models import Conversation
from src.openship.events.service import append_event

from .models import ConversationWorkspace


@dataclass(frozen=True, slots=True)
class PurgeItem:
    artifact_id: str
    expected_status: str
    sha256: str
    size_bytes: int
    relative_storage_key: str


@dataclass(frozen=True, slots=True)
class PurgePlan:
    workspace_id: str
    conversation_id: str
    project_id: str
    storage_id: str
    items: tuple[PurgeItem, ...]


@dataclass(frozen=True, slots=True)
class PurgeReport:
    workspace_id: str
    examined: int
    deleted: int
    skipped: int
    failed: int
    dry_run: bool
    items: tuple[PurgeItem, ...]


@dataclass(frozen=True, slots=True)
class RestoreReport:
    storage_id: str
    storage_available: bool
    examined: int
    verified: int
    recovered: int
    unavailable: int
    workspaces_available: int
    workspaces_unavailable: int
    failed: int


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "workspace_not_found", "message": "Workspace not found"}},
    )


def _purge_conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"error": {"code": code, "message": message}})


def _item(artifact: Artifact) -> PurgeItem:
    return PurgeItem(
        artifact_id=str(artifact.id),
        expected_status=artifact.status,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        relative_storage_key=artifact.relative_storage_key,
    )


def _canonical_artifact_key(project_id: uuid.UUID, conversation_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return (
        f"projects/{project_id}/conversations/{conversation_id}/"
        f"artifacts/{artifact_id}/content"
    )


def plan_workspace_purge(db: Session, workspace_id: uuid.UUID | str) -> PurgePlan:
    """Create an exact, read-only purge snapshot and refuse active input pins."""
    try:
        parsed_workspace_id = uuid.UUID(str(workspace_id))
    except (TypeError, ValueError) as exc:
        raise _not_found() from exc
    workspace = (
        db.query(ConversationWorkspace)
        .filter(ConversationWorkspace.id == parsed_workspace_id)
        .with_for_update()
        .first()
    )
    if workspace is None:
        raise _not_found()
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == workspace.conversation_id,
            Conversation.project_id == workspace.project_id,
        )
        .with_for_update()
        .first()
    )
    if conversation is None:
        raise _not_found()

    rows = (
        db.query(Artifact)
        .filter(
            Artifact.conversation_id == workspace.conversation_id,
            Artifact.project_id == workspace.project_id,
            Artifact.status.in_(("ready", "unavailable", "deleting")),
        )
        .order_by(Artifact.created_at, Artifact.id)
        .all()
    )
    publishing = (
        db.query(Artifact.id)
        .filter(
            Artifact.conversation_id == workspace.conversation_id,
            Artifact.project_id == workspace.project_id,
            Artifact.status == "staging",
        )
        .first()
    )
    if publishing is not None:
        raise _purge_conflict(
            "workspace_artifact_publishing",
            "Workspace cannot be purged while an artifact is being published",
        )
    for artifact in rows:
        if artifact.relative_storage_key != _canonical_artifact_key(
            workspace.project_id, workspace.conversation_id, artifact.id
        ):
            raise _purge_conflict(
                "workspace_artifact_scope_invalid",
                "Workspace contains an artifact with an invalid storage association",
            )
        require_artifact_not_pinned(db, artifact)

    return PurgePlan(
        workspace_id=str(workspace.id),
        conversation_id=str(workspace.conversation_id),
        project_id=str(workspace.project_id),
        storage_id=str(workspace.storage_id),
        items=tuple(_item(row) for row in rows),
    )


def _append_deleted_event(db: Session, artifact: Artifact) -> None:
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


def purge_workspace(
    db: Session,
    store: LocalArtifactStore,
    plan: PurgePlan,
    *,
    dry_run: bool = False,
) -> PurgeReport:
    """Apply the reviewed plan with the artifact tombstone/unlink protocol."""
    workspace_id = uuid.UUID(plan.workspace_id)
    workspace = (
        db.query(ConversationWorkspace)
        .filter(ConversationWorkspace.id == workspace_id)
        .with_for_update()
        .first()
    )
    if workspace is None:
        raise _not_found()
    if (
        str(workspace.conversation_id) != plan.conversation_id
        or str(workspace.project_id) != plan.project_id
        or str(workspace.storage_id) != plan.storage_id
        or store.storage_id != workspace.storage_id
    ):
        raise ArtifactStorageError("purge plan does not match the bound workspace storage")
    store.verify()

    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == uuid.UUID(plan.conversation_id),
            Conversation.project_id == uuid.UUID(plan.project_id),
        )
        .with_for_update()
        .first()
    )
    if conversation is None:
        raise _not_found()

    # Revalidate the entire reviewed set under the conversation lock before any
    # state changes. Mark every artifact deleting in one transaction so new run
    # admissions cannot pin a subset after purge has begun.
    artifacts: list[Artifact] = []
    skipped = failed = 0
    for expected in plan.items:
        artifact_id = uuid.UUID(expected.artifact_id)
        artifact = (
            db.query(Artifact)
            .filter(Artifact.id == artifact_id)
            .with_for_update()
            .first()
        )
        if artifact is None or artifact.status == "deleted":
            skipped += 1
            continue
        if (
            str(artifact.conversation_id) != plan.conversation_id
            or str(artifact.project_id) != plan.project_id
            or artifact.status != expected.expected_status
            or artifact.sha256 != expected.sha256
            or artifact.size_bytes != expected.size_bytes
            or artifact.relative_storage_key != expected.relative_storage_key
            or artifact.relative_storage_key
            != _canonical_artifact_key(artifact.project_id, artifact.conversation_id, artifact.id)
        ):
            failed += 1
            continue
        try:
            require_artifact_not_pinned(db, artifact)
            artifacts.append(artifact)
        except HTTPException:
            failed += 1

    if failed:
        db.rollback()
        return PurgeReport(
            workspace_id=plan.workspace_id,
            examined=len(plan.items),
            deleted=0,
            skipped=skipped,
            failed=failed,
            dry_run=dry_run,
            items=plan.items,
        )

    if dry_run:
        db.rollback()
        return PurgeReport(
            workspace_id=plan.workspace_id,
            examined=len(plan.items),
            deleted=0,
            skipped=skipped,
            failed=0,
            dry_run=True,
            items=plan.items,
        )

    try:
        for artifact in artifacts:
            begin_artifact_delete(db, conversation.user_id, artifact.id)
        workspace.status = "deleted"
        # This is the durable purge intent. The existing reconciler can finish
        # each committed tombstone after an unlink or process interruption.
        db.commit()
    except (HTTPException, SQLAlchemyError, ValueError):
        db.rollback()
        return PurgeReport(
            workspace_id=plan.workspace_id,
            examined=len(plan.items),
            deleted=0,
            skipped=skipped,
            failed=1,
            dry_run=False,
            items=plan.items,
        )

    deleted = 0
    for expected in plan.items:
        try:
            artifact_id = uuid.UUID(expected.artifact_id)
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if artifact is None or artifact.status == "deleted":
                skipped += 1
                continue
            if artifact.status != "deleting":
                failed += 1
                continue
            store.remove(artifact)
            deleted_artifact = finish_artifact_delete(db, artifact_id)
            _append_deleted_event(db, deleted_artifact)
            db.commit()
            deleted += 1
        except (HTTPException, ArtifactStorageError, SQLAlchemyError, ValueError):
            db.rollback()
            failed += 1

    return PurgeReport(
        workspace_id=plan.workspace_id,
        examined=len(plan.items),
        deleted=deleted,
        skipped=skipped,
        failed=failed,
        dry_run=dry_run,
        items=plan.items,
    )


def verify_restored_artifacts(
    db: Session,
    store: LocalArtifactStore,
) -> RestoreReport:
    """Reconcile metadata against a restored volume without creating or rebinding it."""
    try:
        store.verify()
        storage_available = True
    except ArtifactStorageError:
        storage_available = False

    workspace_rows = (
        db.query(ConversationWorkspace)
        .filter(ConversationWorkspace.status != "deleted")
        .order_by(ConversationWorkspace.id)
        .all()
    )
    workspace_storage_matches: dict[uuid.UUID, bool] = {}
    workspaces_available = workspaces_unavailable = failed = 0
    for snapshot in workspace_rows:
        try:
            workspace = (
                db.query(ConversationWorkspace)
                .filter(ConversationWorkspace.id == snapshot.id)
                .with_for_update()
                .first()
            )
            conversation = db.query(Conversation).filter(
                Conversation.id == snapshot.conversation_id
            ).first()
            expected_root = (
                f"projects/{snapshot.project_id}/conversations/{snapshot.conversation_id}"
            )
            bound = (
                storage_available
                and snapshot.storage_id == store.storage_id
                and snapshot.relative_root == expected_root
                and conversation is not None
                and conversation.project_id == snapshot.project_id
            )
            workspace_storage_matches[snapshot.conversation_id] = bound
            if workspace is not None:
                workspace.status = "available" if bound else "unavailable"
            db.commit()
            if bound:
                workspaces_available += 1
            else:
                workspaces_unavailable += 1
        except SQLAlchemyError:
            db.rollback()
            failed += 1
            workspace_storage_matches[snapshot.conversation_id] = False

    candidates = (
        db.query(Artifact)
        .filter(Artifact.status.in_(("ready", "unavailable")))
        .order_by(Artifact.created_at, Artifact.id)
        .all()
    )
    verified = recovered = unavailable = examined = 0
    for snapshot in candidates:
        try:
            _lock_scopes(db, snapshot.conversation_id, snapshot.project_id)
            artifact = (
                db.query(Artifact)
                .filter(Artifact.id == snapshot.id)
                .with_for_update()
                .first()
            )
            if artifact is None or artifact.status not in ("ready", "unavailable"):
                db.rollback()
                continue
            examined += 1
            workspace_match = workspace_storage_matches.get(artifact.conversation_id, True)
            valid = False
            if storage_available and workspace_match:
                try:
                    expected_key = _canonical_artifact_key(
                        artifact.project_id, artifact.conversation_id, artifact.id
                    )
                    if artifact.relative_storage_key == expected_key:
                        stream = store.open_verified(artifact)
                        stream.close()
                        valid = True
                except ArtifactStorageError:
                    valid = False
            previous_status = artifact.status
            if valid:
                artifact.status = "ready"
                verified += 1
                if previous_status == "unavailable":
                    recovered += 1
            else:
                artifact.status = "unavailable"
                unavailable += 1
            db.commit()
        except (HTTPException, SQLAlchemyError):
            db.rollback()
            failed += 1

    return RestoreReport(
        storage_id=str(store.storage_id),
        storage_available=storage_available,
        examined=examined,
        verified=verified,
        recovered=recovered,
        unavailable=unavailable,
        workspaces_available=workspaces_available,
        workspaces_unavailable=workspaces_unavailable,
        failed=failed,
    )
