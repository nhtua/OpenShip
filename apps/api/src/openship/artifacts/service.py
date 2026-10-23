"""Caller-owned artifact reservations, quotas and lifecycle transitions."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from urllib.parse import unquote_to_bytes

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.runs.models import Run
from src.openship.workspace.lifecycle import (
    ArchiveStateError,
    archive_http_error,
    assert_active_conversation,
)
from src.openship.workspace.models import Project

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEARCH_KIND = "search_result"
_UPLOAD_KIND = "upload"
_ZERO_SHA256 = "0" * 64


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def parse_sha256_digest(value: str) -> str:
    """Parse the required RFC-style SHA-256 Digest header into lowercase hex."""
    if not isinstance(value, str) or not value.startswith("sha-256="):
        raise ValueError("Digest must contain sha-256")
    encoded = value[len("sha-256=") :]
    try:
        digest = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Digest is malformed") from exc
    if len(digest) != hashlib.sha256().digest_size:
        raise ValueError("Digest must contain 32 bytes")
    return digest.hex()


def decode_artifact_name(value: str) -> str:
    """Decode percent-encoded UTF-8 display metadata without accepting paths."""
    if not isinstance(value, str) or re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("Artifact name contains an invalid percent escape")
    try:
        name = unquote_to_bytes(value).decode("utf-8", "strict")
    except (UnicodeDecodeError, AttributeError) as exc:
        raise ValueError("Artifact name must be percent-encoded UTF-8") from exc
    if (
        not name
        or name in (".", "..")
        or len(name.encode("utf-8")) > 255
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError("Artifact name is invalid")
    return name


def validate_artifact_name(name: str) -> str:
    """Validate an already-decoded display name used by trusted callers."""
    try:
        return decode_artifact_name(name)
    except ValueError as exc:
        raise _error(422, "invalid_artifact_name", "Artifact name is invalid") from exc


def _validate_media_type(media_type: str) -> str:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized not in ("application/json", "text/plain"):
        raise _error(415, "unsupported_artifact_type", "Only plain text and JSON artifacts are supported")
    return normalized


def _lock_scopes(db: Session, conversation_id: uuid.UUID, project_id: uuid.UUID):
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if conversation is None:
        raise _error(404, "conversation_not_found", "Conversation not found")
    project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if project is None:
        raise _error(404, "project_not_found", "Project not found")
    return conversation, project


def _quota_rows(db: Session, project_id: uuid.UUID, conversation_id: uuid.UUID):
    project_quota = (
        db.query(ArtifactQuota)
        .filter(ArtifactQuota.project_id == project_id, ArtifactQuota.scope == "project")
        .with_for_update()
        .first()
    )
    conversation_quota = (
        db.query(ArtifactQuota)
        .filter(
            ArtifactQuota.project_id == project_id,
            ArtifactQuota.conversation_id == conversation_id,
            ArtifactQuota.scope == "conversation",
        )
        .with_for_update()
        .first()
    )
    if project_quota is None:
        project_quota = ArtifactQuota(project_id=project_id, scope="project")
        db.add(project_quota)
        db.flush()
    if conversation_quota is None:
        conversation_quota = ArtifactQuota(
            project_id=project_id,
            conversation_id=conversation_id,
            scope="conversation",
        )
        db.add(conversation_quota)
        db.flush()
    return project_quota, conversation_quota


def reserve_artifact(
    db: Session,
    conversation_id: uuid.UUID,
    project_id: uuid.UUID,
    max_bytes: int,
    kind: str,
    request_key: str,
    *,
    expected_sha256: str | None = None,
    display_name: str = "artifact",
    media_type: str = "application/json",
    source: str | None = None,
    originating_run_id: uuid.UUID | None = None,
    originating_operation_id: uuid.UUID | None = None,
    provenance: str | None = None,
) -> Artifact:
    """Reserve per-conversation and project quota without committing the caller's transaction."""
    if kind not in (_SEARCH_KIND, _UPLOAD_KIND):
        raise _error(422, "invalid_artifact_kind", "Artifact kind is invalid")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise _error(422, "invalid_artifact_size", "Artifact size limit is invalid")
    maximum = settings.artifact_search_reservation_bytes if kind == _SEARCH_KIND else settings.artifact_upload_max_bytes
    if max_bytes > maximum:
        raise _error(413, "artifact_too_large", "Artifact exceeds the configured size limit")
    try:
        normalized_key = str(uuid.UUID(str(request_key)))
    except (TypeError, ValueError) as exc:
        raise _error(422, "invalid_idempotency_key", "Idempotency-Key must be a UUID") from exc
    display_name = validate_artifact_name(display_name)
    media_type = _validate_media_type(media_type)
    if expected_sha256 is not None and not _SHA256.fullmatch(expected_sha256):
        raise _error(422, "invalid_artifact_digest", "Artifact digest is invalid")
    _lock_scopes(db, conversation_id, project_id)
    existing = (
        db.query(Artifact)
        .filter(Artifact.conversation_id == conversation_id, Artifact.request_key == normalized_key)
        .with_for_update()
        .first()
    )
    if existing is not None:
        if (
            existing.kind != kind
            or existing.display_name != display_name
            or existing.media_type != media_type
            or (expected_sha256 is not None and existing.sha256 != expected_sha256)
        ):
            raise _error(409, "artifact_request_conflict", "Idempotency-Key already has different content")
        if existing.status == "ready":
            return existing
        raise _error(409, "artifact_upload_in_progress", "An upload with this Idempotency-Key is still in progress")

    project_quota, conversation_quota = _quota_rows(db, project_id, conversation_id)
    if kind == _UPLOAD_KIND:
        incomplete = (
            db.query(Artifact)
            .filter(
                Artifact.conversation_id == conversation_id,
                Artifact.kind == _UPLOAD_KIND,
                Artifact.status == "staging",
            )
            .count()
        )
        if incomplete >= settings.artifact_incomplete_uploads_per_conversation:
            raise _error(429, "too_many_incomplete_uploads", "Too many uploads are already in progress")

    project_over = (
        project_quota.committed_bytes + project_quota.reserved_bytes + max_bytes
        > settings.artifact_project_quota_bytes
        or project_quota.committed_count + project_quota.reserved_count + 1
        > settings.artifact_project_quota_count
    )
    conversation_over = (
        conversation_quota.committed_bytes + conversation_quota.reserved_bytes + max_bytes
        > settings.artifact_conversation_quota_bytes
        or conversation_quota.committed_count + conversation_quota.reserved_count + 1
        > settings.artifact_conversation_quota_count
    )
    if project_over or conversation_over:
        raise _error(413, "artifact_quota_exceeded", "Artifact storage quota is full")

    artifact_id = uuid.uuid4()
    artifact = Artifact(
        id=artifact_id,
        project_id=project_id,
        conversation_id=conversation_id,
        originating_run_id=originating_run_id,
        originating_operation_id=originating_operation_id,
        kind=kind,
        media_type=media_type,
        display_name=display_name,
        size_bytes=0,
        sha256=expected_sha256 or _ZERO_SHA256,
        relative_storage_key=(
            f"projects/{project_id}/conversations/{conversation_id}/artifacts/{artifact_id}/content"
        ),
        status="staging",
        mutability="immutable",
        source=source or kind,
        provenance=provenance,
        request_key=normalized_key,
        reserved_bytes=max_bytes,
    )
    project_quota.reserved_bytes += max_bytes
    project_quota.reserved_count += 1
    conversation_quota.reserved_bytes += max_bytes
    conversation_quota.reserved_count += 1
    db.add(artifact)
    db.flush()
    return artifact


def _lock_artifact_and_scopes(db: Session, artifact_id: uuid.UUID) -> Artifact:
    existing = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if existing is None:
        raise _error(404, "artifact_not_found", "Artifact not found")
    _lock_scopes(db, existing.conversation_id, existing.project_id)
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).with_for_update().first()
    if artifact is None:
        raise _error(404, "artifact_not_found", "Artifact not found")
    return artifact


def mark_artifact_ready(db: Session, artifact_id: uuid.UUID, observed) -> Artifact:
    """Promote already-published bytes and consume their reserved quota in one caller transaction."""
    artifact = _lock_artifact_and_scopes(db, artifact_id)
    if observed.id != artifact.id or observed.media_type != artifact.media_type:
        raise _error(409, "artifact_observation_conflict", "Published artifact does not match its reservation")
    if artifact.status == "ready":
        if artifact.sha256 == observed.sha256 and artifact.size_bytes == observed.size_bytes:
            return artifact
        raise _error(409, "artifact_observation_conflict", "Artifact was already published with different bytes")
    if artifact.status != "staging":
        raise _error(409, "artifact_not_staging", "Artifact reservation is no longer active")
    if artifact.sha256 != _ZERO_SHA256 and artifact.sha256 != observed.sha256:
        raise _error(409, "artifact_digest_mismatch", "Artifact digest does not match its reservation")
    if observed.size_bytes > artifact.reserved_bytes:
        raise _error(413, "artifact_too_large", "Artifact exceeds its reserved size")
    project_quota, conversation_quota = _quota_rows(
        db, artifact.project_id, artifact.conversation_id
    )
    reserved = artifact.reserved_bytes
    for quota in (project_quota, conversation_quota):
        quota.reserved_bytes = max(0, quota.reserved_bytes - reserved)
        quota.reserved_count = max(0, quota.reserved_count - 1)
        quota.committed_bytes += observed.size_bytes
        quota.committed_count += 1
    artifact.size_bytes = observed.size_bytes
    artifact.sha256 = observed.sha256
    artifact.reserved_bytes = 0
    artifact.status = "ready"
    db.flush()
    return artifact


def release_artifact_reservation(db: Session, artifact_id: uuid.UUID) -> bool:
    """Release a failed staging reservation; caller removes hidden bytes separately."""
    artifact = _lock_artifact_and_scopes(db, artifact_id)
    if artifact.status != "staging":
        return False
    project_quota, conversation_quota = _quota_rows(
        db, artifact.project_id, artifact.conversation_id
    )
    for quota in (project_quota, conversation_quota):
        quota.reserved_bytes = max(0, quota.reserved_bytes - artifact.reserved_bytes)
        quota.reserved_count = max(0, quota.reserved_count - 1)
    artifact.reserved_bytes = 0
    artifact.status = "deleted"
    db.flush()
    return True


def begin_artifact_delete(db: Session, user_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact:
    """Authorize and tombstone an artifact unless an active run has pinned its exact version."""
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if artifact is None:
        raise _error(404, "artifact_not_found", "Artifact not found")
    conversation, project = _lock_scopes(db, artifact.conversation_id, artifact.project_id)
    if conversation.user_id != user_id:
        raise _error(404, "artifact_not_found", "Artifact not found")
    try:
        assert_active_conversation(conversation, project)
    except ArchiveStateError as exc:
        raise archive_http_error(exc) from None
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).with_for_update().first()
    if artifact.status in ("deleted", "deleting"):
        return artifact
    if artifact.status not in ("ready", "unavailable"):
        raise _error(409, "artifact_unavailable", "Artifact cannot be deleted while it is being published")
    require_artifact_not_pinned(db, artifact)
    artifact.status = "deleting"
    db.flush()
    return artifact


def require_artifact_not_pinned(db: Session, artifact: Artifact) -> None:
    """Refuse deletion when a live run or operation pins this exact snapshot."""
    artifact_text_id = str(artifact.id)
    runs = db.query(Run).filter(
        Run.conversation_id == artifact.conversation_id,
        Run.status.in_(("created", "queued", "running")),
    ).all()
    for run in runs:
        try:
            execution = json.loads(run.execution_configuration or "{}")
            if any(item.get("id") == artifact_text_id for item in execution.get("selected_artifacts", ())):
                raise _error(409, "artifact_is_pinned", "Artifact is selected by an active run")
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
    from src.openship.execution.models import ToolOperation

    operations = db.query(ToolOperation).filter(
        ToolOperation.conversation_id == artifact.conversation_id,
        ToolOperation.status.in_(("admitted", "dispatching", "running")),
    ).all()
    for operation in operations:
        try:
            refs = json.loads(operation.input_artifact_refs or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if any(item.get("id") == artifact_text_id for item in refs if isinstance(item, dict)):
            raise _error(409, "artifact_is_pinned", "Artifact is selected by an active run")


def finish_artifact_delete(db: Session, artifact_id: uuid.UUID) -> Artifact:
    artifact = _lock_artifact_and_scopes(db, artifact_id)
    if artifact.status == "deleted":
        return artifact
    if artifact.status != "deleting":
        raise _error(409, "artifact_delete_not_started", "Artifact deletion is not pending")
    project_quota, conversation_quota = _quota_rows(
        db, artifact.project_id, artifact.conversation_id
    )
    for quota in (project_quota, conversation_quota):
        quota.committed_bytes = max(0, quota.committed_bytes - artifact.size_bytes)
        quota.committed_count = max(0, quota.committed_count - 1)
    artifact.status = "deleted"
    db.flush()
    return artifact


def mark_artifact_unavailable(db: Session, artifact_id: uuid.UUID) -> Artifact:
    artifact = _lock_artifact_and_scopes(db, artifact_id)
    if artifact.status == "ready":
        artifact.status = "unavailable"
        db.flush()
    return artifact
