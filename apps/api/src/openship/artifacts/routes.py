"""Owned, bounded conversation artifact upload and download routes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import tempfile
import uuid
from datetime import datetime
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactRef, ArtifactSummary
from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import (
    begin_artifact_delete,
    decode_artifact_name,
    finish_artifact_delete,
    mark_artifact_ready,
    mark_artifact_unavailable,
    parse_sha256_digest,
    release_artifact_reservation,
    reserve_artifact,
)
from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.chat.lifecycle import (
    ConversationLifecycleError,
    get_owned_conversation_scope,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import get_db
from src.openship.events.service import append_event
from src.openship.runs.models import Outbox
from src.openship.workspace.lifecycle import (
    ArchiveStateError,
    archive_http_error,
    assert_active_conversation,
)

router = APIRouter(prefix="/api", tags=["artifacts"])
_CHUNK_SIZE = 64 * 1024
_SPOOL_MEMORY_BYTES = 1024 * 1024


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


def configured_store() -> LocalArtifactStore:
    storage_id = settings.artifact_storage_id
    if storage_id is None:
        raise ArtifactStorageError("artifact storage identity is not configured")
    return LocalArtifactStore(settings.artifact_root, storage_id)


def _owned_conversation(db: Session, user_id: uuid.UUID, conversation_id: str) -> Conversation:
    try:
        parsed = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError) as exc:
        raise _error(422, "invalid_conversation_id", "Invalid conversation ID format") from exc
    conversation = db.query(Conversation).filter(
        Conversation.id == parsed,
        Conversation.user_id == user_id,
    ).first()
    if conversation is None:
        raise _error(404, "conversation_not_found", "Conversation not found")
    return conversation


def _active_conversation_scope(
    db: Session, user_id: uuid.UUID, conversation_id: str
) -> Conversation:
    try:
        parsed = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError) as exc:
        raise _error(422, "invalid_conversation_id", "Invalid conversation ID format") from exc
    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=parsed,
            for_update=True,
        )
    except ConversationLifecycleError:
        raise _error(404, "conversation_not_found", "Conversation not found") from None
    try:
        assert_active_conversation(conversation, project)
    except ArchiveStateError as exc:
        raise archive_http_error(exc) from None
    return conversation


def _owned_artifact(db: Session, user_id: uuid.UUID, artifact_id: str) -> Artifact:
    try:
        parsed = uuid.UUID(str(artifact_id))
    except (TypeError, ValueError) as exc:
        raise _error(422, "invalid_artifact_id", "Invalid artifact ID format") from exc
    artifact = (
        db.query(Artifact)
        .join(Conversation, Conversation.id == Artifact.conversation_id)
        .filter(Artifact.id == parsed, Conversation.user_id == user_id)
        .first()
    )
    if artifact is None:
        raise _error(404, "artifact_not_found", "Artifact not found")
    return artifact


def _summary(artifact: Artifact) -> dict:
    summary = ArtifactSummary(
        id=artifact.id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        media_type=artifact.media_type,
        conversation_id=artifact.conversation_id,
        project_id=artifact.project_id,
        name=artifact.display_name,
        source=artifact.source,
        status=artifact.status,
        created_at=artifact.created_at,
        mutability=artifact.mutability,
    )
    return summary.model_dump(mode="json")


def _cursor_encode(created_at: datetime, artifact_id: uuid.UUID) -> str:
    value = f"{created_at.isoformat()}|{artifact_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _cursor_decode(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp, artifact_id = decoded.rsplit("|", 1)
        return datetime.fromisoformat(timestamp), uuid.UUID(artifact_id)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise _error(422, "invalid_artifact_cursor", "Artifact cursor is invalid") from exc


async def _read_upload(request: Request, maximum: int):
    length_header = request.headers.get("content-length")
    expected_length = None
    if length_header is not None:
        try:
            expected_length = int(length_header)
        except ValueError as exc:
            raise _error(400, "invalid_content_length", "Content-Length is invalid") from exc
        if expected_length < 0:
            raise _error(400, "invalid_content_length", "Content-Length is invalid")
        if expected_length > maximum:
            raise _error(413, "artifact_too_large", "Upload exceeds the configured size limit")

    # The caller needs the spool after this function returns, so close it on every
    # failure path and let upload_artifact own the successful lifetime.
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES, mode="w+b")  # noqa: SIM115
    digest = hashlib.sha256()
    size = 0
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if not isinstance(chunk, bytes):
                    raise _error(400, "invalid_upload_body", "Upload body is invalid")
                size += len(chunk)
                if size > maximum:
                    raise _error(413, "artifact_too_large", "Upload exceeds the configured size limit")
                spool.write(chunk)
                digest.update(chunk)
    except TimeoutError as exc:
        spool.close()
        raise _error(408, "artifact_upload_timeout", "Upload exceeded the allowed time") from exc
    except Exception:
        spool.close()
        raise
    if expected_length is not None and expected_length != size:
        spool.close()
        raise _error(400, "content_length_mismatch", "Upload length does not match Content-Length")
    spool.seek(0)
    return spool, size, digest.hexdigest()


def _spool_chunks(spool: BinaryIO):
    spool.seek(0)
    while True:
        chunk = spool.read(_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def _append_artifact_event(db: Session, artifact: Artifact, actor_id: uuid.UUID, event_type: str) -> None:
    event = append_event(
        db=db,
        conversation_id=artifact.conversation_id,
        run_id=None,
        event_type=event_type,
        payload={
            "schema_version": 1,
            "artifact_id": str(artifact.id),
            "sha256": artifact.sha256,
            "status": artifact.status,
        },
        actor_id=actor_id,
    )
    db.flush()
    db.add(
        Outbox(
            conversation_id=artifact.conversation_id,
            event_id=event.id,
            type=event_type,
            payload=event.payload,
        )
    )


def _cleanup_failed_upload(
    db: Session,
    store: LocalArtifactStore,
    artifact: Artifact,
    observed: ArtifactRef | None,
):
    db.rollback()
    try:
        store.remove_staging(artifact.id, artifact.project_id, artifact.conversation_id)
    except ArtifactStorageError:
        pass
    try:
        if observed is not None:
            store.remove_unpublished(
                artifact.relative_storage_key,
                expected_size=observed.size_bytes,
                expected_sha256=observed.sha256,
            )
    except ArtifactStorageError:
        pass
    try:
        release_artifact_reservation(db, artifact.id)
        db.commit()
    except (HTTPException, ArtifactStorageError, SQLAlchemyError):
        db.rollback()


@router.post("/conversations/{conversation_id}/artifacts", status_code=201)
async def upload_artifact(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Stream a bounded raw upload into an immutable conversation artifact."""
    # Reserve under the same conversation → project lock order used by archive.
    # Commit the reservation before reading the request body; the final check
    # below reacquires these locks after streaming.
    conversation = _active_conversation_scope(db, user.id, conversation_id)
    key = request.headers.get("idempotency-key")
    if not key:
        raise _error(400, "missing_idempotency_key", "Idempotency-Key is required")
    try:
        expected_sha256 = parse_sha256_digest(request.headers.get("digest", ""))
        display_name = decode_artifact_name(request.headers.get("x-artifact-name", ""))
    except ValueError as exc:
        raise _error(400, "invalid_upload_headers", "Upload headers are invalid") from exc
    media_type = request.headers.get("content-type", "")
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in ("text/plain", "application/json"):
        raise _error(415, "unsupported_artifact_type", "Only plain text and JSON uploads are supported")

    try:
        store = configured_store()
        store.verify()
    except ArtifactStorageError as exc:
        raise _error(503, "artifact_storage_unavailable", "Artifact storage is unavailable") from exc

    artifact = reserve_artifact(
        db,
        conversation.id,
        conversation.project_id,
        settings.artifact_upload_max_bytes,
        "upload",
        key,
        expected_sha256=expected_sha256,
        display_name=display_name,
        media_type=media_type,
        source="upload",
    )
    replay = artifact.status == "ready"
    db.commit()

    spool = None
    observed = None
    publication_committed = False
    try:
        spool, size, observed_sha256 = await _read_upload(request, settings.artifact_upload_max_bytes)
        if observed_sha256 != expected_sha256:
            raise _error(400, "artifact_digest_mismatch", "Upload digest does not match Digest header")
        if replay:
            if artifact.size_bytes != size or artifact.sha256 != observed_sha256:
                raise _error(409, "artifact_request_conflict", "Idempotency-Key already has different content")
            return JSONResponse(status_code=200, content=_summary(artifact))

        observed = ArtifactRef(
            id=artifact.id,
            sha256=observed_sha256,
            size_bytes=size,
            media_type=media_type,
        )
        staged = store.stage(
            artifact.id,
            _spool_chunks(spool),
            observed,
            project_id=conversation.project_id,
            conversation_id=conversation.id,
        )
        # The upload may span a project or conversation archive. Refresh both
        # rows under lock after streaming, then publish and commit while the
        # scope remains protected from a concurrent archive.
        _active_conversation_scope(db, user.id, str(conversation.id))
        store.publish_file(staged, artifact.relative_storage_key)
        artifact.size_bytes = size
        artifact.sha256 = observed_sha256
        ready = mark_artifact_ready(db, artifact.id, observed)
        _append_artifact_event(db, ready, user.id, "artifact.created")
        db.commit()
        publication_committed = True
        db.refresh(ready)
        return JSONResponse(status_code=201, content=_summary(ready))
    except HTTPException:
        if not replay and not publication_committed:
            _cleanup_failed_upload(db, store, artifact, observed)
        raise
    except ArtifactStorageError as exc:
        if not replay and not publication_committed:
            _cleanup_failed_upload(db, store, artifact, observed)
        raise _error(503, "artifact_storage_unavailable", "Artifact storage is unavailable") from exc
    except Exception as exc:
        if not replay and not publication_committed:
            _cleanup_failed_upload(db, store, artifact, observed)
        raise _error(500, "artifact_upload_failed", "Artifact upload could not be completed") from exc
    finally:
        if spool is not None:
            spool.close()


@router.get("/conversations/{conversation_id}/artifacts")
async def list_artifacts(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conversation = _owned_conversation(db, user.id, conversation_id)
    query = db.query(Artifact).filter(
        Artifact.conversation_id == conversation.id,
        Artifact.status.in_(("ready", "unavailable", "deleting", "deleted")),
    )
    if cursor:
        timestamp, artifact_id = _cursor_decode(cursor)
        query = query.filter(
            or_(
                Artifact.created_at < timestamp,
                (Artifact.created_at == timestamp) & (Artifact.id < artifact_id),
            )
        )
    items = query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit + 1).all()
    more = len(items) > limit
    items = items[:limit]
    return {
        "items": [_summary(item) for item in items],
        "next_cursor": _cursor_encode(items[-1].created_at, items[-1].id) if more and items else None,
    }


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    return _summary(_owned_artifact(db, user.id, artifact_id))


def _file_chunks(stream: BinaryIO):
    try:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact_content(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    artifact = _owned_artifact(db, user.id, artifact_id)
    if artifact.status != "ready":
        raise _error(503, "artifact_unavailable", "Artifact content is unavailable")
    try:
        stream = configured_store().open_verified(artifact)
    except ArtifactStorageError as exc:
        try:
            mark_artifact_unavailable(db, artifact.id)
            db.commit()
        except (HTTPException, SQLAlchemyError):
            db.rollback()
        raise _error(503, "artifact_unavailable", "Artifact content is unavailable") from exc
    filename = quote(artifact.display_name, safe="")
    return StreamingResponse(
        _file_chunks(stream),
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Length": str(artifact.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        parsed = uuid.UUID(artifact_id)
    except ValueError as exc:
        raise _error(422, "invalid_artifact_id", "Invalid artifact ID format") from exc
    artifact = begin_artifact_delete(db, user.id, parsed)
    if artifact.status == "deleted":
        return _summary(artifact)
    db.commit()  # durable tombstone makes an unlink crash recoverable
    try:
        configured_store().remove(artifact)
    except ArtifactStorageError as exc:
        raise _error(503, "artifact_storage_unavailable", "Artifact storage is unavailable") from exc
    deleted = finish_artifact_delete(db, artifact.id)
    _append_artifact_event(db, deleted, user.id, "artifact.deleted")
    db.commit()
    db.refresh(deleted)
    return _summary(deleted)
