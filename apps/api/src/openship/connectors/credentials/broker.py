"""Resolve only admitted search Connector credentials for one project."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy.orm import Session

from ...capabilities.contracts import ConnectorRef
from ...config import settings
from ..credentials.dependencies import get_credential_store
from ..credentials.recovery import managed_dispatch_is_locked
from ..credentials.store import CredentialContext, CredentialEnvelope
from ..errors import CredentialStoreUnavailable
from ..models import Connector, ConnectorRevision, CredentialVersion


class ConnectorCredentialError(RuntimeError):
    """Closed, secret-free refusal to use a pinned Connector credential."""

    _CODES: ClassVar[frozenset[str]] = frozenset({
        "connector_scope_denied",
        "connector_expired",
        "connector_revoked",
        "connector_unavailable",
        "credential_store_unavailable",
    })

    def __init__(self, code: str):
        self.code = code if code in self._CODES else "connector_unavailable"
        super().__init__(self.code)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _connector_bundle(
    db: Session,
    *,
    project_id: UUID,
    kind: str,
    reference: ConnectorRef,
    lock: bool = False,
) -> tuple[Connector, ConnectorRevision, CredentialVersion]:
    if kind not in {"brave", "tavily"} or reference.kind != kind:
        raise ConnectorCredentialError("connector_scope_denied")
    connector_query = db.query(Connector).filter(Connector.id == reference.id)
    if lock:
        connector_query = connector_query.with_for_update()
    connector = connector_query.first()
    if (
        connector is None
        or connector.kind != kind
        or connector.visibility != reference.visibility
        or (
            connector.visibility == "global"
            and connector.project_id is not None
        )
        or (
            connector.visibility == "project"
            and connector.project_id != project_id
        )
    ):
        raise ConnectorCredentialError("connector_scope_denied")

    revision_query = (
        db.query(ConnectorRevision)
        .filter(
            ConnectorRevision.connector_id == connector.id,
            ConnectorRevision.revision == reference.configuration_revision,
            ConnectorRevision.credential_version_id == reference.credential_version_id,
            ConnectorRevision.visibility == connector.visibility,
            ConnectorRevision.project_id == connector.project_id,
        )
    )
    credential_query = db.query(CredentialVersion).filter(
        CredentialVersion.id == reference.credential_version_id,
        CredentialVersion.connector_id == connector.id,
        CredentialVersion.visibility == connector.visibility,
        CredentialVersion.project_id == connector.project_id,
        CredentialVersion.kind == f"{kind}_api_key",
    )
    if lock:
        revision_query = revision_query.with_for_update()
        credential_query = credential_query.with_for_update()
    revision = revision_query.first()
    credential = credential_query.first()
    if revision is None or credential is None:
        raise ConnectorCredentialError("connector_scope_denied")
    if connector.revoked_at is not None or credential.revoked_at is not None or credential.state == "revoked":
        raise ConnectorCredentialError("connector_revoked")
    if not connector.enabled:
        raise ConnectorCredentialError("connector_unavailable")
    if credential.state != "active":
        raise ConnectorCredentialError("connector_unavailable")
    if credential.expires_at is not None and _utc(credential.expires_at) <= datetime.now(UTC):
        raise ConnectorCredentialError("connector_expired")
    return connector, revision, credential


class SearchCredentialBroker:
    """Decrypt an admitted search key after rechecking its live authority.

    Search keys are not cached. Every acquisition reloads connector, immutable
    revision and credential state so a rotation, disable or revocation is
    observed before the next dispatch.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        credential_store_factory=None,
    ) -> None:
        self._session_factory = session_factory
        self._credential_store_factory = credential_store_factory or get_credential_store

    def validate(
        self,
        *,
        project_id: UUID,
        kind: str,
        reference: ConnectorRef,
    ) -> None:
        if managed_dispatch_is_locked():
            raise ConnectorCredentialError("connector_unavailable")
        with self._session_factory() as db:
            _connector_bundle(
                db,
                project_id=project_id,
                kind=kind,
                reference=reference,
            )

    def validate_in_session(
        self,
        db: Session,
        *,
        project_id: UUID,
        kind: str,
        reference: ConnectorRef,
        lock: bool = False,
    ) -> None:
        """Recheck a pinned reference in the caller's publication transaction."""
        _connector_bundle(
            db,
            project_id=project_id,
            kind=kind,
            reference=reference,
            lock=lock,
        )

    def acquire(
        self,
        *,
        project_id: UUID,
        kind: str,
        reference: ConnectorRef,
    ) -> str:
        if managed_dispatch_is_locked():
            raise ConnectorCredentialError("connector_unavailable")
        with self._session_factory() as db:
            connector, _revision, credential = _connector_bundle(
                db,
                project_id=project_id,
                kind=kind,
                reference=reference,
            )
            installation_id = settings.connector_installation_id
            if installation_id is None:
                raise ConnectorCredentialError("credential_store_unavailable")
            context = CredentialContext(
                installation_id=installation_id,
                visibility=connector.visibility,
                project_id=connector.project_id,
                connector_id=connector.id,
                credential_version_id=credential.id,
                schema_version=credential.schema_version,
                kind=credential.kind,
            )
            try:
                envelope = CredentialEnvelope(
                    key_id=credential.key_id,
                    nonce=credential.nonce,
                    ciphertext=credential.ciphertext,
                )
                payload = self._credential_store_factory().decrypt(context, envelope)
            except CredentialStoreUnavailable:
                raise ConnectorCredentialError("credential_store_unavailable") from None
        api_key = payload.get("api_key")
        if (
            not isinstance(api_key, str)
            or not api_key
            or api_key != api_key.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in api_key)
        ):
            raise ConnectorCredentialError("connector_unavailable")
        return api_key


__all__ = ["ConnectorCredentialError", "SearchCredentialBroker"]
