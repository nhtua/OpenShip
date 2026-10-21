"""Lazily and idempotently associate owned conversations with artifact storage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import ArtifactQuota
from src.openship.chat.models import Conversation
from src.openship.config import settings

from .models import ConversationWorkspace
from .schemas import WorkspaceQuotaSummary, WorkspaceSummary


@dataclass(frozen=True, slots=True)
class WorkspaceServiceError(Exception):
    code: str


def ensure_workspace(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    store: LocalArtifactStore,
) -> WorkspaceSummary:
    """Return the stable workspace binding after ownership and storage checks.

    The conversation row is the serialization point shared with run admission.
    This operation never creates directories or changes artifact metadata.
    """
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .with_for_update()
        .first()
    )
    if conversation is None:
        raise WorkspaceServiceError("conversation_not_found")

    relative_root = f"projects/{conversation.project_id}/conversations/{conversation.id}"
    workspace = (
        db.query(ConversationWorkspace)
        .filter(ConversationWorkspace.conversation_id == conversation.id)
        .with_for_update()
        .first()
    )
    if workspace is None:
        try:
            store.verify()
            status = "available"
        except ArtifactStorageError:
            status = "unavailable"
        workspace = ConversationWorkspace(
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            schema_version=1,
            relative_root=relative_root,
            storage_id=store.storage_id,
            status=status,
        )
        db.add(workspace)
        db.flush()
        db.refresh(workspace, attribute_names=["created_at"])
    else:
        if (
            workspace.project_id != conversation.project_id
            or workspace.relative_root != relative_root
            or workspace.schema_version != 1
            or workspace.status == "deleted"
        ):
            raise WorkspaceServiceError("workspace_unavailable")
        try:
            store.verify()
            available = workspace.storage_id == store.storage_id
        except ArtifactStorageError:
            available = False
        workspace.status = "available" if available else "unavailable"
        db.flush()

    quota = (
        db.query(ArtifactQuota)
        .filter(
            ArtifactQuota.project_id == conversation.project_id,
            ArtifactQuota.conversation_id == conversation.id,
            ArtifactQuota.scope == "conversation",
        )
        .first()
    )
    return WorkspaceSummary(
        id=workspace.id,
        conversation_id=workspace.conversation_id,
        project_id=workspace.project_id,
        schema_version=1,
        relative_root=workspace.relative_root,
        storage_id=workspace.storage_id,
        status=workspace.status,
        created_at=workspace.created_at,
        quota=WorkspaceQuotaSummary(
            committed_bytes=quota.committed_bytes if quota else 0,
            committed_count=quota.committed_count if quota else 0,
            reserved_bytes=quota.reserved_bytes if quota else 0,
            reserved_count=quota.reserved_count if quota else 0,
            max_bytes=settings.artifact_conversation_quota_bytes,
            max_count=settings.artifact_conversation_quota_count,
        ),
    )
