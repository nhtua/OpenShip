import uuid

from sqlalchemy.orm import Session

from ..auth.models import User
from ..chat.models import Conversation, Message
from ..chat.llm import chat
from ..config import settings


def _get_or_create_conversation(
    db: Session,
    user: User,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    """Get existing conversation or create a new one for this user."""
    if conversation_id:
        conv = db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        ).first()
        if conv:
            return conv
        # Unknown conversation_id: create a new one
        pass

    conv = Conversation(
        user_id=user.id,
        title="New Conversation",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _get_chat_history(db: Session, conversation: Conversation) -> list[dict]:
    """Get recent messages for chat history (last 50)."""
    recent_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
        .limit(50)
        .all()
    )
    return [
        {"role": msg.role, "content": msg.content}
        for msg in recent_messages
    ]


def _generate_title(user_content: str) -> str:
    """Generate a conversation title from the first user message."""
    # Trim to 50 characters, add ellipsis if longer
    title = user_content.strip()
    if len(title) > 50:
        title = title[:50] + "..."
    return title


def _stream_response(
    conversation: Conversation,
    user_content: str,
    db: Session,
) -> tuple[list[str], str, str | None]:
    """Stream response from OpenAI and persist both user and assistant messages.

    Returns (chunks, assistant_message_id, new_title).
    new_title is non-None if this is the first message in the conversation.
    """
    # Check if this is the first message (no existing messages)
    existing_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .count()
    )
    is_first_message = existing_messages == 0

    # Save user message
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=user_content,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # Build chat history
    history = _get_chat_history(db, conversation)

    # Add system prompt
    messages = [
        {"role": "system", "content": "You are OpenShip, an AI DevOps co-pilot. Help users with infrastructure, deployment, and operations tasks. Be concise and practical."},
        *history,
        {"role": "user", "content": user_content},
    ]

    # Stream from OpenAI
    stream = chat(messages)

    chunks: list[str] = []
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        text = delta.content if delta and delta.content else ""
        if text:
            chunks.append(text)

    assistant_content = "".join(chunks)

    # Save assistant message
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=assistant_content,
    )
    db.add(assistant_msg)
    db.commit()

    # Generate title from first message
    new_title = None
    if is_first_message:
        new_title = _generate_title(user_content)
        conversation.title = new_title
        db.commit()

    return chunks, str(assistant_msg.id), new_title
