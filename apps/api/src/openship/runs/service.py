"""Durable run command service.

Provides transactional commands for submitting turns and cancelling runs.
All mutations happen within a single database transaction for atomicity.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..chat.models import Conversation, Message
from ..config import settings
from ..providers.contracts import RunModelConfig
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..runs.models import Event, Outbox, Run, RunJob


def require_owned_conversation(db: Session, user_id: uuid.UUID, conversation_id: str) -> Conversation:
    """Validate that user owns the conversation. Raises 404 if not."""
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_conversation_id", "message": "Invalid conversation ID format"}},
        )

    conv = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "conversation_not_found", "message": "Conversation not found"}},
        )
    return conv


def _selection_of(run: Run) -> tuple[str | None, list[str]]:
    """What a run was pinned to, as the pair a browser would have sent."""
    if not run.model_configuration:
        return None, []
    config = json.loads(run.model_configuration)
    return (
        config["primary"]["id"],
        [profile["id"] for profile in config.get("fallbacks", [])],
    )


def _selection_conflicts(run: Run, provider_profile_id: str | None, fallback_profile_ids) -> bool:
    """Only an explicit selection that differs from the run's own is a conflict."""
    pinned_profile, pinned_fallbacks = _selection_of(run)
    if provider_profile_id is not None and provider_profile_id != pinned_profile:
        return True
    return fallback_profile_ids is not None and list(fallback_profile_ids) != pinned_fallbacks


def _resolve_selection(provider_profile_id: str | None, fallback_profile_ids) -> RunModelConfig:
    """Freeze what this run will execute, or explain why it cannot.

    Today's credentials are not consulted here: a run is queued against the
    configuration it pinned, and a missing binding is reported where it runs.
    """
    try:
        registry = load_profiles(settings)
        return registry.pin_selection(provider_profile_id, tuple(fallback_profile_ids or ()))
    except ProviderError as refused:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": refused.code, "message": refused.safe_message}},
        ) from refused


def submit_turn(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: str,
    content: str,
    client_request_id: str,
    provider_profile_id: str | None = None,
    fallback_profile_ids=None,
) -> Run:
    """Submit a chat turn as a durable, queued run.

    Atomically persists: user message, run record, queue job, initial event, and outbox row.
    Idempotent: same client_request_id returns existing run; same key with different content = 409.
    The run also freezes the provider configuration it will execute, so a later
    change to the installation's default cannot change what it runs.
    """
    conv = require_owned_conversation(db, user_id, conversation_id)

    # Validate client_request_id format
    try:
        request_uuid = uuid.UUID(client_request_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_client_request_id", "message": "Invalid client_request_id format"}},
        )

    # Check for existing run with same client_request_id (idempotency)
    existing = db.query(Run).filter(Run.client_request_id == client_request_id).first()
    if existing:
        # Verify same conversation
        if existing.conversation_id != conv.id:
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "client_request_id_conflict",
                        "message": "client_request_id already used for different conversation"}},
            )
        # Check if content differs (would be a conflict)
        existing_msg = db.query(Message).filter(
            Message.run_id == existing.id,
            Message.role == "user",
        ).first()
        if existing_msg and existing_msg.content != content:
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "client_request_id_conflict",
                        "message": "client_request_id already used with different content"}},
            )
        # The replay's selection is compared against what the run pinned, never
        # against today's profiles: an exact replay returns the original run.
        if _selection_conflicts(existing, provider_profile_id, fallback_profile_ids):
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "selection_conflict",
                        "message": "client_request_id already used with a different model selection"}},
            )
        return existing

    # Check for an active run on this conversation (one active run per conversation)
    active = db.query(Run).filter(
        Run.conversation_id == conv.id,
        Run.status.in_(["created", "queued", "running"]),
    ).first()
    if active:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "run_already_active",
                    "message": "Another run is already active on this conversation"}},
        )

    # Resolve and validate the selection before anything is queued
    config = _resolve_selection(provider_profile_id, fallback_profile_ids)
    selection = json.dumps(
        {
            "profile_id": provider_profile_id,
            "fallback_profile_ids": list(fallback_profile_ids or ()),
        },
        sort_keys=True,
    )

    # Create the run
    run = Run(
        conversation_id=conv.id,
        project_id=conv.project_id,
        client_request_id=client_request_id,
        status="queued",
        attempt=0,
        fence=0,
        model_selection=selection,
        model_configuration=config.model_dump_json(),
    )
    db.add(run)
    db.flush()

    # Create user message linked to the run
    msg = Message(
        conversation_id=conv.id,
        run_id=run.id,
        role="user",
        content=content,
    )
    db.add(msg)
    db.flush()

    # Create queue job
    job = RunJob(
        run_id=run.id,
        fence=0,
    )
    db.add(job)

    # Create initial event (turn_submitted)
    seq = conv.next_event_sequence
    event = Event(
        project_id=conv.project_id,
        conversation_id=conv.id,
        run_id=run.id,
        sequence=seq,
        type="turn_submitted",
        payload=json.dumps({"content": content, "client_request_id": client_request_id}),
    )
    db.add(event)
    conv.next_event_sequence = seq + 1

    # Create outbox row for SSE notification
    outbox = Outbox(
        conversation_id=conv.id,
        event_id=event.id,
        type="turn_submitted",
        payload=json.dumps({"run_id": str(run.id), "client_request_id": client_request_id}),
    )
    db.add(outbox)

    db.commit()
    db.refresh(run)
    return run


def load_run_model_config(run: Run) -> RunModelConfig:
    """The provider configuration this run must execute, exactly as it froze.

    A run from before profiles records nothing, and nothing is guessed for it.
    """
    if not run.model_configuration:
        raise ProviderError(
            "configuration_error",
            safe_message=f"run {run.id} records no model configuration",
        )
    try:
        return RunModelConfig(**json.loads(run.model_configuration))
    except (json.JSONDecodeError, TypeError, ValueError) as invalid:
        raise ProviderError(
            "configuration_error",
            safe_message=f"run {run.id} records a model configuration this installation cannot read",
        ) from invalid


def request_cancel(db: Session, user_id: uuid.UUID, run_id: str) -> Run:
    """Request cancellation of a run.

    Records cancelling/cancelled status in the same transaction as its event.
    Idempotent: replay of cancel returns success.
    """
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_run_id", "message": "Invalid run ID format"}},
        )

    run = db.query(Run).filter(Run.id == rid).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # Verify ownership through conversation
    conv = db.query(Conversation).filter(
        Conversation.id == run.conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # If already cancelled or completed, return as-is (idempotent)
    if run.status in ["cancelled", "completed", "failed", "timed_out"]:
        return run

    # Update status to cancelled
    run.status = "cancelled"
    run.ended_at = datetime.now(timezone.utc)
    run.fence += 1

    # Create cancel event
    seq = conv.next_event_sequence
    event = Event(
        conversation_id=conv.id,
        run_id=run.id,
        sequence=seq,
        type="run_cancelled",
        payload=json.dumps({"status": "cancelled"}),
    )
    db.add(event)
    conv.next_event_sequence = seq + 1

    db.commit()
    db.refresh(run)
    return run


def get_run(db: Session, user_id: uuid.UUID, run_id: str) -> Run:
    """Get the status of a run.

    Validates user ownership through the conversation.
    """
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_run_id", "message": "Invalid run ID format"}},
        )

    run = db.query(Run).filter(Run.id == rid).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # Verify ownership through conversation
    conv = db.query(Conversation).filter(
        Conversation.id == run.conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    return run
