"""Conversation-scoped logical files backed by existing immutable artifacts."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.requests import Request as StarletteRequest

from src.openship.artifacts.logical_files import advance_file, create_file
from src.openship.artifacts.models import Artifact, FileVersion, LogicalFile
from src.openship.artifacts.routes import (
    _active_conversation_scope,
    _cursor_decode,
    _cursor_encode,
    _owned_conversation,
    delete_artifact,
    upload_artifact,
)
from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.database.session import get_db

router = APIRouter(prefix="/api/conversations/{conversation_id}/files", tags=["files"])
_MAX_SOURCE_BYTES = 256 * 1024


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: UUID
    request_key: UUID


class VersionRequest(CaptureRequest):
    base_version_id: UUID


async def _read_source(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/plain":
        raise HTTPException(
            status_code=415,
            detail={"error": {"code": "unsupported_source_type", "message": "Source edits require text/plain"}},
        )
    chunks = []
    size = 0
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                size += len(chunk)
                if size > _MAX_SOURCE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={"error": {"code": "source_too_large", "message": "Source exceeds the configured size limit"}},
                    )
                chunks.append(chunk)
    except TimeoutError:
        raise HTTPException(
            status_code=408,
            detail={"error": {"code": "source_upload_timeout", "message": "Source upload exceeded the allowed time"}},
        ) from None
    content = b"".join(chunks)
    try:
        decoded = content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_source_encoding", "message": "Source must be valid UTF-8"}},
        ) from None
    if "\x00" in decoded:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_source_content", "message": "Source must not contain NUL"}},
        )
    return content


def _upload_request(content: bytes, request_key: UUID, name: str) -> StarletteRequest:
    chunks = [content]
    digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    headers = [
        (b"content-type", b"text/plain"),
        (b"content-length", str(len(content)).encode("ascii")),
        (b"idempotency-key", str(request_key).encode("ascii")),
        (b"digest", f"sha-256={digest}".encode("ascii")),
        (b"x-artifact-name", quote(name, safe="").encode("ascii")),
    ]

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return StarletteRequest(
        {"type": "http", "method": "POST", "path": "/artifacts", "headers": headers},
        receive,
    )


def _version(value: FileVersion) -> dict:
    return {
        "id": str(value.id),
        "file_id": str(value.file_id),
        "artifact_id": str(value.artifact_id),
        "base_version_id": str(value.base_version_id)
        if value.base_version_id
        else None,
        "created_at": value.created_at.isoformat(),
    }


def _file(value: LogicalFile, version: FileVersion, artifact) -> dict:
    return {
        "id": str(value.id),
        "name": value.name,
        "current_version_id": str(value.current_version_id),
        "current_artifact": {
            "id": str(artifact.id),
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
            "status": artifact.status,
        },
        "created_at": value.created_at.isoformat(),
    }


@router.post("/from-artifact")
def create_from_artifact(
    conversation_id: UUID,
    body: CaptureRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    file, version = create_file(
        db, user.id, conversation_id, body.artifact_id, body.request_key
    )
    db.commit()
    db.refresh(file)
    db.refresh(version)
    artifact = db.get(Artifact, version.artifact_id)
    return {"file": _file(file, version, artifact), "version": _version(version)}


@router.post("/{file_id}/versions")
def append_captured_version(
    conversation_id: UUID,
    file_id: UUID,
    body: VersionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    version = advance_file(
        db,
        user.id,
        conversation_id,
        file_id,
        body.base_version_id,
        body.artifact_id,
        body.request_key,
    )
    db.commit()
    db.refresh(version)
    return _version(version)


@router.put("/{file_id}/content")
async def replace_content(
    conversation_id: UUID,
    file_id: UUID,
    request: Request,
    base_version_id: UUID = Query(),
    request_key: UUID = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Capture bounded replacement source, then compare-and-swap its file head."""
    content = await _read_source(request)
    conversation = _active_conversation_scope(db, user.id, str(conversation_id))
    file = (
        db.query(LogicalFile)
        .filter_by(
            id=file_id,
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            deleted_at=None,
        )
        .first()
    )
    if file is None:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "file_not_found", "message": "File not found"}},
        )
    name = file.name
    db.rollback()

    response = await upload_artifact(
        str(conversation_id), _upload_request(content, request_key, name), db, user
    )
    artifact_id = UUID(json.loads(response.body)["id"])
    version = advance_file(
        db,
        user.id,
        conversation_id,
        file_id,
        base_version_id,
        artifact_id,
        request_key,
    )
    db.commit()
    db.refresh(version)
    return _version(version)


@router.get("")
def list_files(
    conversation_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conversation = _owned_conversation(db, user.id, str(conversation_id))
    query = (
        db.query(LogicalFile, FileVersion, Artifact)
        .join(FileVersion, FileVersion.id == LogicalFile.current_version_id)
        .join(Artifact, Artifact.id == FileVersion.artifact_id)
        .filter(
            LogicalFile.conversation_id == conversation.id,
            LogicalFile.deleted_at.is_(None),
        )
    )
    if cursor:
        timestamp, file_id = _cursor_decode(cursor)
        query = query.filter(
            or_(
                LogicalFile.created_at < timestamp,
                (LogicalFile.created_at == timestamp) & (LogicalFile.id < file_id),
            )
        )
    rows = (
        query.order_by(LogicalFile.created_at.desc(), LogicalFile.id.desc())
        .limit(limit + 1)
        .all()
    )
    more, rows = len(rows) > limit, rows[:limit]
    return {
        "items": [_file(file, version, artifact) for file, version, artifact in rows],
        "next_cursor": _cursor_encode(rows[-1].created_at, rows[-1].id)
        if more
        else None,
    }


@router.get("/{file_id}/versions")
def list_versions(
    conversation_id: UUID,
    file_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conversation = _owned_conversation(db, user.id, str(conversation_id))
    file = (
        db.query(LogicalFile)
        .filter_by(id=file_id, conversation_id=conversation.id)
        .first()
    )
    if file is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "file_not_found", "message": "File not found"}},
        )
    query = db.query(FileVersion).filter_by(
        file_id=file.id, conversation_id=conversation.id
    )
    if cursor:
        timestamp, version_id = _cursor_decode(cursor)
        query = query.filter(
            or_(
                FileVersion.created_at < timestamp,
                (FileVersion.created_at == timestamp) & (FileVersion.id < version_id),
            )
        )
    rows = (
        query.order_by(FileVersion.created_at.desc(), FileVersion.id.desc())
        .limit(limit + 1)
        .all()
    )
    more, rows = len(rows) > limit, rows[:limit]
    return {
        "items": [_version(row) for row in rows],
        "next_cursor": _cursor_encode(rows[-1].created_at, rows[-1].id)
        if more
        else None,
    }


@router.delete("/{file_id}")
def delete_file(
    conversation_id: UUID,
    file_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conversation = _active_conversation_scope(db, user.id, str(conversation_id))
    file = (
        db.query(LogicalFile)
        .filter_by(
            id=file_id,
            conversation_id=conversation.id,
            project_id=conversation.project_id,
        )
        .with_for_update()
        .first()
    )
    if file is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "file_not_found", "message": "File not found"}},
        )
    if file.deleted_at is None:
        file.deleted_at = datetime.now(UTC)
        db.commit()
    return {"id": str(file.id), "deleted": True}


@router.delete("/{file_id}/versions/{version_id}")
async def prune_version(
    conversation_id: UUID,
    file_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conversation = _active_conversation_scope(db, user.id, str(conversation_id))
    version = (
        db.query(FileVersion)
        .join(LogicalFile, LogicalFile.id == FileVersion.file_id)
        .filter(
            LogicalFile.id == file_id,
            LogicalFile.conversation_id == conversation.id,
            LogicalFile.project_id == conversation.project_id,
            FileVersion.id == version_id,
            FileVersion.conversation_id == conversation.id,
            FileVersion.project_id == conversation.project_id,
        )
        .first()
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "file_version_not_found",
                    "message": "File version not found",
                }
            },
        )
    return await delete_artifact(str(version.artifact_id), db, user)
