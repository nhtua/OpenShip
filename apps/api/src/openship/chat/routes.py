import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from ..auth.models import User
from ..auth.routes import require_jwt
from ..chat.models import Conversation, Message
from ..chat.schemas import ConversationResponse, MessageRequest
from ..config import settings
from ..database.session import SessionLocal, get_db
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..runs.models import Run
from ..runs.service import (
    load_run_model_config,
    require_owned_conversation,
    submit_turn,
)
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


# --- Legacy Phase-1 contract ---------------------------------------------------
#
# A client that predates runs still expects its answer inside one response, so
# this route waits. What it does not get is a provider call in this process:
# the turn is queued like every other turn, and this generator only translates
# what a worker made durable. The wait is bounded by the run's own budget,
# never by a guess about how long a model takes.
_LEGACY_POLL_SECONDS = 0.05
_LEGACY_SLACK_SECONDS = 5.0
_LEGACY_DEFAULT_BUDGET_SECONDS = 120.0
_LEGACY_UNANSWERED = ("failed", "cancelled", "timed_out", "stale")
_LEGACY_DONE = "data: [DONE]\n\n"


def _legacy_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _answer_as_legacy_sse(run_id: uuid.UUID, within: float):
    """Wait for the durable answer, then say it in the shapes a Phase-1 client expects."""
    deadline = time.monotonic() + within
    while True:
        with SessionLocal() as poll:
            run = poll.query(Run).filter(Run.id == run_id).first()
            status = run.status if run is not None else None
            if status == "completed":
                answer = poll.query(Message).filter(
                    Message.run_id == run_id,
                    Message.role == "assistant",
                ).first()
                yield _legacy_event(
                    {
                        "type": "chunk",
                        "content": answer.content if answer is not None else "",
                    }
                )
                yield _legacy_event(
                    {
                        "type": "complete",
                        "message_id": str(answer.id) if answer is not None else None,
                    }
                )
                yield _LEGACY_DONE
                return
            if status in _LEGACY_UNANSWERED:
                yield _legacy_event(
                    {
                        "type": "error",
                        "code": run.error_code or status,
                        "message": "The turn did not produce an answer.",
                    }
                )
                yield _LEGACY_DONE
                return
        if time.monotonic() >= deadline:
            yield _legacy_event(
                {
                    "type": "error",
                    "code": "run_incomplete",
                    "message": "The answer did not arrive within its own budget.",
                }
            )
            yield _LEGACY_DONE
            return
        await asyncio.sleep(_LEGACY_POLL_SECONDS)


@router.post("/chat/{conversation_id}/messages")
async def send_message(
    req: MessageRequest,
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """The Phase-1 contract, served from the durable pipeline.

    Ownership and configuration are answered before anything is queued, so a
    client that cannot make this turn never sees a run it cannot finish. The
    answer itself arrives only once a worker made it durable.
    """
    conversation = require_owned_conversation(db, user.id, str(conversation_id))

    try:
        registry = load_profiles(settings)
        availability = registry.availability(registry.default_profile_id)
    except ProviderError as refused:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "configuration_error",
                    "message": refused.safe_message,
                }
            },
        ) from refused
    if not availability.available:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "api_key_not_configured",
                    "message": availability.explanation,
                }
            },
        )

    run = submit_turn(
        db,
        user.id,
        str(conversation.id),
        req.content,
        client_request_id=str(uuid.uuid4()),
    )

    try:
        budget = load_run_model_config(run).limits
    except ProviderError:
        budget = None
    within = (
        budget.run_timeout_seconds + _LEGACY_SLACK_SECONDS
        if budget is not None
        else _LEGACY_DEFAULT_BUDGET_SECONDS
    )

    return StreamingResponse(
        _answer_as_legacy_sse(run.id, within),
        media_type="text/event-stream",
    )
