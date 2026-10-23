"""Transactional lifecycle operations for project and installation Connectors."""

from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.models import User
from ..config import settings
from ..workspace.lifecycle import assert_active_project
from ..workspace.models import Project
from .audit import record_audit
from .contracts import ConnectorConfiguration, ConnectorUsage, canonical_json
from .credentials.dependencies import get_credential_store, get_idempotency_keyring
from .credentials.store import CredentialContext, CredentialEnvelope
from .errors import CredentialStoreUnavailable
from .models import (
    Connector,
    ConnectorAuditEvent,
    ConnectorRequestLedger,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from .selection import ConnectorSelectionError, resolve_connector

MAX_CONNECTORS_PER_SCOPE = 10
REQUEST_TTL = timedelta(hours=24)
WRITE_RATE_WINDOW = timedelta(minutes=1)
WRITE_RATE_DEFAULT = 20

_ERROR_STATUS = {
    "connector_not_found": 404,
    "connector_scope_denied": 404,
    "project_not_found": 404,
    "connector_revision_changed": 409,
    "connector_request_conflict": 409,
    "connector_request_expired": 410,
    "connector_write_rate_limited": 429,
    "connector_limit_reached": 409,
    "connector_required": 409,
    "connector_ambiguous": 409,
    "connector_expired": 409,
    "connector_revoked": 409,
    "connector_unavailable": 409,
    "credential_store_unavailable": 503,
    "global_management_forbidden": 403,
    "connector_visibility_unsupported": 422,
    "invalid_connector_request": 422,
}


class ConnectorCommandError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    @property
    def status_code(self) -> int:
        return _ERROR_STATUS.get(self.code, 400)


@dataclass(frozen=True, slots=True)
class RequestReservation:
    row: ConnectorRequestLedger
    replay_result: dict[str, Any] | None = None


def owned_project(db: Session, *, project_id: uuid.UUID, user_id: uuid.UUID) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.owner_user_id == user_id)
        .first()
    )
    if project is None:
        raise ConnectorCommandError("project_not_found")
    return project


def lock_active_project(
    db: Session, *, project_id: uuid.UUID, user_id: uuid.UUID
) -> Project:
    """Refresh and lock an owned Project before any project-scoped write."""
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.owner_user_id == user_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if project is None:
        raise ConnectorCommandError("project_not_found")
    assert_active_project(project)
    return project


def _lock_connector_project(db: Session, connector: Connector, actor: User) -> None:
    if connector.visibility == "project" and connector.project_id is not None:
        lock_active_project(
            db,
            project_id=connector.project_id,
            user_id=actor.id,
        )


def require_global_operator(user: User) -> None:
    if (
        settings.connector_operator_user_id is None
        or user.id != settings.connector_operator_user_id
    ):
        raise ConnectorCommandError("global_management_forbidden")


def owned_connector(
    db: Session, *, connector_id: uuid.UUID, project_id: uuid.UUID
) -> Connector:
    connector = (
        db.query(Connector)
        .filter(
            Connector.id == connector_id,
            Connector.visibility == "project",
            Connector.project_id == project_id,
        )
        .first()
    )
    if connector is None:
        raise ConnectorCommandError("connector_not_found")
    return connector


def global_connector(db: Session, *, connector_id: uuid.UUID) -> Connector:
    connector = (
        db.query(Connector)
        .filter(
            Connector.id == connector_id,
            Connector.visibility == "global",
            Connector.project_id.is_(None),
            Connector.kind.in_(("brave", "tavily")),
        )
        .first()
    )
    if connector is None:
        raise ConnectorCommandError("connector_not_found")
    return connector


def current_revision(db: Session, connector: Connector) -> ConnectorRevision:
    revision = (
        db.query(ConnectorRevision)
        .filter(
            ConnectorRevision.connector_id == connector.id,
            ConnectorRevision.revision == connector.current_revision,
        )
        .first()
    )
    if revision is None:
        raise ConnectorCommandError("connector_unavailable")
    return revision


def usage(
    db: Session, connector: Connector, *, is_default: bool = False
) -> ConnectorUsage:
    revision = current_revision(db, connector)
    credential = db.get(CredentialVersion, revision.credential_version_id)
    if credential is None:
        status = "unavailable"
    elif (
        connector.revoked_at is not None
        or credential.revoked_at is not None
        or credential.state == "revoked"
    ):
        status = "revoked"
    elif credential.expires_at is not None:
        expires_at = credential.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        status = "expired" if expires_at <= datetime.now(UTC) else "configured"
    elif not connector.enabled:
        status = "disabled"
    else:
        status = "configured"
    return ConnectorUsage(
        id=connector.id,
        kind=connector.kind,
        visibility=connector.visibility,
        name=connector.name,
        status=status,
        revision=connector.current_revision,
        is_default=is_default,
    )


def list_project_connectors(
    db: Session, *, project_id: uuid.UUID, service: str | None = None
) -> list[ConnectorUsage]:
    own_query = db.query(Connector).filter(
        Connector.visibility == "project", Connector.project_id == project_id
    )
    global_query = db.query(Connector).filter(
        Connector.visibility == "global",
        Connector.project_id.is_(None),
        Connector.enabled.is_(True),
        Connector.revoked_at.is_(None),
        Connector.kind.in_(("brave", "tavily")),
    )
    if service is not None:
        own_query = own_query.filter(Connector.kind == service)
        global_query = global_query.filter(Connector.kind == service)
    rows = own_query.order_by(Connector.created_at, Connector.id).all()
    global_rows = global_query.order_by(Connector.created_at, Connector.id).all()
    rows.extend(
        connector
        for connector in global_rows
        if usage(db, connector).status == "configured"
    )
    defaults = {
        (row.service, row.connector_id)
        for row in db.query(ProjectConnectorDefault)
        .filter(ProjectConnectorDefault.project_id == project_id)
        .all()
        if row.connector_id is not None
    }
    return [
        usage(db, connector, is_default=(connector.kind, connector.id) in defaults)
        for connector in rows
    ]


def list_global_connectors(db: Session) -> list[ConnectorUsage]:
    rows = (
        db.query(Connector)
        .filter(
            Connector.visibility == "global",
            Connector.project_id.is_(None),
            Connector.kind.in_(("brave", "tavily")),
        )
        .order_by(Connector.created_at, Connector.id)
        .all()
    )
    return [usage(db, connector) for connector in rows]


def detail(
    db: Session, connector: Connector, *, is_default: bool = False
) -> dict[str, Any]:
    revision = current_revision(db, connector)
    summary = usage(db, connector, is_default=is_default)
    return {
        **summary.model_dump(mode="json"),
        "configuration": json.loads(revision.configuration_json),
        "configuration_digest": revision.configuration_digest,
    }


def _validate_configuration(
    kind: str, raw: dict[str, Any] | None
) -> ConnectorConfiguration:
    try:
        configuration = ConnectorConfiguration.model_validate(raw or {"kind": kind})
    except (TypeError, ValueError):
        raise ConnectorCommandError("invalid_connector_request") from None
    if configuration.kind != kind:
        raise ConnectorCommandError("invalid_connector_request")
    try:
        configuration.canonical_bytes()
    except ValueError:
        raise ConnectorCommandError("invalid_connector_request") from None
    return configuration


def _credential_context(
    *,
    connector_id: uuid.UUID,
    credential_version_id: uuid.UUID,
    visibility: str,
    project_id: uuid.UUID | None,
    kind: str,
) -> CredentialContext:
    if settings.connector_installation_id is None:
        raise CredentialStoreUnavailable()
    return CredentialContext(
        installation_id=settings.connector_installation_id,
        visibility=visibility,
        project_id=project_id,
        connector_id=connector_id,
        credential_version_id=credential_version_id,
        schema_version=1,
        kind=kind,
    )


def _new_credential(
    db: Session,
    *,
    connector: Connector,
    project_id: uuid.UUID | None,
    api_key: str,
) -> CredentialVersion:
    if connector.kind not in {"brave", "tavily"} or not isinstance(api_key, str):
        raise ConnectorCommandError("invalid_connector_request")
    kind = f"{connector.kind}_api_key"
    credential_id = uuid.uuid4()
    context = _credential_context(
        connector_id=connector.id,
        credential_version_id=credential_id,
        visibility=connector.visibility,
        project_id=project_id,
        kind=kind,
    )
    envelope = get_credential_store().encrypt(context, {"api_key": api_key})
    credential = CredentialVersion(
        id=credential_id,
        connector_id=connector.id,
        visibility=connector.visibility,
        project_id=project_id,
        kind=kind,
        schema_version=1,
        key_id=envelope.key_id,
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext,
        state="active",
    )
    db.add(credential)
    return credential


def _new_revision(
    db: Session,
    *,
    connector: Connector,
    configuration: ConnectorConfiguration,
    credential_version_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> ConnectorRevision:
    next_revision = connector.current_revision + 1
    revision = ConnectorRevision(
        id=uuid.uuid4(),
        connector_id=connector.id,
        revision=next_revision,
        schema_version=1,
        visibility=connector.visibility,
        project_id=connector.project_id,
        configuration_json=configuration.canonical_bytes().decode("utf-8"),
        configuration_digest=configuration.digest(),
        credential_version_id=credential_version_id,
        created_by_user_id=actor_user_id,
    )
    db.add(revision)
    connector.current_revision = next_revision
    connector.updated_at = datetime.now(UTC)
    return revision


def _audit(
    db: Session,
    *,
    connector: Connector,
    actor_user_id: uuid.UUID,
    action: str,
    revision: int,
) -> ConnectorAuditEvent:
    return record_audit(
        db,
        visibility=connector.visibility,
        project_id=connector.project_id,
        actor_user_id=actor_user_id,
        connector_id=connector.id,
        connector_revision=revision,
        action=action,
        details={"kind": connector.kind, "revision": revision},
    )


def create_connector(
    db: Session,
    *,
    actor: User,
    visibility: str,
    project_id: uuid.UUID | None,
    kind: str,
    name: str,
    configuration_raw: dict[str, Any] | None,
    api_key: str,
) -> dict[str, Any]:
    if visibility != "global" or project_id is not None:
        raise ConnectorCommandError("connector_visibility_unsupported")
    require_global_operator(actor)
    if kind not in {"brave", "tavily"} or not name.strip() or len(name) > 255:
        raise ConnectorCommandError("invalid_connector_request")
    db.query(User).filter(User.id == actor.id).with_for_update().first()
    count = (
        db.query(func.count(Connector.id))
        .filter(Connector.visibility == "global", Connector.project_id.is_(None))
        .scalar()
    )
    if count >= MAX_CONNECTORS_PER_SCOPE:
        raise ConnectorCommandError("connector_limit_reached")
    configuration = _validate_configuration(kind, configuration_raw)
    connector = Connector(
        id=uuid.uuid4(),
        kind=kind,
        visibility=visibility,
        project_id=project_id,
        name=name.strip(),
        current_revision=1,
        enabled=True,
        managed_by_user_id=actor.id,
        created_by_user_id=actor.id,
    )
    db.add(connector)
    db.flush()
    credential = _new_credential(
        db,
        connector=connector,
        project_id=project_id,
        api_key=api_key,
    )
    db.flush()
    revision = ConnectorRevision(
        id=uuid.uuid4(),
        connector_id=connector.id,
        revision=1,
        schema_version=1,
        visibility=visibility,
        project_id=project_id,
        configuration_json=configuration.canonical_bytes().decode("utf-8"),
        configuration_digest=configuration.digest(),
        credential_version_id=credential.id,
        created_by_user_id=actor.id,
    )
    db.add(revision)
    db.flush()
    _audit(
        db, connector=connector, actor_user_id=actor.id, action="created", revision=1
    )
    return detail(db, connector)


def update_connector(
    db: Session,
    *,
    connector: Connector,
    actor: User,
    expected_revision: int,
    name: str | None = None,
    configuration_raw: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    _lock_connector_project(db, connector, actor)
    connector = (
        db.query(Connector).filter(Connector.id == connector.id).with_for_update().one()
    )
    if connector.current_revision != expected_revision:
        raise ConnectorCommandError("connector_revision_changed")
    if connector.revoked_at is not None:
        raise ConnectorCommandError("connector_revoked")
    if name is not None:
        if not name.strip() or len(name) > 255:
            raise ConnectorCommandError("invalid_connector_request")
        connector.name = name.strip()
    if enabled is not None:
        if connector.revoked_at is not None and enabled:
            raise ConnectorCommandError("connector_revoked")
        connector.enabled = enabled
    prior = current_revision(db, connector)
    if configuration_raw is None:
        configuration = ConnectorConfiguration.model_validate_json(
            prior.configuration_json
        )
    else:
        configuration = _validate_configuration(connector.kind, configuration_raw)
    revision = _new_revision(
        db,
        connector=connector,
        configuration=configuration,
        credential_version_id=prior.credential_version_id,
        actor_user_id=actor.id,
    )
    db.flush()
    _audit(
        db,
        connector=connector,
        actor_user_id=actor.id,
        action="updated",
        revision=revision.revision,
    )
    return detail(db, connector)


def delete_connector(
    db: Session,
    *,
    connector: Connector,
    actor: User,
    expected_revision: int,
) -> dict[str, Any]:
    """Remove a Connector and its encrypted credential history permanently."""
    _lock_connector_project(db, connector, actor)
    connector = (
        db.query(Connector).filter(Connector.id == connector.id).with_for_update().one()
    )
    if connector.current_revision != expected_revision:
        raise ConnectorCommandError("connector_revision_changed")
    revision = current_revision(db, connector)
    now = datetime.now(UTC)

    defaults = (
        db.query(ProjectConnectorDefault)
        .filter(ProjectConnectorDefault.connector_id == connector.id)
        .with_for_update()
        .all()
    )
    for row in defaults:
        old_revision = row.connector_revision
        row.connector_id = None
        row.connector_revision = None
        row.settings_revision += 1
        row.updated_by_user_id = actor.id
        row.updated_at = now
        record_audit(
            db,
            visibility="project",
            project_id=row.project_id,
            actor_user_id=actor.id,
            connector_revision=old_revision,
            action="default_cleared",
            details={
                "service": row.service,
                "settings_revision": row.settings_revision,
                "reason": "connector_deleted",
            },
        )

    record_audit(
        db,
        visibility=connector.visibility,
        project_id=connector.project_id,
        actor_user_id=actor.id,
        connector_revision=revision.revision,
        action="deleted",
        details={"kind": connector.kind, "revision": revision.revision},
    )
    db.flush()

    db.query(ConnectorRevision).filter(
        ConnectorRevision.connector_id == connector.id
    ).delete(synchronize_session=False)
    db.flush()
    db.query(CredentialVersion).filter(
        CredentialVersion.connector_id == connector.id
    ).delete(synchronize_session=False)
    db.delete(connector)
    db.flush()
    return {"id": str(connector.id), "deleted": True}


def connector_api_key(db: Session, connector: Connector) -> str:
    """Decrypt only the selected Connector's active key for a provider status check."""
    revision = current_revision(db, connector)
    credential = (
        db.query(CredentialVersion)
        .filter(
            CredentialVersion.id == revision.credential_version_id,
            CredentialVersion.connector_id == connector.id,
            CredentialVersion.visibility == connector.visibility,
            CredentialVersion.project_id == connector.project_id,
            CredentialVersion.kind == f"{connector.kind}_api_key",
            CredentialVersion.state == "active",
            CredentialVersion.revoked_at.is_(None),
        )
        .first()
    )
    if credential is None or connector.revoked_at is not None:
        raise ConnectorCommandError("connector_unavailable")
    context = _credential_context(
        connector_id=connector.id,
        credential_version_id=credential.id,
        visibility=connector.visibility,
        project_id=connector.project_id,
        kind=credential.kind,
    )
    envelope = CredentialEnvelope(
        key_id=credential.key_id,
        nonce=credential.nonce,
        ciphertext=credential.ciphertext,
    )
    try:
        payload = get_credential_store().decrypt(context, envelope)
    except CredentialStoreUnavailable:
        raise ConnectorCommandError("credential_store_unavailable") from None
    api_key = payload.get("api_key")
    if (
        not isinstance(api_key, str)
        or not api_key
        or api_key != api_key.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in api_key)
    ):
        raise ConnectorCommandError("connector_unavailable")
    return api_key


def replace_credential(
    db: Session,
    *,
    connector: Connector,
    actor: User,
    expected_revision: int,
    api_key: str,
) -> dict[str, Any]:
    _lock_connector_project(db, connector, actor)
    connector = (
        db.query(Connector).filter(Connector.id == connector.id).with_for_update().one()
    )
    if connector.current_revision != expected_revision:
        raise ConnectorCommandError("connector_revision_changed")
    if connector.revoked_at is not None:
        raise ConnectorCommandError("connector_revoked")
    prior = current_revision(db, connector)
    credential = _new_credential(
        db,
        connector=connector,
        project_id=connector.project_id,
        api_key=api_key,
    )
    db.flush()
    configuration = ConnectorConfiguration.model_validate_json(prior.configuration_json)
    revision = _new_revision(
        db,
        connector=connector,
        configuration=configuration,
        credential_version_id=credential.id,
        actor_user_id=actor.id,
    )
    db.flush()
    _audit(
        db,
        connector=connector,
        actor_user_id=actor.id,
        action="credential_replaced",
        revision=revision.revision,
    )
    return detail(db, connector)


def set_default(
    db: Session,
    *,
    project_id: uuid.UUID,
    actor: User,
    service: str,
    expected_revision: int,
    connector_id: uuid.UUID | None,
) -> dict[str, Any]:
    if service not in {"brave", "tavily"}:
        raise ConnectorCommandError("invalid_connector_request")
    lock_active_project(db, project_id=project_id, user_id=actor.id)
    row = (
        db.query(ProjectConnectorDefault)
        .filter(
            ProjectConnectorDefault.project_id == project_id,
        )
        .with_for_update()
        .first()
    )
    current = row.settings_revision if row is not None else 0
    if current != expected_revision:
        raise ConnectorCommandError("connector_revision_changed")
    resolved = None
    if connector_id is not None:
        try:
            resolved = resolve_connector(
                db,
                project_id=project_id,
                service=service,
                selected_id=connector_id,
            )
        except ConnectorSelectionError as exc:
            raise ConnectorCommandError(exc.code) from None
        if resolved.visibility != "global":
            raise ConnectorCommandError("connector_visibility_unsupported")
        target_revision = resolved.revision
    else:
        target_revision = None
    next_revision = current + 1
    if row is None:
        row = ProjectConnectorDefault(
            id=uuid.uuid4(),
            project_id=project_id,
            service=service,
            connector_id=connector_id,
            connector_revision=target_revision,
            settings_revision=next_revision,
            updated_by_user_id=actor.id,
            updated_at=datetime.now(UTC),
        )
        db.add(row)
    else:
        row.service = service
        row.connector_id = connector_id
        row.connector_revision = target_revision
        row.settings_revision = next_revision
        row.updated_by_user_id = actor.id
        row.updated_at = datetime.now(UTC)
    db.flush()
    record_audit(
        db,
        visibility="project",
        project_id=project_id,
        actor_user_id=actor.id,
        connector_id=connector_id,
        connector_revision=target_revision,
        action="default_set" if connector_id else "default_cleared",
        details={"service": service, "settings_revision": next_revision},
    )
    return {
        "service": service,
        "connector_id": str(connector_id) if connector_id else None,
        "revision": next_revision,
    }


def _request_key_digest(
    keyring, *, visibility: str, project_id, request_id, key_id: str
):
    request_bytes = canonical_json(
        {
            "visibility": visibility,
            "project_id": str(project_id) if project_id is not None else None,
            "request_id": str(request_id),
        }
    )
    return keyring.digest(request_bytes, key_id=key_id)[1]


def _find_request(
    db: Session,
    *,
    keyring,
    visibility: str,
    project_id,
    actor_user_id: uuid.UUID,
    request_id: uuid.UUID,
) -> ConnectorRequestLedger | None:
    for key_id in keyring.key_ids:
        request_digest = _request_key_digest(
            keyring,
            visibility=visibility,
            project_id=project_id,
            request_id=request_id,
            key_id=key_id,
        )
        query = db.query(ConnectorRequestLedger).filter(
            ConnectorRequestLedger.visibility == visibility,
            ConnectorRequestLedger.actor_user_id == actor_user_id,
            ConnectorRequestLedger.key_id == key_id,
            ConnectorRequestLedger.request_key_hmac == request_digest,
        )
        if project_id is None:
            query = query.filter(ConnectorRequestLedger.project_id.is_(None))
        else:
            query = query.filter(ConnectorRequestLedger.project_id == project_id)
        row = query.first()
        if row is not None:
            return row
    return None


def reserve_request(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    visibility: str,
    project_id: uuid.UUID | None,
    request_id: uuid.UUID,
    command: str,
    payload: Any,
) -> RequestReservation:
    keyring = get_idempotency_keyring()
    row = _find_request(
        db,
        keyring=keyring,
        visibility=visibility,
        project_id=project_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    try:
        payload_bytes = canonical_json({"command": command, "payload": payload})
    except (TypeError, ValueError):
        raise ConnectorCommandError("invalid_connector_request") from None
    if row is not None:
        now = datetime.now(UTC)
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise ConnectorCommandError("connector_request_expired")
        expected = keyring.digest(payload_bytes, key_id=row.key_id)[1]
        if not hmac.compare_digest(expected, row.payload_hmac):
            raise ConnectorCommandError("connector_request_conflict")
        try:
            result = json.loads(row.result_json or "null")
        except ValueError:
            raise ConnectorCommandError("connector_unavailable") from None
        if not isinstance(result, dict):
            raise ConnectorCommandError("connector_unavailable")
        return RequestReservation(row=row, replay_result=result)

    now = datetime.now(UTC)
    limit = settings.connector_write_rate_limit_per_minute or WRITE_RATE_DEFAULT
    recent = db.query(func.count(ConnectorRequestLedger.id)).filter(
        ConnectorRequestLedger.actor_user_id == actor_user_id,
        ConnectorRequestLedger.visibility == visibility,
        ConnectorRequestLedger.created_at >= now - WRITE_RATE_WINDOW,
    )
    if project_id is None:
        recent = recent.filter(ConnectorRequestLedger.project_id.is_(None))
    else:
        recent = recent.filter(ConnectorRequestLedger.project_id == project_id)
    if recent.scalar() >= limit:
        raise ConnectorCommandError("connector_write_rate_limited")

    key_id = keyring.active_key_id
    row = ConnectorRequestLedger(
        id=uuid.uuid4(),
        visibility=visibility,
        project_id=project_id,
        actor_user_id=actor_user_id,
        key_id=key_id,
        request_key_hmac=_request_key_digest(
            keyring,
            visibility=visibility,
            project_id=project_id,
            request_id=request_id,
            key_id=key_id,
        ),
        payload_hmac=keyring.digest(payload_bytes, key_id=key_id)[1],
        result_json=None,
        expires_at=now + REQUEST_TTL,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        duplicate = _find_request(
            db,
            keyring=keyring,
            visibility=visibility,
            project_id=project_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        if duplicate is None:
            raise ConnectorCommandError("connector_request_conflict") from None
        return reserve_request(
            db,
            actor_user_id=actor_user_id,
            visibility=visibility,
            project_id=project_id,
            request_id=request_id,
            command=command,
            payload=payload,
        )
    return RequestReservation(row=row)


def finalize_request(
    reservation: RequestReservation,
    result: dict[str, Any],
    *,
    connector_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if reservation.replay_result is not None:
        return reservation.replay_result
    reservation.row.connector_id = connector_id
    reservation.row.result_json = canonical_json(result).decode("utf-8")
    return result


def request_status(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    visibility: str,
    project_id: uuid.UUID | None,
    request_id: uuid.UUID,
) -> dict[str, Any]:
    row = _find_request(
        db,
        keyring=get_idempotency_keyring(),
        visibility=visibility,
        project_id=project_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    if row is None:
        raise ConnectorCommandError("connector_not_found")
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise ConnectorCommandError("connector_request_expired")
    if row.result_json is None:
        return {"status": "processing"}
    try:
        result = json.loads(row.result_json)
    except ValueError:
        raise ConnectorCommandError("connector_unavailable") from None
    if not isinstance(result, dict):
        raise ConnectorCommandError("connector_unavailable")
    return {"status": "complete", "result": result}
