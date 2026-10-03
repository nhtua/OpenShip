from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation, Message
from ..database.session import get_db

router = APIRouter(prefix="/api", tags=["workspace"])


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


@router.get("/workspace/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """List all conversations for the current user."""
    # Count messages per conversation
    message_counts = (
        db.query(Message.conversation_id, func.count().label("count"))
        .filter(Message.conversation_id.in_(
            db.query(Conversation.id).filter(Conversation.user_id == user.id)
        ))
        .group_by(Message.conversation_id)
        .all()
    )
    count_map = {row.conversation_id: row.count for row in message_counts}

    conversations = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )

    return [
        {
            "id": str(conv.id),
            "title": conv.title,
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
            "message_count": count_map.get(conv.id, 0),
        }
        for conv in conversations
    ]
