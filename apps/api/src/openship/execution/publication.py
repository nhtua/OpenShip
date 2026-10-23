"""Fenced, caller-transaction publication of immutable tool outputs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactRef, StagedArtifact
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import (
    mark_artifact_ready,
    release_artifact_reservation,
    reserve_artifact,
)
from src.openship.capabilities.contracts import (
    PublishedToolResult,
    ToolFailure,
    canonical_json,
)
from src.openship.capabilities.definitions import (
    ToolDefinitionEntry,
    get_tool_definition_by_id,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.events.service import Event
from src.openship.runs.models import Outbox
from src.openship.runs.queue import Claim
from src.openship.tools.search.contracts import (
    SearchArguments,
    SearchObservation,
    SearchToolResult,
)
from src.openship.tools.search.naming import search_artifact_filename
from src.openship.workspace.models import ConversationWorkspace
from src.openship.workspace.schemas import WorkspaceListResult, WorkspaceReadResult

from .admission import release_execution_slot, require_live_claim
from .models import ExecutionSlot, ToolAttempt, ToolOperation

_ERROR_MESSAGES = {
    "missing_credentials": "The selected search tool is missing its operator credential.",
    "connector_required": "Choose a search Connector before using this service.",
    "connector_ambiguous": "Choose one eligible search Connector explicitly.",
    "connector_scope_denied": "The selected search Connector is not available in this project.",
    "connector_expired": "The selected search Connector credential has expired.",
    "connector_revoked": "The selected search Connector has been revoked.",
    "connector_unavailable": "The selected search Connector is currently unavailable.",
    "credential_store_unavailable": "Search credential storage is unavailable.",
    "disabled": "The requested tool is disabled for this installation.",
    "changed": "The requested tool definition changed after this request was admitted.",
    "authentication_error": "The search service rejected the configured credential.",
    "rate_limited": "The search service is rate limiting this request.",
    "quota_exhausted": "The search service account has exhausted its configured quota.",
    "provider_timeout": "The search service did not respond within the allowed time.",
    "provider_unavailable": "The search service is temporarily unavailable.",
    "aws_unavailable": "The AWS service is temporarily unavailable.",
    "aws_account_mismatch": "The AWS role belongs to a different account than the Connector.",
    "aws_identity_mismatch": "The AWS role identity does not match the Connector.",
    "aws_region_mismatch": "The bound S3 bucket is in a different AWS region.",
    "aws_resource_unavailable": "The selected AWS resource is not available in this project.",
    "aws_invalid_metric_response": "AWS returned an invalid storage metrics response.",
    "aws_request_limit_exceeded": "The AWS request limit was exceeded.",
    "aws_output_limit_exceeded": "The AWS storage observation exceeded its size limit.",
    "invalid_response": "The search service returned an invalid response.",
    "output_limit_exceeded": "The search response exceeded the allowed size.",
    "cancelled": "The search was cancelled before it completed.",
    "capacity_timeout": "Tool capacity was not available before the execution deadline.",
    "artifact_storage_unavailable": "Conversation artifact storage is unavailable.",
    "artifact_publication_failed": "The search result could not be retained safely.",
    "artifact_quota_exceeded": "Conversation artifact storage quota is full.",
    "invalid_tool_output": "The maintained tool returned an invalid result.",
    "workspace_unavailable": "The conversation workspace is unavailable.",
    "artifact_not_found": "The requested file is not available in this conversation.",
    "invalid_cursor": "The workspace file cursor is invalid.",
    "invalid_offset": "The requested byte offset is invalid.",
    "invalid_utf8_boundary": "The requested byte offset is not on a UTF-8 boundary.",
    "artifact_unavailable": "The saved file could not be verified in storage.",
    "lease_lost": "The active run was interrupted before the result could be published.",
    "execution_interrupted": "The tool execution was interrupted and will not be repeated automatically.",
}


class ToolPublicationError(Exception):
    """A closed, safe publication failure."""

    def __init__(self, code: str):
        if code not in _ERROR_MESSAGES:
            raise ValueError("unknown tool publication error code")
        self.code = code
        self.safe_message = _ERROR_MESSAGES[code]
        super().__init__(self.safe_message)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event(
    db: Session,
    conversation: Conversation,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> Event:
    event_id = uuid.uuid4()
    sequence = conversation.next_event_sequence
    conversation.next_event_sequence = sequence + 1
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    event = Event(
        id=event_id,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        run_id=run_id,
        sequence=sequence,
        type=event_type,
        payload=payload_json,
        actor_id=None,
    )
    db.add(event)
    db.add(
        Outbox(
            conversation_id=conversation.id,
            event_id=event_id,
            type=event_type,
            payload=json.dumps(
                {"event_id": str(event_id), "type": event_type},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    return event


def _lock_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .with_for_update()
        .first()
    )
    if conversation is None:
        raise ToolPublicationError("lease_lost")
    return conversation


def _observation_bytes(observation: SearchObservation) -> bytes:
    try:
        return canonical_json(observation.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ToolPublicationError("invalid_tool_output") from exc


def _search_arguments(operation: ToolOperation) -> SearchArguments:
    try:
        return SearchArguments.model_validate_json(operation.arguments_json)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ToolPublicationError("invalid_tool_output") from exc


def validate_search_observation(
    operation: ToolOperation, observation
) -> SearchObservation:
    """Enforce the exact pinned capability/query contract at the dispatch boundary."""
    try:
        normalized = SearchObservation.model_validate(observation)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ToolPublicationError("invalid_tool_output") from exc
    arguments = _search_arguments(operation)
    if (
        normalized.tool.id != operation.capability_id
        or normalized.tool.version != operation.capability_version
        or normalized.tool.manifest_digest != operation.manifest_digest
        or normalized.query != arguments.query
        or normalized.requested_count != arguments.max_results
        or normalized.returned_count > arguments.max_results
        or normalized.observed_at.tzinfo is None
        or normalized.observed_at.utcoffset() is None
    ):
        raise ToolPublicationError("invalid_tool_output")
    return normalized


def build_model_result(
    operation: ToolOperation,
    observation: SearchObservation,
    artifact: ArtifactRef,
) -> SearchToolResult:
    """Drop the lowest-ranked tail until the model-facing result fits 16 KiB."""
    items = list(observation.items)
    truncated = observation.truncated
    while True:
        try:
            return SearchToolResult(
                operation_id=str(operation.id),
                tool=observation.tool,
                observed_at=observation.observed_at,
                query=observation.query,
                items=tuple(items),
                truncated=truncated,
                artifact=artifact,
            )
        except ValidationError as exc:
            too_large = any(
                "model-facing search result exceeds the byte limit" in error["msg"]
                for error in exc.errors()
            )
            if not too_large:
                raise ToolPublicationError("invalid_tool_output") from exc
            if not items:
                raise ToolPublicationError("output_limit_exceeded") from None
            items.pop()
            truncated = True


def _attempt_and_operation(
    db: Session,
    claim: Claim,
    attempt_id: uuid.UUID,
) -> tuple[Conversation, ToolOperation, ToolAttempt]:
    attempt_locator = db.query(ToolAttempt).filter(ToolAttempt.id == attempt_id).first()
    if attempt_locator is None:
        raise ToolPublicationError("execution_interrupted")
    operation_locator = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == attempt_locator.operation_id)
        .first()
    )
    if operation_locator is None or operation_locator.run_id != claim.run_id:
        raise ToolPublicationError("execution_interrupted")
    conversation = _lock_conversation(db, operation_locator.conversation_id)
    require_live_claim(db, claim, lock=True)
    operation = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == operation_locator.id)
        .with_for_update()
        .first()
    )
    attempt = (
        db.query(ToolAttempt)
        .filter(ToolAttempt.id == attempt_id)
        .with_for_update()
        .first()
    )
    if (
        operation is None
        or attempt is None
        or attempt.operation_id != operation.id
        or attempt.fence != claim.fence
        or operation.run_id != claim.run_id
    ):
        raise ToolPublicationError("lease_lost")
    return conversation, operation, attempt


def mark_tool_running(
    db: Session, claim: Claim, attempt_id: uuid.UUID
) -> ToolOperation:
    """Commit the running transition and its event before the external call."""
    _attempt_locator = (
        db.query(ToolAttempt).filter(ToolAttempt.id == attempt_id).first()
    )
    if _attempt_locator is None:
        raise ToolPublicationError("execution_interrupted")
    _operation_locator = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == _attempt_locator.operation_id)
        .first()
    )
    if _operation_locator is None:
        raise ToolPublicationError("execution_interrupted")
    conversation = _lock_conversation(db, _operation_locator.conversation_id)
    require_live_claim(db, claim, lock=True)
    operation = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == _operation_locator.id)
        .with_for_update()
        .first()
    )
    attempt = (
        db.query(ToolAttempt)
        .filter(ToolAttempt.id == attempt_id)
        .with_for_update()
        .first()
    )
    if (
        operation is None
        or attempt is None
        or operation.status != "dispatching"
        or attempt.outcome != "started"
        or attempt.fence != claim.fence
    ):
        raise ToolPublicationError("lease_lost")
    operation.status = "running"
    operation.updated_at = _utc_now()
    _event(
        db,
        conversation,
        operation.run_id,
        "tool.started",
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "attempt_id": str(attempt.id),
            "capability_id": operation.capability_id,
            "status": "running",
        },
    )
    db.flush()
    return operation


def mark_tool_dispatching(
    db: Session,
    claim: Claim,
    operation_id: uuid.UUID,
    slot_id: uuid.UUID,
    max_output_bytes: int,
    *,
    reserve_output: bool = True,
    artifact_kind: str = "search_result",
    artifact_name: str | None = None,
) -> tuple[ToolOperation, ToolAttempt, Artifact | None]:
    """Reserve immutable output and commit one physical dispatch intent."""
    locator = db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
    if locator is None or locator.run_id != claim.run_id:
        raise ToolPublicationError("execution_interrupted")
    conversation = _lock_conversation(db, locator.conversation_id)
    require_live_claim(db, claim, lock=True)
    operation = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == operation_id)
        .with_for_update()
        .first()
    )
    slot = (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.id == slot_id,
            ExecutionSlot.operation_id == operation_id,
            ExecutionSlot.run_id == claim.run_id,
            ExecutionSlot.fence == claim.fence,
            ExecutionSlot.owner_id == claim.worker_id,
        )
        .with_for_update()
        .first()
    )
    if operation is None or operation.status != "admitted" or slot is None:
        raise ToolPublicationError("lease_lost")

    provenance = json.dumps(
        {
            "capability_id": operation.capability_id,
            "capability_version": operation.capability_version,
            "manifest_digest": operation.manifest_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact = None
    if reserve_output:
        if artifact_kind == "search_result":
            arguments = _search_arguments(operation)
            display_name = search_artifact_filename(arguments.query)
            maximum = settings.artifact_search_reservation_bytes
        elif artifact_kind == "cloud_observation" and artifact_name:
            display_name = artifact_name
            maximum = settings.artifact_cloud_observation_reservation_bytes
        else:
            raise ToolPublicationError("invalid_tool_output")
        artifact = reserve_artifact(
            db,
            conversation_id=operation.conversation_id,
            project_id=operation.project_id,
            max_bytes=min(
                max(1, max_output_bytes),
                maximum,
                64 * 1024,
            ),
            kind=artifact_kind,
            request_key=str(operation.id),
            display_name=display_name,
            media_type="application/json",
            source="tool",
            originating_run_id=operation.run_id,
            originating_operation_id=operation.id,
            provenance=provenance,
        )
    attempt = ToolAttempt(
        operation_id=operation.id,
        ordinal=1,
        fence=claim.fence,
        dispatch_started_at=_utc_now(),
        request_digest=operation.arguments_digest,
        outcome="started",
    )
    operation.status = "dispatching"
    operation.updated_at = _utc_now()
    db.add(attempt)
    _event(
        db,
        conversation,
        operation.run_id,
        "tool.requested",
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "capability_id": operation.capability_id,
            "status": "dispatching",
        },
    )
    db.flush()
    return operation, attempt, artifact


def mark_tool_failed(
    db: Session,
    claim: Claim,
    operation_id: uuid.UUID,
    code: str,
    *,
    attempt_id: uuid.UUID | None = None,
    status: str = "failed",
) -> PublishedToolResult:
    """Record a fixed failure and release reserved output quota in this transaction."""
    if code not in _ERROR_MESSAGES or status not in ("failed", "cancelled"):
        raise ValueError("failure outcome is not in the closed execution vocabulary")
    locator = db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
    if locator is None or locator.run_id != claim.run_id:
        raise ToolPublicationError("execution_interrupted")
    conversation = _lock_conversation(db, locator.conversation_id)
    require_live_claim(db, claim, lock=True)
    operation = (
        db.query(ToolOperation)
        .filter(ToolOperation.id == operation_id)
        .with_for_update()
        .first()
    )
    if operation is None:
        raise ToolPublicationError("execution_interrupted")
    if operation.status in ("succeeded", "failed", "cancelled", "interrupted"):
        return project_published_result(db, operation)
    if operation.status not in ("admitted", "dispatching", "running"):
        raise ToolPublicationError("execution_interrupted")

    attempt = None
    if attempt_id is not None:
        attempt = (
            db.query(ToolAttempt)
            .filter(
                ToolAttempt.id == attempt_id,
                ToolAttempt.operation_id == operation.id,
            )
            .with_for_update()
            .first()
        )
        if attempt is None or attempt.fence != claim.fence:
            raise ToolPublicationError("lease_lost")
        if attempt.outcome == "started":
            attempt.outcome = "cancelled" if status == "cancelled" else "failed"
            attempt.error_code = code
            attempt.finished_at = _utc_now()

    for slot in (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.operation_id == operation.id,
            ExecutionSlot.run_id == claim.run_id,
            ExecutionSlot.fence == claim.fence,
            ExecutionSlot.owner_id == claim.worker_id,
        )
        .with_for_update()
        .all()
    ):
        if not release_execution_slot(db, claim, slot.id):
            raise ToolPublicationError("lease_lost")
    output_rows = (
        db.query(Artifact.id)
        .filter(
            Artifact.originating_operation_id == operation.id,
            Artifact.status == "staging",
        )
        .all()
    )
    for (artifact_id,) in output_rows:
        release_artifact_reservation(db, artifact_id)

    operation.status = status
    operation.error_code = code
    operation.result_json = None
    operation.result_digest = None
    operation.output_artifact_ids = "[]"
    operation.finished_at = _utc_now()
    operation.updated_at = operation.finished_at
    event_type = "tool.cancelled" if status == "cancelled" else "tool.failed"
    _event(
        db,
        conversation,
        operation.run_id,
        event_type,
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "attempt_id": str(attempt.id) if attempt else None,
            "capability_id": operation.capability_id,
            "status": status,
            "error_code": code,
        },
    )
    db.flush()
    return PublishedToolResult(
        operation_id=operation.id,
        status=status,
        error=ToolFailure(code=code, message=_ERROR_MESSAGES[code]),
    )


def project_published_result(
    db: Session, operation: ToolOperation
) -> PublishedToolResult:
    """Read a completed operation without ever re-dispatching its external effect."""
    if operation.status == "succeeded":
        try:
            result = get_tool_definition_by_id(
                operation.capability_id
            ).validate_public_result(json.loads(operation.result_json))
            artifact_ids = json.loads(operation.output_artifact_ids or "[]")
        except (LookupError, TypeError, ValueError, json.JSONDecodeError):
            return PublishedToolResult(
                operation_id=operation.id,
                status="interrupted",
                error=ToolFailure(
                    code="execution_interrupted",
                    message=_ERROR_MESSAGES["execution_interrupted"],
                ),
            )
        artifacts = []
        for artifact_id in artifact_ids:
            try:
                parsed_id = uuid.UUID(artifact_id)
            except (TypeError, ValueError):
                continue
            artifact = (
                db.query(Artifact)
                .filter(
                    Artifact.id == parsed_id,
                    Artifact.status == "ready",
                )
                .first()
            )
            if artifact is not None:
                artifacts.append(
                    ArtifactRef(
                        id=artifact.id,
                        sha256=artifact.sha256,
                        size_bytes=artifact.size_bytes,
                        media_type=artifact.media_type,
                    )
                )
        return PublishedToolResult(
            operation_id=operation.id,
            status="succeeded",
            result=result,
            artifacts=tuple(artifacts),
        )
    if operation.status in ("failed", "cancelled", "interrupted"):
        code = (
            operation.error_code
            if operation.error_code in _ERROR_MESSAGES
            else "execution_interrupted"
        )
        return PublishedToolResult(
            operation_id=operation.id,
            status=operation.status,
            error=ToolFailure(code=code, message=_ERROR_MESSAGES[code]),
        )
    raise HTTPException(
        status_code=409,
        detail={
            "error": {
                "code": "tool_operation_in_progress",
                "message": "The logical tool call is already admitted and cannot be dispatched again.",
            }
        },
    )


def publish_tool_result(
    db: Session,
    claim: Claim,
    attempt_id: uuid.UUID,
    observation,
    staged_outputs: tuple[StagedArtifact, ...],
    definition: ToolDefinitionEntry | None = None,
) -> PublishedToolResult:
    """Atomically expose artifact readiness, operation output and completion events."""
    conversation, operation, attempt = _attempt_and_operation(db, claim, attempt_id)
    if operation.status != "running" or attempt.outcome != "started":
        raise ToolPublicationError("lease_lost")
    generic_publication = (
        definition is not None and definition.external_publication_hook is not None
    )
    normalized = (
        validate_search_observation(operation, observation)
        if definition is None
        else definition.validate_handler_result(operation, observation)
    )
    evidence = (
        normalized if definition is None else definition.project_evidence(normalized)
    )
    if hasattr(evidence, "model_dump"):
        evidence = evidence.model_dump(mode="json")
    snapshot = (
        _observation_bytes(normalized)
        if not generic_publication
        else canonical_json(evidence)
    )
    if len(snapshot) > 64 * 1024:
        raise ToolPublicationError("output_limit_exceeded")
    if len(staged_outputs) != 1:
        raise ToolPublicationError("invalid_tool_output")
    staged = staged_outputs[0]
    if staged.observed.sha256 != hashlib.sha256(
        snapshot
    ).hexdigest() or staged.observed.size_bytes != len(snapshot):
        raise ToolPublicationError("invalid_tool_output")
    slot_id = _slot_id_for_operation(db, operation.id, claim)

    artifact = (
        db.query(Artifact)
        .filter(Artifact.id == staged.artifact_id)
        .with_for_update()
        .first()
    )
    if (
        artifact is None
        or artifact.status != "staging"
        or artifact.originating_run_id != operation.run_id
        or artifact.originating_operation_id != operation.id
        or artifact.conversation_id != operation.conversation_id
        or artifact.project_id != operation.project_id
        or artifact.media_type != "application/json"
        or artifact.kind
        != ("search_result" if not generic_publication else definition.artifact_kind)
        or artifact.display_name
        != (
            search_artifact_filename(normalized.query)
            if not generic_publication
            else definition.artifact_name
        )
    ):
        raise ToolPublicationError("artifact_publication_failed")
    ready = mark_artifact_ready(db, artifact.id, staged.observed)
    artifact_ref = ArtifactRef(
        id=ready.id,
        sha256=ready.sha256,
        size_bytes=ready.size_bytes,
        media_type=ready.media_type,
    )
    if not generic_publication:
        result_model = build_model_result(operation, normalized, artifact_ref)
        result_data = result_model.model_dump(mode="json")
    else:
        if definition.external_publication_hook is None:
            raise ToolPublicationError("invalid_tool_output")
        published_value = definition.external_publication_hook(
            db, operation, normalized, artifact_ref
        )
        result_data = definition.project_result(published_value)
    result_bytes = canonical_json(result_data)
    if len(result_bytes) > int(
        (definition.manifest.limits if definition is not None else {}).get(
            "max_model_result_bytes", 16 * 1024
        )
    ):
        raise ToolPublicationError("output_limit_exceeded")
    now = _utc_now()
    operation.status = "succeeded"
    operation.error_code = None
    operation.result_json = result_bytes.decode("utf-8")
    operation.result_digest = hashlib.sha256(result_bytes).hexdigest()
    operation.output_artifact_ids = json.dumps([str(ready.id)], separators=(",", ":"))
    operation.finished_at = now
    operation.updated_at = now
    attempt.outcome = "succeeded"
    attempt.error_code = None
    attempt.response_digest = hashlib.sha256(snapshot).hexdigest()
    attempt.finished_at = now
    if not release_execution_slot(db, claim, slot_id):
        raise ToolPublicationError("lease_lost")
    _event(
        db,
        conversation,
        operation.run_id,
        "artifact.created",
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "artifact_id": str(ready.id),
            "sha256": ready.sha256,
            "size_bytes": ready.size_bytes,
            "media_type": ready.media_type,
            "name": ready.display_name,
        },
    )
    if generic_publication:
        from src.openship.resources.models import ResourceObservation

        db.flush()
        resource_observation = (
            db.query(ResourceObservation)
            .filter(
                ResourceObservation.operation_id == operation.id,
                ResourceObservation.project_id == operation.project_id,
                ResourceObservation.conversation_id == operation.conversation_id,
            )
            .one_or_none()
        )
        if resource_observation is None:
            raise ToolPublicationError("invalid_tool_output")
        _event(
            db,
            conversation,
            operation.run_id,
            "resource.observed",
            {
                "schema_version": 1,
                "observation_id": str(resource_observation.id),
                "resource_id": str(resource_observation.resource_object_id),
                "binding_id": str(resource_observation.resource_binding_id),
                "operation_id": str(operation.id),
                "outcome": resource_observation.outcome,
                "retrieved_at": resource_observation.retrieved_at.isoformat(),
                "artifact_id": str(ready.id),
            },
        )
    _event(
        db,
        conversation,
        operation.run_id,
        "tool.completed",
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "attempt_id": str(attempt.id),
            "capability_id": operation.capability_id,
            "status": "succeeded",
            "artifact_ids": [str(ready.id)],
            "result_digest": operation.result_digest,
        },
    )
    db.flush()
    return PublishedToolResult(
        operation_id=operation.id,
        status="succeeded",
        result=result_data,
        artifacts=(artifact_ref,),
    )


def publish_local_tool_result(
    db: Session,
    claim: Claim,
    attempt_id: uuid.UUID,
    result,
    definition: ToolDefinitionEntry,
) -> PublishedToolResult:
    """Persist one bounded local result through its registered projection hook."""
    conversation, operation, attempt = _attempt_and_operation(db, claim, attempt_id)
    if operation.status != "running" or attempt.outcome != "started":
        raise ToolPublicationError("lease_lost")
    try:
        validated = (
            definition.publication_hook(db, operation, result)
            if definition.publication_hook is not None
            else result
        )
        result_data = definition.project_result(validated)
        result_bytes = canonical_json(result_data)
        max_result_bytes = int(
            definition.manifest.limits.get("max_model_result_bytes", 16 * 1024)
        )
        if len(result_bytes) > max_result_bytes:
            raise ToolPublicationError("output_limit_exceeded")
    except ToolPublicationError:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise ToolPublicationError("invalid_tool_output") from exc

    slot_id = _slot_id_for_operation(db, operation.id, claim)
    now = _utc_now()
    operation.status = "succeeded"
    operation.error_code = None
    operation.result_json = result_bytes.decode("utf-8")
    operation.result_digest = hashlib.sha256(result_bytes).hexdigest()
    operation.output_artifact_ids = "[]"
    operation.finished_at = now
    operation.updated_at = now
    attempt.outcome = "succeeded"
    attempt.error_code = None
    attempt.response_digest = operation.result_digest
    attempt.finished_at = now
    if not release_execution_slot(db, claim, slot_id):
        raise ToolPublicationError("lease_lost")
    _event(
        db,
        conversation,
        operation.run_id,
        "tool.completed",
        {
            "schema_version": 1,
            "operation_id": str(operation.id),
            "attempt_id": str(attempt.id),
            "capability_id": operation.capability_id,
            "status": "succeeded",
            "artifact_ids": [],
            "result_digest": operation.result_digest,
        },
    )
    db.flush()
    return PublishedToolResult(
        operation_id=operation.id,
        status="succeeded",
        result=result_data,
    )


def validate_workspace_result(db: Session, operation: ToolOperation, result):
    """Check workspace results still point to artifacts in the active conversation."""
    try:
        if operation.capability_id == "workspace_list_files":
            normalized = WorkspaceListResult.model_validate(result)
            expected_workspace = (
                db.query(ConversationWorkspace)
                .filter(
                    ConversationWorkspace.conversation_id == operation.conversation_id
                )
                .first()
            )
            if (
                expected_workspace is None
                or normalized.workspace_id != expected_workspace.id
                or any(
                    db.query(Artifact.id)
                    .filter(
                        Artifact.id == item.id,
                        Artifact.conversation_id == operation.conversation_id,
                        Artifact.project_id == operation.project_id,
                        Artifact.status == "ready",
                        Artifact.sha256 == item.sha256,
                        Artifact.size_bytes == item.size_bytes,
                    )
                    .first()
                    is None
                    for item in normalized.items
                )
            ):
                raise ValueError("workspace list result changed its pinned scope")
            return normalized
        if operation.capability_id == "workspace_read_file":
            normalized = WorkspaceReadResult.model_validate(result)
            expected_workspace = (
                db.query(ConversationWorkspace)
                .filter(
                    ConversationWorkspace.conversation_id == operation.conversation_id
                )
                .first()
            )
            artifact = (
                db.query(Artifact)
                .filter(
                    Artifact.id == normalized.artifact_id,
                    Artifact.conversation_id == operation.conversation_id,
                    Artifact.project_id == operation.project_id,
                    Artifact.status == "ready",
                )
                .first()
            )
            if (
                expected_workspace is None
                or normalized.workspace_id != expected_workspace.id
                or normalized.version_id != normalized.artifact_id
                or artifact is None
                or normalized.sha256 != artifact.sha256
                or normalized.size_bytes != artifact.size_bytes
                or len(normalized.content.encode("utf-8")) > 16 * 1024
            ):
                raise ValueError("workspace read result changed its pinned scope")
            return normalized
        raise ValueError("workspace publication hook received an unrelated operation")
    except (ValidationError, TypeError, ValueError) as exc:
        raise ToolPublicationError("invalid_tool_output") from exc


def _slot_id_for_operation(
    db: Session, operation_id: uuid.UUID, claim: Claim
) -> uuid.UUID:
    slot = (
        db.query(ExecutionSlot)
        .filter(
            ExecutionSlot.operation_id == operation_id,
            ExecutionSlot.run_id == claim.run_id,
            ExecutionSlot.fence == claim.fence,
            ExecutionSlot.owner_id == claim.worker_id,
        )
        .with_for_update()
        .first()
    )
    if slot is None:
        raise ToolPublicationError("lease_lost")
    return slot.id
