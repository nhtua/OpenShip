import uuid
from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation, Message
from ..chat.schemas import ConversationResponse
from ..database.session import get_db
from ..workspace.models import Project

router = APIRouter(prefix="/api", tags=["chat"])


class ConversationCreateRequest(TypedDict):
    title: str


@router.get("/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    user_conversations = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )

    return [
        ConversationResponse(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
        )
        for conv in user_conversations
    ]


@router.post("/conversations", status_code=201)
async def create_conversation(
    req: ConversationCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    # Find user's default project
    project = db.query(Project).filter(
        Project.owner_user_id == user.id
    ).first()
    if not project:
        return {"error": {"code": "project_not_found", "message": f"No project found for user {user.id}"}}

    conv = Conversation(
        user_id=user.id,
        project_id=project.id,
        title=req.get("title", "New Conversation"),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    return ConversationResponse(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
    )


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
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]
