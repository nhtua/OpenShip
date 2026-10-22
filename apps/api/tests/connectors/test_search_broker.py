from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.openship.auth.models import User
from src.openship.capabilities.contracts import ConnectorRef
from src.openship.config import settings
from src.openship.connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.service import create_connector
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project

API_KEY = "synthetic-managed-search-api-key"  # rayloc:ignore


def _keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    encryption_path = tmp_path / "connector-encryption.json"
    hmac_path = tmp_path / "connector-hmac.json"
    for path, key in ((encryption_path, b"e" * 32), (hmac_path, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "broker-test-v1",
                    "keys": {"broker-test-v1": base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)


def _identity(db, label: str):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"broker-{label}-{user_id.hex[:10]}",
        email=f"broker-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(
        id=uuid.uuid4(), owner_user_id=user_id, name=f"broker project {label}"
    )
    db.add_all([user, project])
    db.flush()
    return user, project


def _create_connector(db, user, project, *, visibility: str, api_key: str = API_KEY):
    result = create_connector(
        db,
        actor=user,
        visibility=visibility,
        project_id=project.id if visibility == "project" else None,
        kind="brave",
        name=f"{visibility} Brave",
        configuration_raw={"schema_version": 1, "kind": "brave"},
        api_key=api_key,
    )
    connector_id = uuid.UUID(result["id"])
    revision = (
        db.query(ConnectorRevision)
        .filter(ConnectorRevision.connector_id == connector_id)
        .one()
    )
    return ConnectorRef(
        id=connector_id,
        kind="brave",
        configuration_revision=revision.revision,
        visibility=visibility,
        credential_version_id=revision.credential_version_id,
    )


def test_global_search_credential_is_reused_for_each_authorized_project(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    broker = SearchCredentialBroker(SessionLocal)
    with SessionLocal() as db:
        operator, operator_project = _identity(db, "operator")
        _consumer_a, project_a = _identity(db, "a")
        _consumer_b, project_b = _identity(db, "b")
        reference = _create_connector(
            db, operator, operator_project, visibility="global"
        )
        project_a_id = project_a.id
        project_b_id = project_b.id
        db.commit()

    key_a = broker.acquire(project_id=project_a_id, kind="brave", reference=reference)
    key_b = broker.acquire(project_id=project_b_id, kind="brave", reference=reference)

    assert key_a == key_b == API_KEY
    assert API_KEY not in repr(broker)
    assert reference.visibility == "global"


def test_project_search_credential_cannot_cross_project_scope(monkeypatch, tmp_path):
    _keys(monkeypatch, tmp_path)
    broker = SearchCredentialBroker(SessionLocal)
    with SessionLocal() as db:
        owner, project = _identity(db, "owner")
        _foreign_owner, foreign_project = _identity(db, "foreign")
        reference = _create_connector(db, owner, project, visibility="project")
        foreign_project_id = foreign_project.id
        db.commit()

    with pytest.raises(ConnectorCredentialError) as refused:
        broker.acquire(project_id=foreign_project_id, kind="brave", reference=reference)

    assert refused.value.code == "connector_scope_denied"
    assert API_KEY not in str(refused.value)


def test_broker_rechecks_revocation_and_expiration_for_a_pinned_reference(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    broker = SearchCredentialBroker(SessionLocal)
    with SessionLocal() as db:
        owner, project = _identity(db, "state")
        reference = _create_connector(db, owner, project, visibility="project")
        project_id = project.id
        connector = db.get(Connector, reference.id)
        connector.revoked_at = datetime.now(UTC)
        db.commit()

    with pytest.raises(ConnectorCredentialError) as revoked:
        broker.acquire(project_id=project_id, kind="brave", reference=reference)
    assert revoked.value.code == "connector_revoked"

    with SessionLocal() as db:
        connector = db.get(Connector, reference.id)
        connector.revoked_at = None
        credential = db.get(CredentialVersion, reference.credential_version_id)
        credential.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    with pytest.raises(ConnectorCredentialError) as expired:
        broker.acquire(project_id=project_id, kind="brave", reference=reference)
    assert expired.value.code == "connector_expired"


def test_broker_refuses_a_credential_version_not_bound_to_the_pinned_revision(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    broker = SearchCredentialBroker(SessionLocal)
    with SessionLocal() as db:
        owner, project = _identity(db, "binding")
        reference = _create_connector(db, owner, project, visibility="project")
        project_id = project.id
        db.commit()
    forged = reference.model_copy(update={"credential_version_id": uuid.uuid4()})

    with pytest.raises(ConnectorCredentialError) as refused:
        broker.acquire(project_id=project_id, kind="brave", reference=forged)

    assert refused.value.code == "connector_scope_denied"
    assert API_KEY not in str(refused.value)


def test_broker_refuses_a_reference_for_a_different_search_kind(monkeypatch, tmp_path):
    _keys(monkeypatch, tmp_path)
    broker = SearchCredentialBroker(SessionLocal)
    with SessionLocal() as db:
        owner, project = _identity(db, "kind")
        reference = _create_connector(db, owner, project, visibility="project")
        project_id = project.id
        db.commit()
    forged = reference.model_copy(update={"kind": "tavily"})

    with pytest.raises(ConnectorCredentialError) as refused:
        broker.acquire(project_id=project_id, kind="brave", reference=forged)

    assert refused.value.code == "connector_scope_denied"
    assert API_KEY not in str(refused.value)
