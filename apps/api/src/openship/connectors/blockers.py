"""Transactional pre-dispatch Connector waits and exact resume commands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..capabilities.contracts import canonical_digest, decode_execution_config
from ..chat.lifecycle import ConversationLifecycleError, get_owned_conversation_scope
from ..chat.models import Conversation
from ..connectors.aws.broker import AwsRoleBroker
from ..connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from ..events.service import append_event
from ..execution.models import ToolOperation
from ..resources.models import ConnectorBlocker, ResourceBinding
from ..runs.models import ProviderAttempt, Run, RunJob
from .readiness import ConnectorReadinessFailure, preflight_connector_references

_WAIT_LIMIT = timedelta(hours=24)


def configuration_digest(execution) -> str:
    """Digest the exact secret-free references and limits admitted to a run."""
    return canonical_digest(execution.model_dump(mode="json"))


def park_connector_wait(
    db: Session,
    *,
    claim,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    project_id: uuid.UUID,
    execution,
    failure: ConnectorReadinessFailure,
    now: datetime | None = None,
) -> bool:
    """Atomically park a still-owned run before any provider dispatch intent."""
    now = _utc(now or datetime.now(UTC))
    if failure.reference not in getattr(execution, "connector_refs", ()):
        return False
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    run = (
        db.query(Run)
        .filter(Run.id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if (
        conversation is None
        or run is None
        or job is None
        or conversation.project_id != project_id
        or run.conversation_id != conversation_id
        or run.project_id != project_id
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
        or run.provider_started_at is not None
        or db.query(ProviderAttempt).filter(ProviderAttempt.run_id == run_id).first()
        is not None
        or db.query(ToolOperation).filter(ToolOperation.run_id == run_id).first()
        is not None
    ):
        db.rollback()
        return False

    reference = failure.reference
    deadline = _utc(run.execution_deadline_at) if run.execution_deadline_at else None
    expires_at = (
        min(now + _WAIT_LIMIT, deadline) if deadline is not None else now + _WAIT_LIMIT
    )
    if expires_at <= now:
        run.status = "timed_out"
        run.error_code = "budget_exceeded"
        run.ended_at = now
        run.fence += 1
        job.fence = run.fence
        job.owner_id = None
        job.lease_until = None
        _append_event(
            db,
            conversation,
            run,
            "run.failed",
            {"schema_version": 1, "error_code": "budget_exceeded"},
        )
        db.commit()
        return True

    blocker = ConnectorBlocker(
        id=uuid.uuid4(),
        project_id=project_id,
        conversation_id=conversation_id,
        run_id=run_id,
        connector_kind=reference.kind,
        connector_visibility=reference.visibility,
        connector_project_id=(
            project_id if reference.visibility == "project" else None
        ),
        connector_id=reference.id,
        connector_revision=reference.configuration_revision,
        credential_version_id=reference.credential_version_id,
        configuration_digest=configuration_digest(execution),
        request_id=uuid.uuid4(),
        reason=failure.reason,
        state="waiting",
        expires_at=expires_at,
    )
    db.add(blocker)
    run.status = "waiting_connector"
    run.error_code = None
    run.ended_at = None
    run.fence += 1
    job.fence = run.fence
    job.owner_id = None
    job.lease_until = None
    _append_event(
        db,
        conversation,
        run,
        "connector.blocked",
        {
            "schema_version": 1,
            "blocker_id": str(blocker.id),
            "request_id": str(blocker.request_id),
            "reason": blocker.reason,
            "configuration_digest": blocker.configuration_digest,
            "expires_at": blocker.expires_at.isoformat(),
            "connector": {
                "id": str(reference.id),
                "visibility": reference.visibility,
                "revision": reference.configuration_revision,
                "kind": reference.kind,
            },
        },
    )
    db.commit()
    return True


def resume_connector_wait(
    db: Session,
    *,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    blocker_id: uuid.UUID,
    request_id: uuid.UUID,
    expected_configuration_digest: str,
    session_factory,
    now: datetime | None = None,
) -> Run:
    """Resume the same pre-dispatch run after validating its unchanged pins."""
    now = _utc(now or datetime.now(UTC))
    initial = (
        db.query(Run, Conversation, ConnectorBlocker)
        .join(Conversation, Conversation.id == Run.conversation_id)
        .join(ConnectorBlocker, ConnectorBlocker.run_id == Run.id)
        .filter(
            Run.id == run_id,
            Run.project_id == ConnectorBlocker.project_id,
            Run.conversation_id == ConnectorBlocker.conversation_id,
            Conversation.user_id == user_id,
            ConnectorBlocker.id == blocker_id,
        )
        .first()
    )
    if initial is None:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        )
    initial_run, _conversation, initial_blocker = initial
    _validate_resume_command(initial_blocker, request_id, expected_configuration_digest)
    if initial_blocker.state == "resumed":
        return initial_run
    if initial_blocker.state != "waiting" or initial_run.status != "waiting_connector":
        raise _resume_error(
            409, "connector_wait_closed", "This Connector wait is no longer active"
        )
    if _utc(initial_blocker.expires_at) <= now:
        expire_connector_blockers(db, now=now)
        db.refresh(initial_run)
        return initial_run

    run_project_id = initial_run.project_id
    run_conversation_id = initial_run.conversation_id
    execution_payload = initial_run.execution_configuration or ""
    try:
        execution = decode_execution_config(execution_payload)
    except (TypeError, ValueError):
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The original Connector settings cannot be resumed",
        ) from None
    current_digest = configuration_digest(execution)
    if current_digest != expected_configuration_digest:
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The original Connector settings have changed",
        )

    failure = preflight_connector_references(
        session_factory=session_factory,
        project_id=run_project_id,
        references=tuple(getattr(execution, "connector_refs", ())),
    )
    if failure is not None:
        raise _resume_error(
            409, failure.reason, "The pinned Connector is still unavailable"
        )

    db.rollback()
    try:
        conversation, project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=run_conversation_id,
            for_update=True,
        )
    except ConversationLifecycleError:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        ) from None
    from ..workspace.lifecycle import ArchiveStateError, assert_active_conversation

    try:
        assert_active_conversation(conversation, project)
    except ArchiveStateError:
        raise _resume_error(
            409,
            "conversation_archived",
            "Restore the conversation before resuming this run",
        ) from None

    run = (
        db.query(Run)
        .filter(Run.id == run_id, Run.project_id == project.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    blocker = (
        db.query(ConnectorBlocker)
        .filter(
            ConnectorBlocker.id == blocker_id,
            ConnectorBlocker.run_id == run_id,
            ConnectorBlocker.project_id == project.id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if run is None or blocker is None or job is None:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        )
    _validate_resume_command(blocker, request_id, expected_configuration_digest)
    if blocker.state == "resumed":
        return run
    if blocker.state != "waiting" or run.status != "waiting_connector":
        raise _resume_error(
            409, "connector_wait_closed", "This Connector wait is no longer active"
        )
    if _utc(blocker.expires_at) <= now or (
        run.execution_deadline_at is not None and _utc(run.execution_deadline_at) <= now
    ):
        status = (
            "timed_out"
            if run.execution_deadline_at is not None
            and _utc(run.execution_deadline_at) <= now
            else "failed"
        )
        code = "budget_exceeded" if status == "timed_out" else "connector_wait_expired"
        blocker.state = "expired"
        blocker.closed_at = now
        _close_run(db, run, status, code, now)
        _append_event(
            db,
            conversation,
            run,
            "connector.expired",
            {"schema_version": 1, "blocker_id": str(blocker.id), "error_code": code},
        )
        db.commit()
        db.refresh(run)
        return run
    if (
        configuration_digest(decode_execution_config(run.execution_configuration or ""))
        != expected_configuration_digest
    ):
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The original Connector settings have changed",
        )
    if not _validate_references_in_session(db, project.id, execution):
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The pinned Connector settings have changed",
        )
    if not _validate_resource_bindings_in_session(db, project.id, execution):
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The pinned AWS target settings have changed",
        )
    active = (
        db.query(Run)
        .filter(
            Run.conversation_id == conversation.id,
            Run.id != run.id,
            Run.status.in_(("created", "queued", "running")),
        )
        .first()
    )
    if active is not None:
        raise _resume_error(
            409, "run_already_active", "Another run is active on this conversation"
        )

    blocker.state = "resumed"
    blocker.consumed_at = now
    blocker.closed_at = now
    run.status = "queued"
    run.error_code = None
    run.ended_at = None
    job.owner_id = None
    job.lease_until = None
    job.available_at = now
    job.fence = run.fence
    job.dispatch_generation += 1
    from ..messaging.models import MessageTransportState
    from ..messaging.outbox import enqueue_run

    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)
    _append_event(
        db,
        conversation,
        run,
        "connector.resumed",
        {
            "schema_version": 1,
            "blocker_id": str(blocker.id),
            "request_id": str(blocker.request_id),
            "configuration_digest": blocker.configuration_digest,
        },
        actor_id=user_id,
    )
    db.commit()
    db.refresh(run)
    return run


def dismiss_connector_wait(
    db: Session, *, user_id: uuid.UUID, run_id: uuid.UUID, blocker_id: uuid.UUID
) -> Run:
    """Close a waiting run when its owner chooses not to resume it."""
    run, conversation, blocker = _owned_wait(db, user_id, run_id, blocker_id, lock=True)
    if blocker.state == "dismissed":
        return run
    if blocker.state != "waiting" or run.status != "waiting_connector":
        raise _resume_error(
            409, "connector_wait_closed", "This Connector wait is no longer active"
        )
    now = datetime.now(UTC)
    blocker.state = "dismissed"
    blocker.closed_at = now
    _close_run(db, run, "cancelled", "connector_dismissed", now)
    _append_event(
        db,
        conversation,
        run,
        "connector.dismissed",
        {"schema_version": 1, "blocker_id": str(blocker.id)},
        actor_id=user_id,
    )
    db.commit()
    db.refresh(run)
    return run


def expire_connector_blockers(db: Session, *, now: datetime | None = None) -> int:
    """Expire bounded Connector waits from the worker's periodic reconciler."""
    now = _utc(now or datetime.now(UTC))
    blocker_ids = [
        item.id
        for item in (
            db.query(ConnectorBlocker.id)
            .filter(
                ConnectorBlocker.state == "waiting", ConnectorBlocker.expires_at <= now
            )
            .order_by(ConnectorBlocker.expires_at, ConnectorBlocker.id)
            .limit(100)
            .all()
        )
    ]
    expired = 0
    for blocker_id in blocker_ids:
        blocker_identity = (
            db.query(ConnectorBlocker.run_id, ConnectorBlocker.conversation_id)
            .filter(ConnectorBlocker.id == blocker_id)
            .first()
        )
        if blocker_identity is None:
            continue
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == blocker_identity.conversation_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        run = (
            db.query(Run)
            .filter(Run.id == blocker_identity.run_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        blocker = (
            db.query(ConnectorBlocker)
            .filter(ConnectorBlocker.id == blocker_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        job = (
            db.query(RunJob)
            .filter(RunJob.run_id == blocker_identity.run_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if (
            conversation is None
            or run is None
            or blocker is None
            or job is None
            or blocker.state != "waiting"
            or _utc(blocker.expires_at) > now
            or run.status != "waiting_connector"
        ):
            continue
        terminal_status = (
            "timed_out"
            if run.execution_deadline_at is not None
            and _utc(run.execution_deadline_at) <= now
            else "failed"
        )
        code = (
            "budget_exceeded"
            if terminal_status == "timed_out"
            else "connector_wait_expired"
        )
        blocker.state = "expired"
        blocker.closed_at = now
        _close_run(db, run, terminal_status, code, now)
        _append_event(
            db,
            conversation,
            run,
            "connector.expired",
            {"schema_version": 1, "blocker_id": str(blocker.id), "error_code": code},
        )
        expired += 1
    if expired:
        db.commit()
    elif blocker_ids:
        db.rollback()
    return expired


def waiting_blocker(db: Session, run_id: uuid.UUID) -> ConnectorBlocker | None:
    return (
        db.query(ConnectorBlocker)
        .filter(ConnectorBlocker.run_id == run_id, ConnectorBlocker.state == "waiting")
        .first()
    )


def _validate_references_in_session(
    db: Session, project_id: uuid.UUID, execution
) -> bool:
    aws_broker = AwsRoleBroker(lambda: db)
    search_broker = SearchCredentialBroker(lambda: db)
    try:
        for reference in getattr(execution, "connector_refs", ()):
            if reference.kind == "aws":
                aws_broker.validate_in_session(
                    db, project_id=project_id, reference=reference, lock=True
                )
            elif reference.kind in {"brave", "tavily"}:
                search_broker.validate_in_session(
                    db,
                    project_id=project_id,
                    kind=reference.kind,
                    reference=reference,
                    lock=True,
                )
            else:
                return False
    except ConnectorCredentialError:
        return False
    return True


def _validate_resource_bindings_in_session(
    db: Session, project_id: uuid.UUID, execution
) -> bool:
    """Recheck every exact target pin before consuming a wait."""
    connector_revisions = {
        reference.id: reference.configuration_revision
        for reference in getattr(execution, "connector_refs", ())
    }
    references = {}
    for reference in (
        *getattr(execution, "resource_binding_refs", ()),
        *getattr(execution, "selected_resource_binding_refs", ()),
    ):
        existing = references.get(reference.id)
        if existing is not None and existing != reference:
            return False
        references[reference.id] = reference

    for reference in references.values():
        if connector_revisions.get(reference.connector_id) != reference.connector_revision:
            return False
        binding = (
            db.query(ResourceBinding)
            .filter(
                ResourceBinding.id == reference.id,
                ResourceBinding.project_id == project_id,
                ResourceBinding.revision == reference.revision,
                ResourceBinding.connector_id == reference.connector_id,
                ResourceBinding.connector_revision == reference.connector_revision,
                ResourceBinding.state == "active",
            )
            .with_for_update()
            .first()
        )
        if binding is None:
            return False
    return True


def _owned_wait(db, user_id, run_id, blocker_id, *, lock):
    identity = (
        db.query(Run.conversation_id)
        .join(Conversation, Conversation.id == Run.conversation_id)
        .filter(Run.id == run_id, Conversation.user_id == user_id)
        .first()
    )
    if identity is None:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        )
    try:
        conversation, _project = get_owned_conversation_scope(
            db,
            owner_id=user_id,
            conversation_id=identity.conversation_id,
            for_update=lock,
        )
    except ConversationLifecycleError:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        ) from None
    run = (
        db.query(Run)
        .filter(Run.id == run_id, Run.conversation_id == conversation.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    blocker = (
        db.query(ConnectorBlocker)
        .filter(
            ConnectorBlocker.id == blocker_id,
            ConnectorBlocker.run_id == run_id,
            ConnectorBlocker.project_id == conversation.project_id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if run is None or blocker is None or job is None:
        raise _resume_error(
            404, "connector_blocker_not_found", "Connector wait not found"
        )
    return run, conversation, blocker


def _validate_resume_command(blocker, request_id, expected_digest):
    if blocker.request_id != request_id:
        raise _resume_error(
            409,
            "connector_request_conflict",
            "This resume request does not match the Connector wait",
        )
    if blocker.configuration_digest != expected_digest:
        raise _resume_error(
            409,
            "connector_configuration_changed",
            "The original Connector settings have changed",
        )


def _close_run(db, run, status, error_code, now):
    run.status = status
    run.error_code = error_code
    run.ended_at = now
    run.fence += 1
    job = db.query(RunJob).filter(RunJob.run_id == run.id).with_for_update().first()
    if job is not None:
        job.fence = run.fence
        job.owner_id = None
        job.lease_until = None


def _append_event(db, conversation, run, event_type, payload, actor_id=None):
    append_event(
        db,
        conversation.id,
        run.id,
        event_type,
        payload,
        actor_id=actor_id,
    )


def _resume_error(status, code, message):
    return HTTPException(
        status_code=status, detail={"error": {"code": code, "message": message}}
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
