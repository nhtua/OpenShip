"""Caller-owned transactions for fenced tool admission and shared slots."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..capabilities.catalog import resolve_capability
from ..capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    canonical_digest,
    decode_execution_config,
)
from ..capabilities.definitions import (
    get_tool_definition,
    get_tool_definition_by_id,
)
from ..capabilities.policy import ToolPolicyError, require_current_tool_policy
from ..chat.models import Conversation
from ..config import settings
from ..runs.models import Run, RunJob
from ..runs.queue import Claim
from .errors import CapabilityError
from .models import ExecutionSlot, ToolOperation


def _refused(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_live_claim(db: Session, claim: Claim, *, lock: bool = True) -> Run:
    """Return the running record only while its worker, fence and lease match."""
    run_query = db.query(Run).filter(Run.id == claim.run_id)
    job_query = db.query(RunJob).filter(RunJob.run_id == claim.run_id)
    if lock:
        run_query = run_query.with_for_update()
    run = run_query.first()
    if lock:
        job_query = job_query.with_for_update()
    job = job_query.order_by(RunJob.id).first()
    now = datetime.now(UTC)
    if (
        job is None
        or run is None
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
        or job.lease_until is None
        or _utc(job.lease_until) <= now
    ):
        raise _refused(409, "lease_lost", "This worker no longer owns the active run")
    return run


def _input_refs(inputs: tuple[ArtifactRef, ...]) -> tuple[dict, ...]:
    try:
        return tuple(item.model_dump(mode="json") for item in inputs)
    except AttributeError as exc:
        raise _refused(422, "invalid_tool_inputs", "Tool inputs are invalid") from exc


def admit_operation(
    db: Session,
    claim: Claim,
    context: AuthorizationContext,
    logical_call_id: str,
    ref: CapabilityRef,
    arguments: dict,
    inputs: tuple[ArtifactRef, ...],
) -> ToolOperation:
    """Admit or replay one logical operation without committing the caller's transaction."""
    if not logical_call_id or len(logical_call_id) > 128:
        raise _refused(422, "invalid_logical_call_id", "Logical tool call ID is invalid")
    run_identity = db.query(Run.conversation_id).filter(Run.id == claim.run_id).first()
    if run_identity is None:
        raise _refused(409, "lease_lost", "This worker no longer owns the active run")
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == run_identity.conversation_id)
        .with_for_update()
        .first()
    )
    if conversation is None:
        raise _refused(409, "lease_lost", "This worker no longer owns the active run")
    run = require_live_claim(db, claim, lock=True)
    is_manual_tool = run.kind == "tool" and run.graph_version in (1, 2)
    is_chat_tool = run.kind == "chat" and run.graph_version in (2, 3)
    if not (is_manual_tool or is_chat_tool):
        raise _refused(
            409,
            "unsupported_execution",
            "This run does not support maintained tool execution",
        )
    try:
        pinned = decode_execution_config(run.execution_configuration or "{}")
    except (TypeError, ValueError):
        raise _refused(409, "changed", "The pinned tool execution settings are unavailable") from None
    if (
        pinned.graph_kind != run.kind
        or pinned.graph_version != run.graph_version
        or ref not in pinned.allowed_capabilities
    ):
        raise _refused(403, "unsupported_capability", "This tool is not authorized for this run")
    if (
        conversation is None
        or context.principal_id != conversation.user_id
        or context.project_id != run.project_id
        or conversation.project_id != run.project_id
        or context.conversation_id != run.conversation_id
        or context.action != "execute_tool"
        or context.target_id != str(run.id)
    ):
        raise _refused(404, "operation_not_found", "The requested operation is not available")

    effective_inputs = list(inputs)
    try:
        candidate_definition = get_tool_definition_by_id(ref.id)
        candidate_arguments = candidate_definition.validate_arguments(arguments)
        referenced_artifacts = candidate_definition.input_artifact_ids(candidate_arguments)
    except (LookupError, TypeError, ValueError):
        referenced_artifacts = ()
    for referenced_artifact_id in referenced_artifacts:
        try:
            already_selected = any(
                item.id == referenced_artifact_id for item in effective_inputs
            )
        except AttributeError:
            already_selected = False
        if not already_selected:
            target = (
                db.query(Artifact)
                .filter(
                    Artifact.id == referenced_artifact_id,
                    Artifact.conversation_id == run.conversation_id,
                    Artifact.project_id == run.project_id,
                )
                .first()
            )
            if target is not None:
                effective_inputs.append(
                    ArtifactRef(
                        id=target.id,
                        sha256=target.sha256,
                        size_bytes=target.size_bytes,
                        media_type=target.media_type,
                    )
                )
    input_refs = _input_refs(tuple(effective_inputs))
    try:
        raw_digest = canonical_digest(
            {
                "capability": ref.model_dump(mode="json"),
                "arguments": arguments,
                "inputs": input_refs,
            }
        )
    except (TypeError, ValueError) as exc:
        raise _refused(422, "invalid_tool_arguments", "Tool arguments are invalid") from exc

    existing = (
        db.query(ToolOperation)
        .filter(
            ToolOperation.run_id == run.id,
            ToolOperation.logical_call_id == logical_call_id,
        )
        .with_for_update()
        .first()
    )
    if existing is not None:
        if existing.request_digest != raw_digest:
            raise _refused(409, "tool_operation_conflict", "Logical tool call already has different input")
        return existing

    try:
        policy = require_current_tool_policy(
            db,
            frozenset({ref.id}),
            pinned_revision=run.tool_policy_revision,
            lock=True,
        )
        manifest = resolve_capability(ref, policy.effective_ids)
        definition = get_tool_definition(manifest)
        normalized = definition.validate_arguments(arguments).model_dump(mode="json")
    except ToolPolicyError as refused:
        if refused.code == "tool_policy_unavailable":
            raise _refused(
                503, refused.code, "Managed tool availability is unavailable"
            ) from None
        raise _refused(
            422, "disabled", "The requested tool is disabled for this installation"
        ) from None
    except CapabilityError as refused:
        raise _refused(422, refused.code, str(refused)) from refused
    except LookupError as refused:
        raise _refused(422, "unsupported_tool", "This tool is not supported by this execution version") from refused
    except ValueError as invalid:
        raise _refused(422, "invalid_tool_arguments", "Tool arguments are invalid") from invalid

    for item in effective_inputs:
        artifact = db.query(Artifact).filter(Artifact.id == item.id).first()
        if (
            artifact is None
            or artifact.conversation_id != run.conversation_id
            or artifact.project_id != run.project_id
            or artifact.status != "ready"
            or artifact.sha256 != item.sha256
            or artifact.size_bytes != item.size_bytes
            or artifact.media_type != item.media_type
        ):
            raise _refused(409, "tool_input_unavailable", "A selected tool input is no longer available")

    arguments_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    bundle_digest = canonical_digest(
        {
            "run_id": str(run.id),
            "graph_version": run.graph_version,
            "capability": ref.model_dump(mode="json"),
            "arguments": normalized,
            "inputs": input_refs,
            "tool_policy_revision": policy.revision,
            "execution_configuration_digest": canonical_digest(
                json.loads(run.execution_configuration) if run.execution_configuration else {}
            ),
        }
    )
    operation = ToolOperation(
        run_id=run.id,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        principal_id=context.principal_id,
        logical_call_id=logical_call_id,
        capability_id=manifest.id,
        capability_version=manifest.version,
        manifest_digest=manifest.manifest_digest,
        arguments_json=arguments_json,
        arguments_digest=canonical_digest(normalized),
        request_digest=raw_digest,
        input_artifact_refs=json.dumps(input_refs, sort_keys=True, separators=(",", ":")),
        credential_binding_ref=getattr(manifest, "credential_binding_ref", None),
        configuration_revision=manifest.manifest_digest,
        execution_bundle_digest=bundle_digest,
        tool_policy_revision=policy.revision,
        status="admitted",
    )
    db.add(operation)
    db.flush()
    return operation


def _seed_slot_rows(db: Session, capacity: int) -> None:
    rows = [{"id": uuid.uuid4(), "slot_number": number} for number in range(capacity)]
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(
            pg_insert(ExecutionSlot)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["slot_number"])
        )
    elif dialect == "sqlite":
        db.execute(
            sqlite_insert(ExecutionSlot)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["slot_number"])
        )
    else:
        for row in rows:
            if not db.query(ExecutionSlot.id).filter(ExecutionSlot.slot_number == row["slot_number"]).first():
                db.add(ExecutionSlot(**row))
        db.flush()


def acquire_execution_slot(
    db: Session,
    claim: Claim,
    operation_id: uuid.UUID,
) -> ExecutionSlot | None:
    """Acquire a shared slot or return ``None`` when all configured slots are leased."""
    run = require_live_claim(db, claim, lock=True)
    operation = db.query(ToolOperation).filter(ToolOperation.id == operation_id).with_for_update().first()
    if operation is None or operation.run_id != run.id or operation.status not in ("admitted", "dispatching"):
        raise _refused(409, "tool_operation_unavailable", "The tool operation is not available for dispatch")
    now = datetime.now(UTC)
    existing = db.query(ExecutionSlot).filter(
        ExecutionSlot.operation_id == operation.id,
        ExecutionSlot.run_id == run.id,
        ExecutionSlot.fence == claim.fence,
        ExecutionSlot.owner_id == claim.worker_id,
        ExecutionSlot.lease_until > now,
    ).with_for_update().first()
    if existing is not None:
        return existing

    capacity = max(1, min(int(settings.tool_concurrency), 128))
    _seed_slot_rows(db, capacity)
    slot = (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.slot_number < capacity,
            or_(
                ExecutionSlot.operation_id.is_(None),
                ExecutionSlot.lease_until <= now,
            ),
        )
        .order_by(ExecutionSlot.slot_number)
        .with_for_update(skip_locked=True)
        .first()
    )
    if slot is None:
        return None
    job = db.query(RunJob).filter(RunJob.run_id == run.id).first()
    if job is None or job.lease_until is None:
        raise _refused(409, "lease_lost", "This worker no longer owns the active run")
    slot.operation_id = operation.id
    slot.run_id = run.id
    slot.fence = claim.fence
    slot.owner_id = claim.worker_id
    slot.lease_until = job.lease_until
    slot.updated_at = now
    db.flush()
    return slot


def release_execution_slot(
    db: Session,
    claim: Claim,
    slot_id: uuid.UUID,
) -> bool:
    """Release only the exact lease still held by this run fence and worker."""
    try:
        require_live_claim(db, claim, lock=False)
    except HTTPException:
        return False
    result = db.execute(
        update(ExecutionSlot)
        .where(
            ExecutionSlot.id == slot_id,
            ExecutionSlot.run_id == claim.run_id,
            ExecutionSlot.fence == claim.fence,
            ExecutionSlot.owner_id == claim.worker_id,
        )
        .values(
            operation_id=None,
            run_id=None,
            fence=None,
            owner_id=None,
            lease_until=None,
            updated_at=datetime.now(UTC),
        )
        .returning(ExecutionSlot.id)
        .execution_options(synchronize_session=False)
    )
    return result.first() is not None


def renew_execution_slot(
    db: Session,
    claim: Claim,
    slot_id: uuid.UUID,
) -> bool:
    """Extend one active slot only to the current live job lease."""
    try:
        require_live_claim(db, claim, lock=False)
    except HTTPException:
        return False
    job = db.query(RunJob).filter(RunJob.run_id == claim.run_id).first()
    if job is None or job.lease_until is None:
        return False
    now = datetime.now(UTC)
    result = db.execute(
        update(ExecutionSlot)
        .where(
            ExecutionSlot.id == slot_id,
            ExecutionSlot.run_id == claim.run_id,
            ExecutionSlot.fence == claim.fence,
            ExecutionSlot.owner_id == claim.worker_id,
            ExecutionSlot.lease_until > now,
        )
        .values(lease_until=job.lease_until, updated_at=now)
        .returning(ExecutionSlot.id)
        .execution_options(synchronize_session=False)
    )
    return result.first() is not None
