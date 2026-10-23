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
from ..capabilities.catalog import (
    legacy_credential_binding,
    list_capability_manifests,
    list_legacy_search_manifests,
    resolve_capability,
)
from ..capabilities.contracts import (
    CapabilityRef,
    ConnectorRef,
    ExecutionConfig,
    ExecutionConfigV2,
    ExecutionConfigV3,
    LoopLimits,
    ResourceBindingRef,
    ToolCommandRequest,
    canonical_digest,
)
from ..capabilities.definitions import get_tool_definition
from ..capabilities.policy import read_effective_tool_policy
from ..chat.lifecycle import ConversationLifecycleError, get_owned_conversation_scope
from ..chat.models import Conversation, Message
from ..config import settings
from ..connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from ..connectors.selection import ConnectorSelectionError, resolve_connector
from ..execution.errors import CapabilityError
from ..providers.contracts import RunModelConfig
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..resources.models import ConnectorBlocker, ResourceBinding
from ..runs.models import Event, Outbox, Run, RunJob
from ..workspace.lifecycle import (
    ArchiveStateError,
    archive_http_error,
    assert_active_conversation,
)
from .queue import fenced_update_run


def require_owned_conversation(
    db: Session, user_id: uuid.UUID, conversation_id: str
) -> Conversation:
    """Validate that user owns the conversation. Raises 404 if not."""
    try:
        conv_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_conversation_id",
                    "message": "Invalid conversation ID format",
                }
            },
        )

    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == user_id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        )
    return conv


def _lock_active_conversation_scope(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    """Refresh and lock conversation → project before admitting fresh work."""
    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
    except ConversationLifecycleError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                }
            },
        ) from None
    try:
        assert_active_conversation(conversation, project)
    except ArchiveStateError as exc:
        raise archive_http_error(exc) from None
    return conversation


def _selection_of(run: Run) -> tuple[str | None, list[str]]:
    """What a run was pinned to, as the pair a browser would have sent."""
    if not run.model_configuration:
        return None, []
    config = json.loads(run.model_configuration)
    return (
        config["primary"]["id"],
        [profile["id"] for profile in config.get("fallbacks", [])],
    )


def _selection_conflicts(
    run: Run, provider_profile_id: str | None, fallback_profile_ids
) -> bool:
    """Only an explicit selection that differs from the run's own is a conflict."""
    pinned_profile, pinned_fallbacks = _selection_of(run)
    if provider_profile_id is not None and provider_profile_id != pinned_profile:
        return True
    return (
        fallback_profile_ids is not None
        and list(fallback_profile_ids) != pinned_fallbacks
    )


def _resolve_selection(
    provider_profile_id: str | None, fallback_profile_ids
) -> RunModelConfig:
    """Freeze what this run will execute, or explain why it cannot.

    Today's credentials are not consulted here: a run is queued against the
    configuration it pinned, and a missing binding is reported where it runs.
    """
    try:
        registry = load_profiles(settings)
        return registry.pin_selection(
            provider_profile_id, tuple(fallback_profile_ids or ())
        )
    except ProviderError as refused:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": refused.code, "message": refused.safe_message}},
        ) from refused


def _turn_submission_digest(
    content: str,
    enable_tools: bool,
    artifact_ids,
    connector_selections=None,
    resource_binding_selections=(),
) -> str:
    """Hash only client-controlled chat semantics, never current configuration."""
    canonical = {
        "content": content,
        "enable_tools": enable_tools,
        "artifact_ids": [str(item) for item in artifact_ids],
        "connector_selections": {
            kind: str(connector_id)
            for kind, connector_id in sorted((connector_selections or {}).items())
        },
    }
    if resource_binding_selections:
        canonical["resource_binding_selections"] = sorted(
            str(item) for item in resource_binding_selections
        )
    return canonical_digest(canonical)


def _owned_artifact_refs(
    db: Session, conversation: Conversation, artifact_ids
) -> tuple[ArtifactRef, ...]:
    requested = tuple(uuid.UUID(str(item)) for item in artifact_ids)
    if len(requested) != len(set(requested)):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_artifact_selection",
                    "message": "Selected artifacts must be distinct",
                }
            },
        )
    if not requested:
        return ()
    rows = (
        db.query(Artifact)
        .filter(
            Artifact.id.in_(requested),
            Artifact.conversation_id == conversation.id,
            Artifact.project_id == conversation.project_id,
            Artifact.status == "ready",
            Artifact.mutability == "immutable",
        )
        .all()
    )
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(requested):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "artifact_not_found",
                    "message": "One or more selected artifacts are not available in this conversation",
                }
            },
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


def _connector_rows_for_project(
    db: Session, project_id: uuid.UUID, service: str
) -> int:
    return (
        db.query(Connector)
        .filter(
            Connector.kind == service,
            ((Connector.visibility == "global") & Connector.project_id.is_(None))
            | (
                (Connector.visibility == "project")
                & (Connector.project_id == project_id)
            ),
        )
        .count()
    )


def _connector_reference(resolved) -> ConnectorRef:
    return ConnectorRef(
        id=resolved.connector_id,
        kind=resolved.kind,
        configuration_revision=resolved.revision,
        visibility=resolved.visibility,
        credential_version_id=resolved.credential_version_id,
    )


def _aws_connector_reference(
    db: Session,
    *,
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    revision_number: int | None = None,
) -> ConnectorRef:
    connector = (
        db.query(Connector)
        .filter(
            Connector.id == connector_id,
            Connector.kind == "aws",
            Connector.visibility == "project",
            Connector.project_id == project_id,
        )
        .first()
    )
    if connector is None or connector.revoked_at is not None or not connector.enabled:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "aws_resource_unavailable",
                    "message": "The selected AWS Connector is not available for this project",
                }
            },
        )
    revision_number = revision_number or connector.current_revision
    revision = (
        db.query(ConnectorRevision)
        .filter(
            ConnectorRevision.connector_id == connector.id,
            ConnectorRevision.project_id == project_id,
            ConnectorRevision.visibility == "project",
            ConnectorRevision.revision == revision_number,
        )
        .first()
    )
    if revision is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "aws_resource_unavailable",
                    "message": "The selected AWS Connector revision is not available",
                }
            },
        )
    credential = (
        db.query(CredentialVersion)
        .filter(
            CredentialVersion.id == revision.credential_version_id,
            CredentialVersion.connector_id == connector.id,
            CredentialVersion.project_id == project_id,
            CredentialVersion.visibility == "project",
            CredentialVersion.kind == "aws_bootstrap",
            CredentialVersion.state == "active",
            CredentialVersion.revoked_at.is_(None),
        )
        .first()
    )
    if credential is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "aws_resource_unavailable",
                    "message": "The selected AWS Connector credential is unavailable",
                }
            },
        )
    return ConnectorRef(
        id=connector.id,
        kind="aws",
        configuration_revision=revision.revision,
        visibility="project",
        credential_version_id=credential.id,
    )


def _selected_aws_bindings(
    db: Session, *, project_id: uuid.UUID, binding_ids
) -> tuple[tuple[ResourceBindingRef, ...], tuple[ConnectorRef, ...]]:
    requested = tuple(uuid.UUID(str(item)) for item in binding_ids)
    if len(requested) != len(set(requested)) or len(requested) > 10:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_resource_selection",
                    "message": "Selected AWS resources must be distinct and within the limit",
                }
            },
        )
    if not requested:
        return (), ()
    rows = (
        db.query(ResourceBinding)
        .filter(
            ResourceBinding.id.in_(requested),
            ResourceBinding.project_id == project_id,
            ResourceBinding.state == "active",
        )
        .all()
    )
    by_id = {item.id: item for item in rows}
    if len(by_id) != len(requested):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "aws_resource_unavailable",
                    "message": "One or more selected AWS resources are unavailable",
                }
            },
        )
    binding_refs = []
    connector_refs = {}
    for binding_id in requested:
        binding = by_id[binding_id]
        connector_ref = _aws_connector_reference(
            db,
            project_id=project_id,
            connector_id=binding.connector_id,
            revision_number=binding.connector_revision,
        )
        binding_refs.append(
            ResourceBindingRef(
                id=binding.id,
                revision=binding.revision,
                connector_id=binding.connector_id,
                connector_revision=binding.connector_revision,
            )
        )
        connector_refs[connector_ref.id] = connector_ref
    return tuple(binding_refs), tuple(connector_refs.values())


def _legacy_search_ref(capability_id: str) -> CapabilityRef:
    manifest = next(
        item for item in list_legacy_search_manifests() if item.id == capability_id
    )
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


def _tool_chat_execution(
    db: Session,
    project_id: uuid.UUID,
    model_config: RunModelConfig,
    artifact_refs: tuple[ArtifactRef, ...],
    connector_selections: dict[str, uuid.UUID],
    resource_binding_selections=(),
):
    if model_config.fallbacks:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unsupported_capability",
                    "message": "Tool-enabled chat does not support model fallbacks in this phase",
                }
            },
        )
    profiles = (model_config.primary,)
    for profile in profiles:
        if profile.adapter != "openai" or not profile.capabilities.tool_calls:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "code": "unsupported_capability",
                        "message": "Tool-enabled chat requires a qualified OpenAI profile with tool-call support",
                    }
                },
            )
        if profile.capabilities.parallel_tool_calls:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "code": "unsupported_capability",
                        "message": "Tool-enabled chat requires sequential tool-call support",
                    }
                },
            )

    policy = read_effective_tool_policy(db, lock=True)
    if not policy.state_available:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "tool_policy_unavailable",
                    "message": "Managed tool availability is unavailable",
                }
            },
        )
    enabled_ids = policy.effective_ids
    refs: list[CapabilityRef] = []
    binding_refs: list[str] = []
    connector_refs: list[ConnectorRef] = []
    resource_binding_refs, aws_connector_refs = _selected_aws_bindings(
        db,
        project_id=project_id,
        binding_ids=resource_binding_selections,
    )
    explicit_services = set(connector_selections)
    managed_services: set[str] = set()
    for manifest in list_capability_manifests():
        if manifest.id not in enabled_ids:
            continue
        try:
            definition = get_tool_definition(manifest)
        except LookupError as refused:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "code": "unsupported_capability",
                        "message": "A maintained tool definition is unavailable",
                    }
                },
            ) from refused
        if not definition.model_selectable:
            continue
        if manifest.id == "aws_s3_bucket_storage":
            if resource_binding_refs:
                refs.append(
                    CapabilityRef(
                        id=manifest.id,
                        version=manifest.version,
                        manifest_digest=manifest.manifest_digest,
                    )
                )
                connector_refs.extend(aws_connector_refs)
            continue
        if manifest.id in {"brave_web_search", "tavily_web_search"}:
            service = "brave" if manifest.id == "brave_web_search" else "tavily"
            selected_id = connector_selections.get(service)
            try:
                resolved = resolve_connector(
                    db,
                    project_id=project_id,
                    service=service,
                    selected_id=selected_id,
                )
            except ConnectorSelectionError as refused:
                if service in explicit_services:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": {
                                "code": refused.code,
                                "message": "The selected search Connector is not available for this project",
                            }
                        },
                    ) from refused
                if (
                    refused.code == "connector_required"
                    and _connector_rows_for_project(db, project_id, service) == 0
                    and db.query(ProjectConnectorDefault)
                    .filter(
                        ProjectConnectorDefault.project_id == project_id,
                        ProjectConnectorDefault.service == service,
                    )
                    .first()
                    is None
                    and not managed_services
                ):
                    refs.append(_legacy_search_ref(manifest.id))
                    binding = legacy_credential_binding(manifest.id)
                    if binding:
                        binding_refs.append(binding)
                continue
            refs.append(
                CapabilityRef(
                    id=manifest.id,
                    version=manifest.version,
                    manifest_digest=manifest.manifest_digest,
                )
            )
            connector_refs.append(_connector_reference(resolved))
            managed_services.add(service)
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
        credential_binding_ref = getattr(manifest, "credential_binding_ref", None)
        if credential_binding_ref:
            binding_refs.append(credential_binding_ref)
    if not refs:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unsupported_capability",
                    "message": "Enable at least one maintained tool before using tool-enabled chat",
                }
            },
        )

    search_connector_refs = [
        item for item in connector_refs if item.kind in {"brave", "tavily"}
    ]
    if connector_refs:
        connector_refs = list({item.id: item for item in connector_refs}.values())
    if search_connector_refs:
        managed_capability_ids = {
            "brave_web_search" if ref.kind == "brave" else "tavily_web_search"
            for ref in search_connector_refs
        }
        refs = [
            ref
            for ref in refs
            if ref.id not in {"brave_web_search", "tavily_web_search"}
            or ref.id in managed_capability_ids
        ]
        active_default = (
            db.query(ProjectConnectorDefault)
            .filter(ProjectConnectorDefault.project_id == project_id)
            .first()
        )
        explicit_service = (
            next(iter(explicit_services)) if len(explicit_services) == 1 else None
        )
        explicit_preference = (
            explicit_service if explicit_service in managed_services else None
        )
        preferred_search_kind = explicit_preference or (
            active_default.service
            if active_default is not None
            and active_default.connector_id is not None
            and active_default.service in managed_services
            else None
        )
        if preferred_search_kind is not None:
            preferred_id = (
                "brave_web_search"
                if preferred_search_kind == "brave"
                else "tavily_web_search"
            )
            refs.sort(key=lambda ref: ref.id != preferred_id)
        execution_fields = {
            "graph_kind": "chat",
            "graph_version": 3 if search_connector_refs else 2,
            "allowed_capabilities": tuple(refs),
            "selected_artifacts": artifact_refs,
            "connector_refs": tuple(connector_refs),
            "resource_binding_refs": resource_binding_refs,
            "selected_resource_binding_refs": resource_binding_refs,
            "loop_limits": LoopLimits(
                deadline_seconds=min(
                    settings.workflow_run_timeout_seconds,
                    int(model_config.limits.run_timeout_seconds),
                )
            ),
        }
        if preferred_search_kind is not None:
            return (
                ExecutionConfigV3(
                    **execution_fields,
                    preferred_search_kind=preferred_search_kind,
                ),
                policy.revision,
            )
        return ExecutionConfigV2(**execution_fields), policy.revision

    return (
        ExecutionConfig(
            graph_kind="chat",
            graph_version=2,
            allowed_capabilities=tuple(refs),
            selected_artifacts=artifact_refs,
            credential_binding_refs=tuple(binding_refs),
            loop_limits=LoopLimits(
                deadline_seconds=min(
                    settings.workflow_run_timeout_seconds,
                    int(model_config.limits.run_timeout_seconds),
                )
            ),
        ),
        policy.revision,
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
    connector_selections: dict[str, uuid.UUID] | None = None,
    resource_binding_selections=(),
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
            detail={
                "error": {
                    "code": "invalid_client_request_id",
                    "message": "Invalid client_request_id format",
                }
            },
        )

    try:
        submission_digest = _turn_submission_digest(
            content,
            enable_tools,
            artifact_ids,
            connector_selections,
            resource_binding_selections,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_turn_request",
                    "message": "The submitted chat request is invalid",
                }
            },
        ) from exc

    # Check for existing run with same client_request_id (idempotency)
    existing = db.query(Run).filter(Run.client_request_id == client_request_id).first()
    if existing:
        # Verify same conversation
        if existing.conversation_id != conv.id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "client_request_id_conflict",
                        "message": "client_request_id already used for different conversation",
                    }
                },
            )
        # Check if content differs (would be a conflict)
        existing_msg = (
            db.query(Message)
            .filter(
                Message.run_id == existing.id,
                Message.role == "user",
            )
            .first()
        )
        if existing_msg and existing_msg.content != content:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "client_request_id_conflict",
                        "message": "client_request_id already used with different content",
                    }
                },
            )
        if existing.submission_digest is not None:
            if existing.submission_digest != submission_digest:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "client_request_id_conflict",
                            "message": "client_request_id already used with a different chat request",
                        }
                    },
                )
        elif enable_tools or artifact_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "client_request_id_conflict",
                        "message": "client_request_id already used with a different chat request",
                    }
                },
            )
        # The replay's selection is compared against what the run pinned, never
        # against today's profiles: an exact replay returns the original run.
        if _selection_conflicts(existing, provider_profile_id, fallback_profile_ids):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "selection_conflict",
                        "message": "client_request_id already used with a different model selection",
                    }
                },
            )
        return existing

    # Exact idempotent replays above are reads of the previously accepted run.
    # Only fresh work needs an active scope, locked in conversation → project
    # order so an archive and admission serialize on the same rows.
    conv = _lock_active_conversation_scope(db, user_id=user_id, conversation_id=conv.id)

    # Check for an active run on this conversation (one active run per conversation)
    active = (
        db.query(Run)
        .filter(
            Run.conversation_id == conv.id,
            Run.status.in_(["created", "queued", "running"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "run_already_active",
                    "message": "Another run is already active on this conversation",
                }
            },
        )

    # Resolve and validate the selection before anything is queued
    config = _resolve_selection(provider_profile_id, fallback_profile_ids)
    artifact_refs = _owned_artifact_refs(db, conv, artifact_ids)
    if resource_binding_selections and not enable_tools:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unsupported_capability",
                    "message": "Selected AWS resources require tool-enabled chat in this phase",
                }
            },
        )
    if artifact_refs and not enable_tools:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unsupported_capability",
                    "message": "Selected artifacts require tool-enabled chat in this phase",
                }
            },
        )
    execution_and_policy = (
        _tool_chat_execution(
            db,
            conv.project_id,
            config,
            artifact_refs,
            connector_selections or {},
            resource_binding_selections,
        )
        if enable_tools
        else None
    )
    execution, tool_policy_revision = (
        execution_and_policy if execution_and_policy is not None else (None, None)
    )
    admitted_context_ids = ()
    if execution is not None and getattr(execution, "connector_refs", ()):
        admitted_context_ids = tuple(
            row[0]
            for row in (
                db.query(Message.id)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
        )
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
        graph_version=execution.graph_version if execution is not None else 1,
        execution_configuration=execution.model_dump_json()
        if execution is not None
        else None,
        tool_policy_revision=tool_policy_revision,
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

    if admitted_context_ids:
        execution = execution.model_copy(
            update={"admitted_context_message_ids": (*admitted_context_ids, msg.id)}
        )
        run.execution_configuration = execution.model_dump_json()

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
        payload=json.dumps(
            {"content": content, "client_request_id": client_request_id}
        ),
    )
    db.add(event)
    conv.next_event_sequence = seq + 1

    # Create outbox row for SSE notification
    outbox = Outbox(
        conversation_id=conv.id,
        event_id=event.id,
        type="turn_submitted",
        payload=json.dumps(
            {"run_id": str(run.id), "client_request_id": client_request_id}
        ),
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
            "connector_selections": {
                kind: str(connector_id)
                for kind, connector_id in sorted(command.connector_selections.items())
            },
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
        raise _tool_command_error(
            422, "invalid_tool_arguments", "Tool arguments are invalid"
        ) from exc
    try:
        conversation_uuid = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError) as exc:
        raise _tool_command_error(
            422, "invalid_conversation_id", "Invalid conversation ID format"
        ) from exc

    # Request identifiers are globally unique. Check ownership before exposing
    # whether the key exists, then compare the complete command snapshot.
    existing = db.query(Run).filter(Run.client_request_id == request_id).first()
    if existing is not None:
        existing_conversation = (
            db.query(Conversation)
            .filter(Conversation.id == existing.conversation_id)
            .first()
        )
        if existing_conversation is None or existing_conversation.user_id != user_id:
            raise _tool_command_error(
                404, "conversation_not_found", "Conversation not found"
            )
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

    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=conversation_uuid,
            for_update=True,
        )
    except ConversationLifecycleError:
        raise _tool_command_error(
            404, "conversation_not_found", "Conversation not found"
        ) from None

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

    try:
        assert_active_conversation(conversation, project)
    except ArchiveStateError as exc:
        raise archive_http_error(exc) from None

    active = (
        db.query(Run)
        .filter(
            Run.conversation_id == conversation.id,
            Run.status.in_(("created", "queued", "running")),
        )
        .first()
    )
    if active is not None:
        raise _tool_command_error(
            409,
            "run_already_active",
            "Another run is already active on this conversation",
        )

    policy = read_effective_tool_policy(db, lock=True)
    if not policy.state_available:
        raise _tool_command_error(
            503,
            "tool_policy_unavailable",
            "Managed tool availability is unavailable",
        )
    try:
        manifest = resolve_capability(command.capability, policy.effective_ids)
        definition = get_tool_definition(manifest)
        normalized_arguments = definition.validate_arguments(command.arguments)
    except CapabilityError as refused:
        raise _tool_command_error(422, refused.code, str(refused)) from refused
    except LookupError as refused:
        raise _tool_command_error(
            422, "unsupported_tool", "This tool command is not supported"
        ) from refused
    except ValueError as invalid:
        raise _tool_command_error(
            422, "invalid_tool_arguments", "Tool arguments are invalid"
        ) from invalid

    execution_ref = command.capability
    connector_refs: tuple[ConnectorRef, ...] = ()
    binding_refs: tuple[str, ...] = ()
    resource_binding_refs: tuple[ResourceBindingRef, ...] = ()
    if manifest.schema_version == 2 and command.capability.id in {
        "brave_web_search",
        "tavily_web_search",
    }:
        service = "brave" if command.capability.id == "brave_web_search" else "tavily"
        selected_id = command.connector_selections.get(service)
        try:
            selected = resolve_connector(
                db,
                project_id=conversation.project_id,
                service=service,
                selected_id=selected_id,
            )
        except ConnectorSelectionError as refused:
            if selected_id is not None:
                raise _tool_command_error(
                    422,
                    refused.code,
                    "The selected search Connector is not available for this project",
                ) from refused
            active_default = (
                db.query(ProjectConnectorDefault)
                .filter(ProjectConnectorDefault.project_id == conversation.project_id)
                .first()
            )
            legacy_fallback = (
                refused.code == "connector_required"
                and _connector_rows_for_project(db, conversation.project_id, service)
                == 0
                and (active_default is None or active_default.service != service)
            )
            if not legacy_fallback:
                raise _tool_command_error(
                    422,
                    refused.code,
                    "Choose an eligible search Connector before using this tool",
                ) from refused
            execution_ref = _legacy_search_ref(command.capability.id)
            binding = legacy_credential_binding(command.capability.id)
            binding_refs = (binding,) if binding else ()
        else:
            connector_refs = (_connector_reference(selected),)

    if command.capability.id == "aws_check_connection":
        arguments = normalized_arguments.model_dump(mode="python")
        connector_ref = _aws_connector_reference(
            db,
            project_id=conversation.project_id,
            connector_id=arguments["connector_id"],
        )
        connector_refs = (connector_ref,)
        if arguments.get("resource_binding_id") is not None:
            selected_bindings, _connectors = _selected_aws_bindings(
                db,
                project_id=conversation.project_id,
                binding_ids=(arguments["resource_binding_id"],),
            )
            if (
                selected_bindings[0].connector_id != connector_ref.id
                or selected_bindings[0].connector_revision
                != connector_ref.configuration_revision
            ):
                raise _tool_command_error(
                    422,
                    "aws_resource_unavailable",
                    "The selected bucket binding does not use this Connector revision",
                )
            resource_binding_refs = selected_bindings
    elif command.capability.id == "aws_s3_bucket_storage":
        arguments = normalized_arguments.model_dump(mode="python")
        resource_binding_refs, connector_refs = _selected_aws_bindings(
            db,
            project_id=conversation.project_id,
            binding_ids=(arguments["resource_binding_id"],),
        )

    if connector_refs:
        execution = ExecutionConfigV2(
            graph_kind="tool",
            graph_version=2,
            loop_limits=LoopLimits(
                deadline_seconds=settings.workflow_run_timeout_seconds
            ),
            allowed_capabilities=(execution_ref,),
            connector_refs=connector_refs,
            resource_binding_refs=resource_binding_refs,
            selected_resource_binding_refs=resource_binding_refs,
        )
        graph_version = 2
    else:
        execution = ExecutionConfig(
            graph_kind="tool",
            graph_version=1,
            loop_limits=LoopLimits(
                deadline_seconds=settings.workflow_run_timeout_seconds
            ),
            allowed_capabilities=(execution_ref,),
            credential_binding_refs=binding_refs
            or (
                (manifest.credential_binding_ref,)
                if getattr(manifest, "credential_binding_ref", None)
                else ()
            ),
        )
        graph_version = 1
    run = Run(
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        client_request_id=request_id,
        command_digest=command_digest,
        tool_command_json=json.dumps(
            {
                "capability": execution_ref.model_dump(mode="json"),
                "arguments": normalized_arguments.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        kind="tool",
        graph_version=graph_version,
        execution_configuration=execution.model_dump_json(),
        tool_policy_revision=policy.revision,
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
            "capability": execution_ref.model_dump(mode="json"),
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
            selection.get("profile_id"),
            tuple(selection.get("fallback_profile_ids") or ()),
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
            detail={
                "error": {"code": "invalid_run_id", "message": "Invalid run ID format"}
            },
        )

    identity = db.query(Run.conversation_id).filter(Run.id == rid).first()
    if identity is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == identity.conversation_id,
            Conversation.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
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
    blocker = (
        db.query(ConnectorBlocker)
        .filter(ConnectorBlocker.run_id == rid, ConnectorBlocker.state == "waiting")
        .with_for_update()
        .first()
    )
    if blocker is not None:
        blocker.state = "cancelled"
        blocker.closed_at = datetime.now(UTC)
    run.status = "cancelled"
    run.ended_at = datetime.now(UTC)
    run.fence += 1
    job = db.query(RunJob).filter(RunJob.run_id == rid).with_for_update().first()
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
            detail={
                "error": {"code": "invalid_run_id", "message": "Invalid run ID format"}
            },
        )

    run = db.query(Run).filter(Run.id == rid).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    # Verify ownership through conversation
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == run.conversation_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "run_not_found", "message": "Run not found"}},
        )

    return run
