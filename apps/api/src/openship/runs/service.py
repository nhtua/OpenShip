"""Durable run command service.

Provides transactional commands for submitting turns and cancelling runs.
All mutations happen within a single database transaction for atomicity.
"""

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..capabilities.catalog import list_capability_manifests, resolve_capability
from ..capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
    ToolCommandRequest,
    canonical_digest,
)
from ..chat.models import Conversation, Message
from ..config import settings
from ..execution.errors import CapabilityError
from ..providers.contracts import RunModelConfig
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..runs.models import Event, Outbox, Run, RunJob
from ..tools.search.contracts import SearchArguments
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
    ).with_for_update().first()
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


def _turn_submission_digest(content: str, enable_tools: bool, artifact_ids) -> str:
    """Hash only client-controlled chat semantics, never current configuration."""
    return canonical_digest(
        {
            "content": content,
            "enable_tools": enable_tools,
            "artifact_ids": [str(item) for item in artifact_ids],
        }
    )


def _owned_artifact_refs(db: Session, conversation: Conversation, artifact_ids) -> tuple[ArtifactRef, ...]:
    requested = tuple(uuid.UUID(str(item)) for item in artifact_ids)
    if len(requested) != len(set(requested)):
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_artifact_selection", "message": "Selected artifacts must be distinct"}},
        )
    if not requested:
        return ()
    rows = db.query(Artifact).filter(
        Artifact.id.in_(requested),
        Artifact.conversation_id == conversation.id,
        Artifact.project_id == conversation.project_id,
        Artifact.status == "ready",
        Artifact.mutability == "immutable",
    ).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(requested):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "artifact_not_found", "message": "One or more selected artifacts are not available in this conversation"}},
        )
    return tuple(
        ArtifactRef(
            id=row.id,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            media_type=row.media_type,
        )
        for artifact_id in requested
        for row in (by_id[artifact_id],)
    )


def _tool_chat_execution(model_config: RunModelConfig, artifact_refs: tuple[ArtifactRef, ...]) -> ExecutionConfig:
    if model_config.fallbacks:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "unsupported_capability", "message": "Tool-enabled chat does not support model fallbacks in this phase"}},
        )
    profiles = (model_config.primary,)
    for profile in profiles:
        if profile.adapter != "openai" or not profile.capabilities.tool_calls:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "unsupported_capability", "message": "Tool-enabled chat requires a qualified OpenAI profile with tool-call support"}},
            )
        if profile.capabilities.parallel_tool_calls:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "unsupported_capability", "message": "Tool-enabled chat requires sequential tool-call support"}},
            )

    enabled_ids = settings.enabled_tool_ids
    refs: list[CapabilityRef] = []
    binding_refs: list[str] = []
    for manifest in list_capability_manifests():
        if manifest.id not in enabled_ids:
            continue
        ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        try:
            resolve_capability(ref, enabled_ids)
        except CapabilityError as refused:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": refused.code, "message": str(refused)}},
            ) from refused
        refs.append(ref)
        if manifest.credential_binding_ref:
            binding_refs.append(manifest.credential_binding_ref)
    if not refs:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "unsupported_capability", "message": "Enable at least one maintained tool before using tool-enabled chat"}},
        )

    return ExecutionConfig(
        graph_kind="chat",
        graph_version=2,
        allowed_capabilities=tuple(refs),
        selected_artifacts=artifact_refs,
        credential_binding_refs=tuple(binding_refs),
        loop_limits=LoopLimits(deadline_seconds=min(
            settings.workflow_run_timeout_seconds,
            int(model_config.limits.run_timeout_seconds),
        )),
    )


def submit_turn(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: str,
    content: str,
    client_request_id: str,
    provider_profile_id: str | None = None,
    fallback_profile_ids=None,
    enable_tools: bool = False,
    artifact_ids=(),
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

    try:
        submission_digest = _turn_submission_digest(content, enable_tools, artifact_ids)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_turn_request", "message": "The submitted chat request is invalid"}},
        ) from exc

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
        if existing.submission_digest is not None:
            if existing.submission_digest != submission_digest:
                raise HTTPException(
                    status_code=409,
                    detail={"error": {"code": "client_request_id_conflict", "message": "client_request_id already used with a different chat request"}},
                )
        elif enable_tools or artifact_ids:
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "client_request_id_conflict", "message": "client_request_id already used with a different chat request"}},
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
    artifact_refs = _owned_artifact_refs(db, conv, artifact_ids)
    if artifact_refs and not enable_tools:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "unsupported_capability", "message": "Selected artifacts require tool-enabled chat in this phase"}},
        )
    execution = _tool_chat_execution(config, artifact_refs) if enable_tools else None
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
        graph_version=2 if execution is not None else 1,
        execution_configuration=execution.model_dump_json() if execution is not None else None,
        submission_digest=submission_digest,
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
    event_id = uuid.uuid4()
    event = Event(
        id=event_id,
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


def _tool_command_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _tool_command_digest(command: ToolCommandRequest) -> str:
    return canonical_digest(
        {
            "capability": command.capability.model_dump(mode="json"),
            "arguments": command.arguments,
        }
    )


def submit_tool_run(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    command: ToolCommandRequest,
) -> Run:
    """Admit one model-free tool run without committing the caller's transaction."""
    request_id = str(command.client_request_id)
    try:
        command_digest = _tool_command_digest(command)
    except (TypeError, ValueError) as exc:
        raise _tool_command_error(422, "invalid_tool_arguments", "Tool arguments are invalid") from exc
    try:
        conversation_uuid = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError) as exc:
        raise _tool_command_error(422, "invalid_conversation_id", "Invalid conversation ID format") from exc

    # Request identifiers are globally unique. Check ownership before exposing
    # whether the key exists, then compare the complete command snapshot.
    existing = db.query(Run).filter(Run.client_request_id == request_id).first()
    if existing is not None:
        existing_conversation = db.query(Conversation).filter(
            Conversation.id == existing.conversation_id
        ).first()
        if existing_conversation is None or existing_conversation.user_id != user_id:
            raise _tool_command_error(404, "conversation_not_found", "Conversation not found")
        if existing.conversation_id != conversation_uuid:
            raise _tool_command_error(
                409,
                "client_request_id_conflict",
                "client_request_id already used for a different conversation",
            )
        if existing.kind != "tool" or existing.command_digest != command_digest:
            raise _tool_command_error(
                409,
                "client_request_id_conflict",
                "client_request_id already used with a different tool command",
            )
        return existing

    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_uuid,
        Conversation.user_id == user_id,
    ).with_for_update().first()
    if conversation is None:
        raise _tool_command_error(404, "conversation_not_found", "Conversation not found")

    # Recheck after locking the conversation. On PostgreSQL this serializes
    # chat/tool admission for the same conversation across both command paths.
    existing = db.query(Run).filter(Run.client_request_id == request_id).first()
    if existing is not None:
        if existing.conversation_id != conversation.id:
            raise _tool_command_error(
                409,
                "client_request_id_conflict",
                "client_request_id already used for a different conversation",
            )
        if existing.kind != "tool" or existing.command_digest != command_digest:
            raise _tool_command_error(
                409,
                "client_request_id_conflict",
                "client_request_id already used with a different tool command",
            )
        return existing

    active = db.query(Run).filter(
        Run.conversation_id == conversation.id,
        Run.status.in_(("created", "queued", "running")),
    ).first()
    if active is not None:
        raise _tool_command_error(
            409,
            "run_already_active",
            "Another run is already active on this conversation",
        )

    try:
        manifest = resolve_capability(command.capability, settings.enabled_tool_ids)
        normalized_arguments = SearchArguments.model_validate(command.arguments)
    except CapabilityError as refused:
        raise _tool_command_error(422, refused.code, str(refused)) from refused
    except ValueError as invalid:
        raise _tool_command_error(422, "invalid_tool_arguments", "Tool arguments are invalid") from invalid

    if manifest.id not in ("brave_web_search", "tavily_web_search"):
        raise _tool_command_error(422, "unsupported_tool", "This tool command is not supported")

    execution = ExecutionConfig(
        graph_kind="tool",
        graph_version=1,
        loop_limits=LoopLimits(deadline_seconds=settings.workflow_run_timeout_seconds),
        allowed_capabilities=(command.capability,),
        credential_binding_refs=(manifest.credential_binding_ref,)
        if manifest.credential_binding_ref
        else (),
    )
    run = Run(
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        client_request_id=request_id,
        command_digest=command_digest,
        tool_command_json=json.dumps(
            {
                "capability": command.capability.model_dump(mode="json"),
                "arguments": normalized_arguments.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        kind="tool",
        graph_version=1,
        execution_configuration=execution.model_dump_json(),
        status="queued",
        attempt=0,
        fence=0,
        model_selection=None,
        model_configuration=None,
    )
    db.add(run)
    db.flush()

    db.add(RunJob(run_id=run.id, fence=0))
    sequence = conversation.next_event_sequence
    payload = json.dumps(
        {
            "schema_version": 1,
            "run_id": str(run.id),
            "client_request_id": request_id,
            "capability": command.capability.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event_id = uuid.uuid4()
    event = Event(
        id=event_id,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        run_id=run.id,
        sequence=sequence,
        type="tool.submitted",
        payload=payload,
        actor_id=user_id,
    )
    db.add(event)
    conversation.next_event_sequence = sequence + 1
    db.add(
        Outbox(
            conversation_id=conversation.id,
            event_id=event_id,
            type="tool.submitted",
            payload=json.dumps(
                {"run_id": str(run.id), "client_request_id": request_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.flush()
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

    identity = db.query(Run.conversation_id).filter(Run.id == rid).first()
    if identity is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )
    conv = db.query(Conversation).filter(
        Conversation.id == identity.conversation_id,
        Conversation.user_id == user_id,
    ).with_for_update().first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # Cancellation and tool admission/publication share conversation → run →
    # job lock order. Refresh the run after waiting for the conversation lock.
    run = (
        db.query(Run)
        .filter(Run.id == rid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if run is None or run.conversation_id != conv.id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # If already cancelled or completed, return as-is (idempotent)
    if run.status in ["cancelled", "completed", "failed", "timed_out"]:
        return run

    # A cancellation moves both fences and clears the lease. The supervisor
    # sees the terminal run promptly and any late gateway publication fails
    # its live-claim check.
    prior_fence = run.fence
    run.status = "cancelled"
    run.ended_at = datetime.now(UTC)
    run.fence += 1
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == rid)
        .with_for_update()
        .first()
    )
    if job is not None:
        job.fence = run.fence
        job.owner_id = None
        job.lease_until = None

    from ..events.service import append_event
    from ..execution.recovery import cancel_tool_operations

    cancel_tool_operations(
        db,
        run_id=run.id,
        conversation_id=conv.id,
        prior_fence=prior_fence,
    )
    event = append_event(
        db,
        conversation_id=conv.id,
        run_id=run.id,
        event_type="run_cancelled",
        payload={"status": "cancelled"},
        actor_id=user_id,
    )
    db.flush()
    db.add(
        Outbox(
            conversation_id=conv.id,
            event_id=event.id,
            type=event.type,
            payload=json.dumps(
                {"event_id": str(event.id), "type": event.type},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )

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
