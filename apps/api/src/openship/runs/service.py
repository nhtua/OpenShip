"""Durable run command service.

Provides transactional commands for submitting turns and cancelling runs.
All mutations happen within a single database transaction for atomicity.
"""

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..chat.models import Conversation, Message
from ..config import settings
from ..providers.contracts import RunModelConfig
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..runs.models import Event, Outbox, Run, RunJob
from .queue import fenced_update_run


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
        uuid.UUID(client_request_id)
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
    return parse_run_model_config(run.model_configuration, run.id)


def parse_run_model_config(snapshot: str | None, run_id) -> RunModelConfig:
    """Read a run's frozen configuration, or explain why this installation cannot.

    Nothing is re-resolved here: a snapshot that no longer matches today's
    profiles is still what that run must execute, and one this installation
    cannot read is a safe error, never a guess.
    """
    if not snapshot:
        raise ProviderError(
            "configuration_error",
            safe_message=f"run {run_id} records no model configuration",
        )
    try:
        return RunModelConfig(**json.loads(snapshot))
    except (json.JSONDecodeError, TypeError, ValueError) as invalid:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"run {run_id} records a model configuration this installation "
                "cannot read; choose a run made against a supported schema"
            ),
        ) from invalid


def bind_run_model_config(db: Session, run: Run, fence: int) -> RunModelConfig:
    """The configuration this run will execute, binding one if it never recorded any.

    A run submitted after profiles existed already froze its configuration, so
    nothing here re-resolves it and a changed default cannot change what it
    runs. A run from before that was queued but never dispatched gets one now,
    under the fence its worker holds, and says which it was: binding at
    execution is not submission-time pinning.
    """
    if run.model_configuration:
        return parse_run_model_config(run.model_configuration, run.id)

    if run.provider_started_at is not None:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"run {run.id} was dispatched before it recorded any model configuration; "
                "its provider is not knowable after the fact"
            ),
        )

    selection = json.loads(run.model_selection) if run.model_selection else {}
    try:
        registry = load_profiles(settings)
        pinned = registry.pin_selection(
            selection.get("profile_id"), tuple(selection.get("fallback_profile_ids") or ())
        )
    except ProviderError as refused:
        raise ProviderError(
            refused.code,
            safe_message=(
                f"run {run.id} predates model profiles and cannot be bound to one: "
                f"{refused.safe_message}"
            ),
        ) from refused

    config = RunModelConfig(
        schema_version=1,
        primary=pinned.primary,
        fallbacks=pinned.fallbacks,
        limits=pinned.limits,
        binding_origin="legacy_bound_at_execution",
    )

    if not fenced_update_run(
        db=db,
        run_id=run.id,
        fence=fence,
        model_configuration=config.model_dump_json(),
    ):
        raise ProviderError(
            "lease_lost",
            safe_message=(
                f"this worker no longer holds the lease for run {run.id}, "
                "so it bound no configuration"
            ),
        )
    return config


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

    # Lock the run before checking its state. Finalization also updates this
    # row, so exactly one of completion and cancellation can win the terminal
    # transition. Refresh because the identity map may predate the lock wait.
    run = (
        db.query(Run)
        .filter(Run.id == rid)
        .with_for_update()
        .populate_existing()
        .first()
    )
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

    # A cancellation takes ownership away. The run's fence and its job's move
    # together, because the job's fence is what a worker renews against while
    # a turn is running: moving only the run's would leave a worker mid-request
    # still holding a lease it can renew, and the provider read would keep
    # consuming until the run deadline ended. The invariant the queue keeps
    # (claim_next sets both to the same value) is kept here too.
    run.status = "cancelled"
    run.ended_at = datetime.now(UTC)
    run.fence += 1
    job = db.query(RunJob).filter(RunJob.run_id == rid).first()
    if job is not None:
        job.fence = run.fence

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
