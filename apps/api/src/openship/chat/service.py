import uuid

from sqlalchemy.orm import Session

from ..auth.models import User
from ..chat.models import Conversation, Message
from ..chat.llm import chat
from ..config import settings
from ..workspace.models import Project


def _get_or_create_conversation(
    db: Session,
    user: User,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    """Get existing conversation or create a new one for this user's project."""
    if conversation_id:
        conv = db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        ).first()
        if conv:
            return conv
        # Unknown conversation_id: create a new one
        pass

    # Find user's default project
    project = db.query(Project).filter(
        Project.owner_user_id == user.id
    ).first()

    conv = Conversation(
        user_id=user.id,
        project_id=project.id,
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


import re


def _generate_title(user_content: str) -> str:
    """Generate a conversation title from the first user message."""
    # Trim to 50 characters, add ellipsis if longer
    title = user_content.strip()
    if len(title) > 50:
        title = title[:50] + "..."
    return title


def _extract_title_from_response(response: str) -> str | None:
    """Extract a conversation title from the LLM response.

    The system prompt asks the LLM to include a short summary prefixed with
    '## Title: ' at the end of the response.
    """
    match = re.search(r'## Title:\s*(.{1,50})', response)
    if match:
        return match.group(1).strip()
    return None


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

    # Add system prompt with title generation instruction (for first message only)
    system_content = "You are OpenShip, an AI DevOps co-pilot. Help users with infrastructure, deployment, and operations tasks. Be concise and practical."
    if is_first_message:
        system_content += "\n\nAfter your response, include a short 50-character summary on a new line prefixed with '## Title: '. This will be used as the conversation title. Do not include any other content after the title."

    messages = [
        {"role": "system", "content": system_content},
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

    # Extract title from LLM response (first message only)
    new_title = None
    cleaned_content = assistant_content
    if is_first_message:
        new_title = _extract_title_from_response(assistant_content)
        if new_title:
            conversation.title = new_title
            # Remove the title line from the response before storing
            cleaned_content = re.sub(r'## Title:\s*.{1,50}', '', assistant_content).strip()
            # Update the stored message content
            assistant_msg.content = cleaned_content
            db.commit()

    return chunks, str(assistant_msg.id), new_title, cleaned_content
