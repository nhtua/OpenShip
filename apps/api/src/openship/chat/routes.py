import json
import uuid
from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation, Message
from ..chat.schemas import ConversationResponse, MessageRequest
from ..chat.service import _get_or_create_conversation, _stream_response
from ..config import settings
from ..database.session import get_db

router = APIRouter(prefix="/api", tags=["chat"])


class ConversationCreateRequest(TypedDict):
    title: str


@router.post("/chat/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    req: MessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    # Parse conversation_id: accept "new" or a valid UUID
    parsed_conversation_id: uuid.UUID | None = None
    if conversation_id != "new":
        try:
            parsed_conversation_id = uuid.UUID(conversation_id)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
            )

    # Get or create conversation
    conversation = _get_or_create_conversation(db, user, parsed_conversation_id)
    created_new = parsed_conversation_id is None

    # Check API key availability
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "api_key_not_configured",
                    "message": "AI service is not configured. Please set OPENAI_API_KEY.",
                }
            },
        )

    # Stream response
    def generate():
        try:
            # Notify frontend of new conversation ID if one was just created
            if created_new:
                yield f"data: {json.dumps({'type': 'conversation_created', 'conversation_id': str(conversation.id)})}\n\n"

            chunks, message_id, new_title, cleaned_content = _stream_response(conversation, req.content, db)

            # Stream the response chunks
            for chunk_text in chunks:
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk_text})}\n\n"

            # Notify frontend of new title if this was the first message
            if new_title:
                yield f"data: {json.dumps({'type': 'title_updated', 'conversation_id': str(conversation.id), 'title': new_title})}\n\n"

            yield f"data: {json.dumps({'type': 'complete', 'message_id': message_id})}\n\n"

            # Send cleaned content after complete (so message ID is assigned)
            if new_title:
                yield f"data: {json.dumps({'type': 'message_updated', 'message_id': message_id, 'content': cleaned_content})}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@router.post("/conversations")
async def create_conversation(
    req: ConversationCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    conv = Conversation(
        user_id=user.id,
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
