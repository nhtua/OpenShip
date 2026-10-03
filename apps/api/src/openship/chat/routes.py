import json
import uuid

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

router = APIRouter(prefix="/api/chat", tags=["chat"])
conversations_router = APIRouter(tags=["conversations"])


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    req: MessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    # Get or create conversation
    try:
        conversation = _get_or_create_conversation(db, user, conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        )

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
            chunks, message_id = _stream_response(conversation, req.content, db)

            for chunk_text in chunks:
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk_text})}\n\n"

            yield f"data: {json.dumps({'type': 'complete', 'message_id': message_id})}\n\n"
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


@conversations_router.get("")
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
