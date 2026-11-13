"""Durable run command service.

Provides transactional commands for submitting turns and cancelling runs.
All mutations happen within a single database transaction for atomicity.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..auth.models import User
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
    ExecutionConfigV4,
    LoopLimits,
    ToolCommandRequest,
    canonical_digest,
)
from ..capabilities.definitions import get_tool_definition
from ..capabilities.policy import read_effective_tool_policy
from ..chat.lifecycle import ConversationLifecycleError, get_owned_conversation_scope
from ..chat.models import Conversation, Message
from ..config import settings
from ..connectors.aws.read_catalog import load_aws_read_catalog
from ..connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from ..connectors.selection import ConnectorSelectionError, resolve_connector
from ..events.service import append_event
from ..execution.errors import CapabilityError
from ..messaging.outbox import (
    check_submission_capacity,
    enqueue_control,
    enqueue_run,
)
from ..providers.contracts import RunLimits, RunModelConfig
from ..providers.errors import ProviderError
from ..providers.registry import load_profiles
from ..resources.models import ConnectorBlocker
from ..runs.models import ACTIVE_RUN_STATUSES, Run, RunJob
from ..sandboxes.profile_schema import catalog_digest
from ..sandboxes.profiles import runtime_catalog
from ..workspace.lifecycle import (
    ArchiveStateError,
    archive_http_error,
    assert_active_conversation,
)
from .queue import fenced_update_run
from .schemas import ResumeAnalysisRequest, ResumeChatPythonRequest
from .tasks.contracts import ExecutionConfigV5, TaskContext, TaskLimits

_AGENT_MODES = ("auto", "copilot")
logger = logging.getLogger(__name__)


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


def _resolve_task_selection(provider_profile_id, fallback_profile_ids) -> RunModelConfig:
    """Freeze what a v5 task run executes, pinned to its task ceilings.

    A v5 task bills planning, steps and diagnosis against one shared set of
    task ceilings, not one generation per turn times a per-loop three: pinning
    the legacy per-loop defaults for a task would charge the whole task
    against the legacy three-attempt budget, which is not what the task's own
    records bound. ``ProviderRegistry.resolve_task`` freezes the declared
    profile with the deployment's task ceilings as defaults, lowered only by
    caps the profile actually declared.
    """
    ceilings = TaskLimits()
    try:
        registry = load_profiles(settings)
        return registry.resolve_task(
            provider_profile_id,
            tuple(fallback_profile_ids or ()),
            task_limits=RunLimits(
                max_attempts=min(
                    ceilings.max_generation_attempts,
                    settings.agentic_max_generation_attempts,
                ),
                max_output_tokens=min(
                    ceilings.max_output_tokens_per_request,
                    settings.model_max_tokens,
                ),
                run_timeout_seconds=settings.workflow_run_timeout_seconds,
                max_context_bytes=settings.model_max_context_bytes,
            ),
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
    agent_mode: str | None = None,
    agent_mode_revision: int | None = None,
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
    # Runs made before agent modes existed keep their original digest shape.
    if agent_mode is not None and agent_mode_revision is not None:
        canonical["agent_mode"] = agent_mode
        canonical["agent_mode_revision"] = agent_mode_revision
    return canonical_digest(canonical)


def _recorded_agent_mode(run: Run) -> tuple[str | None, int | None]:
    """Read the mode/revision pinned into a new-format run's selection record."""
    if not run.model_selection:
        return None, None
    try:
        selection = json.loads(run.model_selection)
    except (TypeError, ValueError):
        return None, None
    mode = selection.get("agent_mode")
    revision = selection.get("agent_mode_revision")
    if mode in _AGENT_MODES and isinstance(revision, int) and not isinstance(
        revision, bool
    ) and revision >= 1:
        return mode, revision
    return None, None


def _locked_agent_preference(db: Session, user_id: uuid.UUID) -> tuple[str, int]:
    """Lock and read this account's preference before a fresh admission."""
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "User not found",
                }
            },
        )
    mode = user.agent_mode if user.agent_mode in _AGENT_MODES else "copilot"
    revision = user.agent_mode_revision if user.agent_mode_revision else 1
    return mode, revision


def _stale_agent_preference(mode: str, revision: int) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": {
                "code": "stale_agent_preference",
                "message": (
                    f"The account preference is now {mode!r} at revision "
                    f"{revision}; reload it and submit the new task again"
                ),
            }
        },
    )


def _require_agentic_profile(config: RunModelConfig) -> None:
    """Refuse a v5 selection that the rollout has not explicitly qualified."""
    if config.primary.id not in settings.agentic_task_profile_allowlist:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "agentic_profile_not_enabled",
                    "message": (
                        f"Profile {config.primary.id!r} is not allowlisted for "
                        "agentic tasks; add it to AGENTIC_TASK_PROFILE_IDS or "
                        "choose an allowlisted profile"
                    ),
                }
            },
        )
    capabilities = config.primary.capabilities
    if config.fallbacks or not (
        capabilities.tool_calls and capabilities.structured_output
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "agentic_profile_unsupported",
                    "message": (
                        f"Profile {config.primary.id!r} cannot support agentic "
                        "tools and contract output; choose a tool-capable "
                        "profile without fallbacks"
                    ),
                }
            },
        )



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
    *,
    task_context: TaskContext | None = None,
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
    aws_discovery_ids = {"aws_read_catalog", "aws_read"} & enabled_ids
    refs: list[CapabilityRef] = []
    binding_refs: list[str] = []
    connector_refs: list[ConnectorRef] = []
    explicit_services = set(connector_selections)
    managed_services: set[str] = set()
    preferred_search_kind = None
    aws_connector_ref = None
    if aws_discovery_ids:
        try:
            resolved_aws = resolve_connector(
                db,
                project_id=project_id,
                service="aws",
                selected_id=connector_selections.get("aws"),
            )
        except ConnectorSelectionError as refused:
            if (
                "aws" in explicit_services
                or refused.code != "connector_required"
                or _connector_rows_for_project(db, project_id, "aws") > 0
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": {
                            "code": refused.code,
                            "message": "Choose one eligible AWS Connector for this project",
                        }
                    },
                ) from refused
        else:
            aws_connector_ref = _connector_reference(resolved_aws)
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
        if manifest.id in {"aws_read_catalog", "aws_read"}:
            if aws_connector_ref is None:
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
            connector_refs.append(aws_connector_ref)
            continue
        if manifest.id in {"aws_s3_bucket_storage", "aws_s3_bucket_metadata"}:
            # These direct-target helpers are explicitly attached through the
            # manual tool-command path. Automatic AWS reads use aws_read.
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

    has_aws_discovery = any(
        ref.id in {"aws_read_catalog", "aws_read"} for ref in refs
    )
    if task_context is not None:
        # v5 pins the same authority set plus the task context and the exact
        # sandbox profile snapshot. Task-specific model/loop budgets arrive
        # with Task 9; this admission does not invent them.
        catalog = runtime_catalog()
        return (
            ExecutionConfigV5(
                allowed_capabilities=tuple(refs),
                selected_artifacts=artifact_refs,
                connector_refs=tuple(connector_refs),
                credential_binding_refs=tuple(binding_refs),
                preferred_search_kind=preferred_search_kind,
                aws_read_catalog_digest=load_aws_read_catalog().digest,
                loop_limits=LoopLimits(
                    deadline_seconds=min(
                        settings.workflow_run_timeout_seconds,
                        int(model_config.limits.run_timeout_seconds),
                    )
                ),
                task_context=task_context,
                profile_catalog=catalog,
                profile_catalog_digest=catalog_digest(catalog),
            ),
            policy.revision,
        )
    if has_aws_discovery:
        loop_limits = LoopLimits(
            deadline_seconds=min(
                settings.workflow_run_timeout_seconds,
                int(model_config.limits.run_timeout_seconds),
                180,
            ),
            max_tool_calls=12,
            max_model_turns=13,
            max_generation_attempts=15,
            max_output_tokens_per_request=1000,
            max_total_output_tokens=15000,
        )
        execution_fields = {
            "graph_kind": "chat",
            "graph_version": 4,
            "allowed_capabilities": tuple(refs),
            "selected_artifacts": artifact_refs,
            "connector_refs": tuple(connector_refs),
            "loop_limits": loop_limits,
        }
        return (
            ExecutionConfigV4(
                **execution_fields,
                credential_binding_refs=tuple(binding_refs),
                preferred_search_kind=preferred_search_kind,
                aws_read_catalog_digest=load_aws_read_catalog().digest,
            ),
            policy.revision,
        )

    if search_connector_refs:
        execution_fields = {
            "graph_kind": "chat",
            "graph_version": 3,
            "allowed_capabilities": tuple(refs),
            "selected_artifacts": artifact_refs,
            "connector_refs": tuple(connector_refs),
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
    agent_mode: str | None = None,
    agent_mode_revision: int | None = None,
    *,
    _inbox_entry=None,
) -> Run:
    """Submit a chat turn as a durable, queued run.

    Atomically persists: user message, run record, queue job, initial event, and outbox row.
    Idempotent: same client_request_id returns existing run; same key with different content = 409.
    The run also freezes the provider configuration it will execute, so a later
    change to the installation's default cannot change what it runs.
    """
    if (agent_mode is None) != (agent_mode_revision is None):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_agent_mode",
                    "message": "agent_mode and agent_mode_revision must be supplied together",
                }
            },
        )
    if agent_mode is not None and agent_mode not in _AGENT_MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_agent_mode",
                    "message": "agent_mode must be 'auto' or 'copilot'",
                }
            },
        )
    if agent_mode_revision is not None and agent_mode_revision < 1:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_agent_mode",
                    "message": "agent_mode_revision must be positive",
                }
            },
        )

    conv = require_owned_conversation(db, user_id, conversation_id)
    if resource_binding_selections:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "resource_binding_retired",
                    "message": "AWS tool requests no longer accept resource bindings; use the project's AWS Connector role",
                }
            },
        )

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

    def digest_for(mode: str | None, revision: int | None) -> str:
        try:
            return _turn_submission_digest(
                content,
                enable_tools,
                artifact_ids,
                connector_selections,
                mode,
                revision,
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

    # Check for existing run with same client_request_id (idempotency). This
    # lookup happens before the current preference is read: a retry preserves
    # the mode its submission originally captured.
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

        recorded_mode, recorded_revision = _recorded_agent_mode(existing)
        if agent_mode is not None:
            if recorded_mode is None or (
                agent_mode,
                agent_mode_revision,
            ) != (recorded_mode, recorded_revision):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "client_request_id_conflict",
                            "message": (
                                "client_request_id already used with a different "
                                "agent mode selection"
                            ),
                        }
                    },
                )
            digest_mode, digest_revision = agent_mode, agent_mode_revision
        else:
            # An omitted pair means "the original submission", not "today's
            # preference"; a run from before modes existed keeps the legacy
            # digest shape.
            digest_mode, digest_revision = recorded_mode, recorded_revision
        submission_digest = digest_for(digest_mode, digest_revision)

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
        elif enable_tools or artifact_ids or agent_mode is not None:
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

    from .inbox.models import ConversationInboxEntry

    if _inbox_entry is None and db.query(ConversationInboxEntry.id).filter(
        ConversationInboxEntry.conversation_id == conv.id,
        ConversationInboxEntry.status.in_(("awaiting", "queued")),
    ).first():
        raise HTTPException(status_code=409, detail={"error": {
            "code": "conversation_queue_pending", "message": "Earlier messages are waiting in the conversation queue"
        }})

    # Check for an active run on this conversation (one active run per
    # conversation). Durable waits stay active: a task waiting for approval
    # must not be bypassed by a second execution admission.
    active = (
        db.query(Run)
        .filter(
            Run.conversation_id == conv.id,
            Run.status.in_(ACTIVE_RUN_STATUSES),
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

    check_submission_capacity(db)

    # Read the account preference only for fresh work, under its row lock, so
    # a concurrent PATCH either commits before this admission (and is seen
    # here) or waits for it. A supplied pair must match exactly; an omitted
    # pair captures the current server value.
    if _inbox_entry is not None:
        captured_mode, captured_revision = agent_mode, agent_mode_revision
    else:
        current_mode, current_revision = _locked_agent_preference(db, user_id)
        if agent_mode is not None:
            if (agent_mode, agent_mode_revision) != (current_mode, current_revision):
                raise _stale_agent_preference(current_mode, current_revision)
            captured_mode, captured_revision = agent_mode, agent_mode_revision
        else:
            captured_mode, captured_revision = current_mode, current_revision
    submission_digest = digest_for(captured_mode, captured_revision)

    # Resolve and validate the selection before anything is queued
    agentic_task = bool(settings.agentic_tasks_enabled and enable_tools)
    config = (
        _resolve_task_selection(provider_profile_id, fallback_profile_ids)
        if agentic_task
        else _resolve_selection(provider_profile_id, fallback_profile_ids)
    )
    artifact_refs = _owned_artifact_refs(db, conv, artifact_ids)
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
    if agentic_task:
        _require_agentic_profile(config)
    run_id = uuid.uuid4()
    task_context = None
    if agentic_task:
        try:
            task_context = TaskContext(
                root_run_id=run_id,
                project_id=conv.project_id,
                conversation_id=conv.id,
                initiator_id=user_id,
                scope="conversation",
                mode=captured_mode,
                graph_version=5,
                original_intent=content,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "code": "invalid_turn_request",
                        "message": "The submitted task intent is invalid",
                    }
                },
            ) from exc
    execution_and_policy = (
        _tool_chat_execution(
            db,
            conv.project_id,
            config,
            artifact_refs,
            connector_selections or {},
            task_context=task_context,
        )
        if enable_tools
        else None
    )
    execution, tool_policy_revision = (
        execution_and_policy if execution_and_policy is not None else (None, None)
    )
    admitted_context_ids = ()
    if execution is not None and (agentic_task or getattr(execution, "connector_refs", ())):
        queued_ids = db.query(ConversationInboxEntry.message_id).filter(
            ConversationInboxEntry.conversation_id == conv.id,
            ConversationInboxEntry.status.in_(("awaiting", "queued", "failed")),
        )
        history_query = db.query(Message.id).filter(
            Message.conversation_id == conv.id, ~Message.id.in_(queued_ids)
        )
        admitted_context_ids = tuple(
            row[0]
            for row in history_query.order_by(Message.created_at.asc(), Message.id.asc()).all()
        )
    selection = json.dumps(
        {
            "profile_id": provider_profile_id,
            "fallback_profile_ids": list(fallback_profile_ids or ()),
            "agent_mode": captured_mode,
            "agent_mode_revision": captured_revision,
        },
        sort_keys=True,
    )

    # Create the run
    run = Run(
        id=run_id,
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
    if _inbox_entry is None:
        msg = Message(conversation_id=conv.id, run_id=run.id, role="user", content=content)
        db.add(msg)
    else:
        msg = db.get(Message, _inbox_entry.message_id)
        msg.run_id = run.id
    db.flush()

    if admitted_context_ids or agentic_task:
        updates = {"admitted_context_message_ids": (*admitted_context_ids, msg.id)}
        if agentic_task:
            from .tasks.contracts import ConversationArtifact
            from .tasks.models import TaskPlanRecord, TaskRecord

            artifacts = db.query(Artifact).filter_by(
                project_id=conv.project_id, conversation_id=conv.id,
                status="ready", mutability="immutable",
            ).order_by(Artifact.created_at.asc(), Artifact.id.asc()).all()
            updates["conversation_artifacts"] = tuple(
                ConversationArtifact(id=item.id, name=item.display_name, media_type=item.media_type,
                                     size_bytes=item.size_bytes, sha256=item.sha256)
                for item in artifacts
            )
            tasks = db.query(TaskRecord).filter(
                TaskRecord.project_id == conv.project_id,
                TaskRecord.conversation_id == conv.id,
                TaskRecord.run_id != run.id,
            ).order_by(TaskRecord.created_at.asc(), TaskRecord.id.asc()).all()
            previous_tasks = []
            for task in tasks:
                plan = db.query(TaskPlanRecord).filter_by(run_id=task.run_id, revision=task.current_revision).one_or_none()
                previous_tasks.append(json.dumps({
                    "run_id": str(task.run_id), "status": task.status,
                    "context": json.loads(task.context_json),
                    "verified_outputs": json.loads(task.verified_outputs_json),
                    "plan": json.loads(plan.plan_json) if plan else None,
                }, ensure_ascii=False))
            updates["prior_task_context"] = tuple(previous_tasks)
        execution = execution.model_copy(
            update=updates
        )
        run.execution_configuration = execution.model_dump_json()

    # Create queue job
    job = RunJob(
        run_id=run.id,
        fence=0,
    )
    db.add(job)
    db.flush()
    from ..messaging.models import MessageTransportState
    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)

    # Create initial event (turn_submitted)
    append_event(
        db,
        conversation_id=conv.id,
        run_id=run.id,
        event_type="turn_submitted",
        payload={"content": content, "client_request_id": client_request_id},
    )

    if _inbox_entry is None:
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
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .first()
    )
    if active is not None:
        raise _tool_command_error(
            409,
            "run_already_active",
            "Another run is already active on this conversation",
        )

    from .inbox.models import ConversationInboxEntry

    if db.query(ConversationInboxEntry.id).filter(
        ConversationInboxEntry.conversation_id == conversation.id,
        ConversationInboxEntry.status.in_(("awaiting", "queued")),
    ).first():
        raise _tool_command_error(409, "conversation_queue_pending", "Earlier messages are waiting in the conversation queue")

    check_submission_capacity(db)

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

    if manifest.schema_version == 3 and command.capability.id in {
        "aws_read_catalog",
        "aws_read",
    }:
        arguments = normalized_arguments.model_dump(mode="python")
        selected_id = command.connector_selections.get("aws")
        argument_connector_id = arguments.get("connector_id")
        if (
            selected_id is not None
            and argument_connector_id is not None
            and selected_id != argument_connector_id
        ):
            raise _tool_command_error(
                422,
                "connector_scope_denied",
                "The AWS operation must use the selected project Connector",
            )
        selected_id = selected_id or argument_connector_id
        try:
            selected = resolve_connector(
                db,
                project_id=conversation.project_id,
                service="aws",
                selected_id=selected_id,
            )
        except ConnectorSelectionError as refused:
            raise _tool_command_error(
                422,
                refused.code,
                "Choose one eligible AWS Connector for this project",
            ) from refused
        connector_refs = (_connector_reference(selected),)
        if command.capability.id == "aws_read":
            arguments["connector_id"] = selected.connector_id
            normalized_arguments = definition.validate_arguments(arguments)

    if command.capability.id == "aws_check_connection":
        arguments = normalized_arguments.model_dump(mode="python")
        selected_id = command.connector_selections.get("aws")
        if selected_id is not None and selected_id != arguments["connector_id"]:
            raise _tool_command_error(
                422,
                "connector_scope_denied",
                "The AWS identity check must use the selected project Connector",
            )
        connector_ref = _aws_connector_reference(
            db,
            project_id=conversation.project_id,
            connector_id=arguments["connector_id"],
        )
        connector_refs = (connector_ref,)
    elif command.capability.id in {"aws_s3_bucket_storage", "aws_s3_bucket_metadata"}:
        arguments = normalized_arguments.model_dump(mode="python")
        selected_id = command.connector_selections.get("aws")
        if selected_id is not None and selected_id != arguments["connector_id"]:
            raise _tool_command_error(
                422,
                "connector_scope_denied",
                "The S3 read must use the selected project Connector",
            )
        connector_ref = _aws_connector_reference(
            db,
            project_id=conversation.project_id,
            connector_id=arguments["connector_id"],
        )
        connector_refs = (connector_ref,)

    if manifest.schema_version == 3 and command.capability.id in {
        "aws_read_catalog",
        "aws_read",
    }:
        execution = ExecutionConfigV4(
            graph_kind="tool",
            graph_version=3,
            loop_limits=LoopLimits(
                deadline_seconds=min(settings.workflow_run_timeout_seconds, 180)
            ),
            allowed_capabilities=(execution_ref,),
            connector_refs=connector_refs,
            aws_read_catalog_digest=manifest.aws_read_catalog_digest,
        )
        graph_version = 3
    elif connector_refs:
        execution = ExecutionConfigV2(
            graph_kind="tool",
            graph_version=2,
            loop_limits=LoopLimits(
                deadline_seconds=settings.workflow_run_timeout_seconds
            ),
            allowed_capabilities=(execution_ref,),
            connector_refs=connector_refs,
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

    model_configuration = None
    model_selection = None
    if command.capability.id == "sandbox_analysis":
        pinned_model = _resolve_selection(None, None)
        bounded_limits = pinned_model.limits.model_copy(
            update={
                "max_attempts": min(
                    pinned_model.limits.max_attempts,
                    execution.loop_limits.max_generation_attempts,
                ),
                "max_output_tokens": min(
                    pinned_model.limits.max_output_tokens,
                    execution.loop_limits.max_output_tokens_per_request,
                ),
            }
        )
        pinned_model = pinned_model.model_copy(update={"limits": bounded_limits})
        model_configuration = pinned_model.model_dump_json()
        model_selection = json.dumps(
            {
                "profile_id": pinned_model.primary.id,
                "fallback_profile_ids": [item.id for item in pinned_model.fallbacks],
            },
            sort_keys=True,
        )
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
        model_selection=model_selection,
        model_configuration=model_configuration,
    )
    db.add(run)
    db.flush()

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
    job = RunJob(run_id=run.id, fence=0)
    db.add(job)
    db.flush()
    from ..messaging.models import MessageTransportState

    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)
    append_event(
        db,
        conversation_id=conversation.id,
        run_id=run.id,
        event_type="tool.submitted",
        payload=json.loads(payload),
        actor_id=user_id,
    )
    db.flush()
    return run


def load_run_model_config(run: Run) -> RunModelConfig:
    """The provider configuration this run must execute, exactly as it froze.

    A run from before profiles records nothing, and nothing is guessed for it.
    """
    return parse_run_model_config(run.model_configuration, run.id)


def resume_analysis(
    db: Session, *, principal_id: uuid.UUID, run_id: uuid.UUID,
    command: ResumeAnalysisRequest,
) -> Run:
    """Explicit fixed-analysis admission; implementation kept out of chat service."""
    from .resume import resume_analysis as admit_resume

    return admit_resume(db, principal_id=principal_id, run_id=run_id, command=command)


def resume_chat_python(
    db: Session, *, principal_id: uuid.UUID, run_id: uuid.UUID,
    command: ResumeChatPythonRequest,
) -> Run:
    """Explicit python-chat continuation; implementation kept out of chat service.

    A task-graph v5 root continues through ``resume_task``, which keeps the
    recovery rules; graphs 2-4 keep the python implementation unchanged.
    """
    from .resume import resume_chat_python as admit_resume
    from .resume import resume_task

    prior = get_run(db=db, user_id=principal_id, run_id=str(run_id))
    if prior.kind == "chat" and prior.graph_version == 5:
        return resume_task(
            db, principal_id=principal_id, run_id=run_id, command=command
        )
    return admit_resume(db, principal_id=principal_id, run_id=run_id, command=command)


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

    from .tasks.models import TaskDecisionRecord, TaskRecord

    task = db.query(TaskRecord).filter_by(run_id=rid).with_for_update().first()
    if task is not None:
        task.status = "cancelled"
        task.fence = run.fence
        task.updated_at = run.ended_at
        decisions = (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=rid, state="open")
            .with_for_update()
            .all()
        )
        for decision in decisions:
            decision.state = "cancelled"
            decision.resolved_at = run.ended_at

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
    enqueue_control(db, run, event_id=event.id)

    db.commit()
    db.refresh(run)
    try:
        from .inbox.promotion import promote_next_message

        promote_next_message(db, conversation_id=conv.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("inbox promotion after cancellation failed")
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
