import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.lifecycle import (
    ConversationLifecycleError,
    archive_conversation,
    get_owned_conversation_scope,
    list_owned_conversations,
    restore_conversation,
)
from ..chat.models import Conversation, Message
from ..chat.schemas import ConversationResponse, conversation_response
from ..database.session import get_db
from ..workspace.lifecycle import ArchiveStateError, archive_http_error
from ..workspace.projects import ProjectLifecycleError, select_project_for_conversation

router = APIRouter(prefix="/api", tags=["chat"])


class ConversationCreateRequest(BaseModel):
    title: str = "New Conversation"
    project_id: uuid.UUID | None = None


@router.get("/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
    state: Literal["active", "archived"] = "active",
):
    return [
        conversation_response(conversation, project)
        for conversation, project in list_owned_conversations(
            db, owner_id=user.id, state=state
        )
    ]


@router.post("/conversations", status_code=201)
async def create_conversation(
    req: ConversationCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        project = select_project_for_conversation(
            db,
            owner_id=user.id,
            project_id=req.project_id,
        )
    except ArchiveStateError as exc:
        db.rollback()
        raise archive_http_error(exc) from None
    except ProjectLifecycleError as exc:
        db.rollback()
        status_code = 409 if exc.code == "project_required" else 404
        message = (
            "Choose a project before creating a conversation"
            if exc.code == "project_required"
            else "Project not found"
        )
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "code": exc.code,
                    "message": message,
                }
            },
        ) from None

    conv = Conversation(
        user_id=user.id,
        project_id=project.id,
        title=req.title,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    return conversation_response(conv, project)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=user.id,
            conversation_id=conversation_id,
        )
    except ConversationLifecycleError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        ) from None
    return conversation_response(conversation, project)


@router.post(
    "/conversations/{conversation_id}/archive", response_model=ConversationResponse
)
async def archive_conversation_route(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        conversation, project = archive_conversation(
            db,
            owner_id=user.id,
            conversation_id=conversation_id,
        )
        db.commit()
        return conversation_response(conversation, project)
    except ArchiveStateError as exc:
        db.rollback()
        raise archive_http_error(exc) from None
    except ConversationLifecycleError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        ) from None


@router.post(
    "/conversations/{conversation_id}/restore", response_model=ConversationResponse
)
async def restore_conversation_route(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        conversation, project = restore_conversation(
            db,
            owner_id=user.id,
            conversation_id=conversation_id,
        )
        db.commit()
        return conversation_response(conversation, project)
    except ArchiveStateError as exc:
        db.rollback()
        raise archive_http_error(exc) from None
    except ConversationLifecycleError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        ) from None


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return [
        {
            "id": str(msg.id),
            "run_id": str(msg.run_id) if msg.run_id else None,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]
