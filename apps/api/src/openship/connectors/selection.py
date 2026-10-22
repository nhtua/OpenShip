"""Deterministic project-aware Connector selection without credential access."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)


class ConnectorSelectionError(ValueError):
    """A fixed selection failure safe to expose to an authenticated caller."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ResolvedConnector:
    connector_id: uuid.UUID
    kind: str
    visibility: str
    project_id: uuid.UUID | None
    revision: int
    credential_version_id: uuid.UUID
    configuration: dict
    configuration_digest: str


def _visible_query(db: Session, project_id: uuid.UUID, service: str):
    return (
        db.query(Connector, ConnectorRevision, CredentialVersion)
        .join(
            ConnectorRevision,
            (ConnectorRevision.connector_id == Connector.id)
            & (ConnectorRevision.revision == Connector.current_revision),
        )
        .join(
            CredentialVersion,
            (CredentialVersion.connector_id == Connector.id)
            & (CredentialVersion.id == ConnectorRevision.credential_version_id),
        )
        .filter(
            Connector.kind == service,
            (
                (Connector.visibility == "global")
                & (Connector.project_id.is_(None))
            )
            | (
                (Connector.visibility == "project")
                & (Connector.project_id == project_id)
            ),
        )
    )


def _eligible(row) -> bool:
    connector, _revision, credential = row
    expires_at = credential.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return (
        connector.enabled
        and connector.revoked_at is None
        and credential.state == "active"
        and credential.revoked_at is None
        and (expires_at is None or expires_at > datetime.now(UTC))
    )


def _unavailable_code(row) -> str:
    connector, _revision, credential = row
    if connector.revoked_at is not None or credential.revoked_at is not None or credential.state == "revoked":
        return "connector_revoked"
    expires_at = credential.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return "connector_expired"
    return "connector_unavailable"


def _resolved(row) -> ResolvedConnector:
    connector, revision, _credential = row
    try:
        configuration = json.loads(revision.configuration_json)
    except (TypeError, ValueError):
        raise ConnectorSelectionError("connector_unavailable") from None
    if not isinstance(configuration, dict):
        raise ConnectorSelectionError("connector_unavailable")
    return ResolvedConnector(
        connector_id=connector.id,
        kind=connector.kind,
        visibility=connector.visibility,
        project_id=connector.project_id,
        revision=revision.revision,
        credential_version_id=revision.credential_version_id,
        configuration=configuration,
        configuration_digest=revision.configuration_digest,
    )


def resolve_connector(
    db: Session,
    *,
    project_id: uuid.UUID,
    service: str,
    selected_id: uuid.UUID | None = None,
) -> ResolvedConnector:
    """Choose explicit ID, then its project default, then a sole eligible record.

    Visibility is always checked against the consuming project. A default is
    authoritative even when unavailable: it never falls back to another scope.
    """
    if service not in {"brave", "tavily"}:
        raise ConnectorSelectionError("connector_required")
    visible = _visible_query(db, project_id, service)

    if selected_id is not None:
        row = visible.filter(Connector.id == selected_id).first()
        if row is None:
            raise ConnectorSelectionError("connector_scope_denied")
        if not _eligible(row):
            raise ConnectorSelectionError(_unavailable_code(row))
        return _resolved(row)

    default = (
        db.query(ProjectConnectorDefault)
        .filter(
            ProjectConnectorDefault.project_id == project_id,
            ProjectConnectorDefault.service == service,
        )
        .first()
    )
    if default is not None and default.connector_id is not None:
        row = visible.filter(Connector.id == default.connector_id).first()
        if row is None:
            raise ConnectorSelectionError("connector_scope_denied")
        if not _eligible(row):
            raise ConnectorSelectionError(_unavailable_code(row))
        return _resolved(row)

    candidates = [row for row in visible.all() if _eligible(row)]
    if not candidates:
        raise ConnectorSelectionError("connector_required")
    if len(candidates) > 1:
        raise ConnectorSelectionError("connector_ambiguous")
    return _resolved(candidates[0])
