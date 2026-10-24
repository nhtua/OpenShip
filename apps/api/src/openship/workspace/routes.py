import base64
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactSummary
from ..artifacts.filesystem import ArtifactStorageError
from ..artifacts.models import Artifact
from ..artifacts.routes import configured_store
from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.lifecycle import list_owned_conversations
from ..chat.models import Conversation, Message
from ..chat.schemas import conversation_response
from ..database.session import get_db
from .schemas import WorkspaceReadArguments
from .service import WorkspaceServiceError, ensure_workspace
from .tools import WorkspaceToolError, read_workspace_file

router = APIRouter(prefix="/api", tags=["workspace"])


class _ReadControl:
    @staticmethod
    def raise_if_stopped():
        return None


def _workspace_cursor_encode(created_at: datetime, artifact_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{artifact_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _workspace_cursor_decode(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        if len(value) > 256:
            raise ValueError
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        timestamp, artifact_id = raw.decode("utf-8").rsplit("|", 1)
        return datetime.fromisoformat(timestamp), uuid.UUID(artifact_id)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_workspace_cursor", "message": "Workspace file cursor is invalid"}},
        ) from exc


@router.get("/workspace")
async def get_workspace(
    user: User = Depends(require_jwt),
):
    """Get the current user's workspace information."""
    return {
        "user": {
            "id": str(user.id),
            "username": user.username,
        },
        "workspace": {
            "name": f"{user.username}'s workspace",
            "user_id": str(user.id),
        },
    }


@router.get("/conversations/{conversation_id}/workspace")
async def get_conversation_workspace(
    conversation_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Return the stable association, safe quota summary and one file page."""
    try:
        parsed_id = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
        ) from exc
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == parsed_id, Conversation.user_id == user.id)
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )
    try:
        workspace = ensure_workspace(
            db,
            user.id,
            conversation.id,
            configured_store(),
        )
        db.commit()
    except WorkspaceServiceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503 if exc.code == "workspace_unavailable" else 404,
            detail={"error": {"code": exc.code, "message": "Conversation workspace is unavailable"}},
        ) from exc
    except (ArtifactStorageError, SQLAlchemyError):
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "workspace_unavailable", "message": "Conversation workspace is unavailable"}},
        ) from None

    query = db.query(Artifact).filter(
        Artifact.conversation_id == conversation.id,
        Artifact.project_id == conversation.project_id,
        Artifact.status.in_(("ready", "unavailable", "deleting", "deleted")),
    )
    if cursor:
        timestamp, artifact_id = _workspace_cursor_decode(cursor)
        if db.get_bind().dialect.name == "sqlite":
            artifact_time = func.strftime("%Y-%m-%d %H:%M:%f", Artifact.created_at)
            cursor_time = func.strftime("%Y-%m-%d %H:%M:%f", timestamp)
        else:
            artifact_time = Artifact.created_at
            cursor_time = timestamp
        query = query.filter(
            or_(
                artifact_time < cursor_time,
                (artifact_time == cursor_time) & (Artifact.id < artifact_id),
            )
        )
    rows = query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "workspace": workspace.model_dump(mode="json"),
        "files": {
            "items": [
                ArtifactSummary(
                    id=row.id,
                    sha256=row.sha256,
                    size_bytes=row.size_bytes,
                    media_type=row.media_type,
                    conversation_id=row.conversation_id,
                    project_id=row.project_id,
                    name=row.display_name,
                    source=row.source,
                    status=row.status,
                    created_at=row.created_at,
                    mutability=row.mutability,
                ).model_dump(mode="json")
                for row in rows
            ],
            "next_cursor": (
                _workspace_cursor_encode(rows[-1].created_at, rows[-1].id)
                if more and rows
                else None
            ),
        },
    }


@router.get("/conversations/{conversation_id}/workspace/files/{artifact_id}")
async def read_conversation_workspace_file(
    conversation_id: str,
    artifact_id: str,
    offset: int = Query(default=0, ge=0),
    max_bytes: int = Query(default=8 * 1024, ge=4, le=16 * 1024),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Read a bounded byte range from one exact file in an owned conversation."""
    try:
        conversation_uuid = uuid.UUID(conversation_id)
        request = WorkspaceReadArguments(
            artifact_id=uuid.UUID(artifact_id),
            offset=offset,
            max_bytes=max_bytes,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_workspace_file_request", "message": "Workspace file request is invalid"}},
        ) from exc
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_uuid,
            Conversation.user_id == user.id,
        )
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )

    from ..artifacts.routes import configured_store
    from ..database.session import SessionLocal

    context = SimpleNamespace(
        authorization=SimpleNamespace(
            action="execute_tool",
            principal_id=user.id,
            project_id=conversation.project_id,
            conversation_id=conversation.id,
        ),
        artifact_store=configured_store(),
        session_factory=SessionLocal,
    )
    try:
        result = await read_workspace_file(request, context, _ReadControl())
        return result.model_dump(mode="json")
    except WorkspaceToolError as exc:
        status = 404 if exc.code == "artifact_not_found" else 422 if exc.code in (
            "invalid_offset",
            "invalid_utf8_boundary",
        ) else 503
        raise HTTPException(
            status_code=status,
            detail={"error": {"code": exc.code, "message": exc.safe_message}},
        ) from None
    except WorkspaceServiceError as exc:
        status = 404 if exc.code == "conversation_not_found" else 503
        raise HTTPException(
            status_code=status,
            detail={
                "error": {
                    "code": exc.code,
                    "message": "Conversation workspace is unavailable",
                }
            },
        ) from None
    except ArtifactStorageError:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "workspace_unavailable", "message": "Conversation workspace is unavailable"}},
        ) from None


@router.get("/workspace/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
    state: Literal["active", "archived"] = "active",
):
    """List the same lifecycle scopes as the chat API, with message counts."""
    scopes = list_owned_conversations(db, owner_id=user.id, state=state)
    conversation_ids = [conversation.id for conversation, _project in scopes]
    count_map = {}
    if conversation_ids:
        message_counts = (
            db.query(Message.conversation_id, func.count().label("count"))
            .filter(Message.conversation_id.in_(conversation_ids))
            .group_by(Message.conversation_id)
            .all()
        )
        count_map = {row.conversation_id: row.count for row in message_counts}

    return [
        {
            **conversation_response(conversation, project).model_dump(mode="json"),
            "message_count": count_map.get(conversation.id, 0),
        }
        for conversation, project in scopes
    ]
