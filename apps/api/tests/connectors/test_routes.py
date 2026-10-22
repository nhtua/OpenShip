from __future__ import annotations

import asyncio
import base64
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.config import settings
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.routes import (
    _require_browser_origin,
    global_connector_create,
    global_connector_request_status,
    project_connector_create,
    project_connector_default_get,
    project_connector_default_set,
    project_connector_detail,
    project_connector_list,
    project_connector_replace_credential,
    project_connector_update,
)
from src.openship.connectors.service import (
    ConnectorCommandError,
    _audit,
    _new_credential,
    create_connector,
    detail,
)
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project

ORIGIN = "http://localhost:5173"


@pytest.fixture
def connector_identity():
    user_id = uuid.uuid4()
    username = f"connector-{user_id.hex[:12]}"
    user = User(
        id=user_id,
        username=username,
        email=f"{user_id.hex}@example.test",
        password_hash="unused",
    )
    project = Project(
        id=uuid.uuid4(),
        owner_user_id=user_id,
        name="connector test project",
    )
    project_id = project.id
    with SessionLocal() as db:
        db.add_all([user, project])
        db.commit()
    return SimpleNamespace(id=user_id, username=username), project_id


def _create_scope():
    user_id = uuid.uuid4()
    username = f"connector-{user_id.hex[:12]}"
    user = User(
        id=user_id,
        username=username,
        email=f"{user_id.hex}@example.test",
        password_hash="unused",
    )
    project = Project(
        id=uuid.uuid4(),
        owner_user_id=user_id,
        name="connector test project",
    )
    project_id = project.id
    with SessionLocal() as db:
        db.add_all([user, project])
        db.commit()
    return SimpleNamespace(id=user_id, username=username), project_id


def _install_keys(monkeypatch, tmp_path):
    encryption_path = tmp_path / "connector-encryption.json"
    hmac_path = tmp_path / "connector-idempotency.json"

    def write(path, key):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "test-v1",
                    "keys": {"test-v1": base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

    write(encryption_path, b"e" * 32)
    write(hmac_path, b"h" * 32)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)
    monkeypatch.setattr(settings, "connector_browser_origins", ORIGIN)


def _create_body(secret="synthetic-brave-secret-sentinel"):
    return {
        "kind": "brave",
        "name": "Primary Brave",
        "configuration": {"schema_version": 1, "kind": "brave"},
        "api_key": secret,
    }


def _seed_legacy_project_connector(actor, project_id, *, kind="brave"):
    """Insert an encrypted pre-policy fixture without using the creation API."""
    configuration = ConnectorConfiguration(kind=kind)
    with SessionLocal() as db:
        connector = Connector(
            id=uuid.uuid4(),
            kind=kind,
            visibility="project",
            project_id=project_id,
            name=f"Legacy {kind.title()}",
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
            api_key="synthetic-legacy-project-secret",
        )
        db.flush()
        db.add(
            ConnectorRevision(
                id=uuid.uuid4(),
                connector_id=connector.id,
                revision=1,
                schema_version=1,
                visibility="project",
                project_id=project_id,
                configuration_json=configuration.canonical_bytes().decode("utf-8"),
                configuration_digest=configuration.digest(),
                credential_version_id=credential.id,
                created_by_user_id=actor.id,
            )
        )
        _audit(
            db,
            connector=connector,
            actor_user_id=actor.id,
            action="created",
            revision=1,
        )
        db.commit()
        return detail(db, connector)


def _request(method, path, *, headers=None, content=None):
    body = content or b""
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    if body and not any(key == b"content-length" for key, _ in raw_headers):
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("test", 1234),
        "server": ("test", 80),
        "root_path": "",
    }
    consumed = False

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    request = Request(scope, receive)
    return request, lambda: consumed


def _invoke(awaitable):
    return asyncio.run(awaitable)


def _json_response(response):
    return json.loads(response.body)


def test_global_setup_encrypts_secret_once_and_exact_retry_returns_safe_result(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    body = _create_body()
    request_id = uuid.uuid4()
    path = "/api/global-connectors"
    headers = {"Origin": ORIGIN, "Idempotency-Key": str(request_id)}

    async def create(request_body):
        request, _consumed = _request(
            "POST", path, headers=headers, content=json.dumps(request_body).encode()
        )
        with SessionLocal() as db:
            return await global_connector_create(request, db, actor)

    created = _invoke(create(body))
    retried = _invoke(create(body))
    created_result = _json_response(created)

    assert created_result["visibility"] == "global"
    assert created_result["status"] == "configured"
    assert "api_key" not in created_result
    assert "synthetic-brave-secret-sentinel" not in json.dumps(created_result)
    assert _json_response(retried) == created_result

    with SessionLocal() as db:
        stored = db.query(CredentialVersion).filter(
            CredentialVersion.connector_id == uuid.UUID(created_result["id"])
        ).one()
        assert b"synthetic-brave-secret-sentinel" not in stored.ciphertext

    async def changed_retry():
        request, _consumed = _request(
            "POST",
            path,
            headers=headers,
            content=json.dumps(_create_body("different-secret-sentinel")).encode(),
        )
        with SessionLocal() as db:
            await global_connector_create(request, db, actor)

    with pytest.raises(HTTPException) as refused:
        _invoke(changed_retry())
    assert refused.value.status_code == 409
    assert "different-secret-sentinel" not in str(refused.value.detail)

    with SessionLocal() as db:
        status = global_connector_request_status(request_id, db, actor)
    assert status["status"] == "complete"
    assert status["result"]["id"] == created_result["id"]
    assert "api_key" not in json.dumps(status)


@pytest.mark.parametrize("kind", ["brave", "tavily"])
def test_new_project_search_credentials_are_refused_before_body_read(
    connector_identity, monkeypatch, tmp_path, kind
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    body = _create_body()
    body["kind"] = kind
    body["configuration"] = {"schema_version": 1, "kind": kind}
    request, consumed = _request(
        "POST",
        f"/api/projects/{project_id}/connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(body).encode(),
    )

    async def create():
        with SessionLocal() as db:
            return await project_connector_create(project_id, request, db, actor)

    with pytest.raises(HTTPException) as refused:
        _invoke(create())

    assert refused.value.status_code == 422
    assert refused.value.detail["error"]["code"] == "connector_visibility_unsupported"
    assert not consumed()
    assert "synthetic-brave-secret-sentinel" not in str(refused.value.detail)
    with SessionLocal() as db:
        assert db.query(Connector).filter(Connector.project_id == project_id).count() == 0


def test_shared_service_refuses_new_project_search_credentials(
    connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ConnectorCommandError) as refused:
            create_connector(
                db,
                actor=actor,
                visibility="project",
                project_id=project_id,
                kind="brave",
                name="Private Brave",
                configuration_raw={"schema_version": 1, "kind": "brave"},
                api_key="synthetic-brave-secret-sentinel",
            )
        assert refused.value.code == "connector_visibility_unsupported"
        db.rollback()
        assert db.query(Connector).filter(Connector.project_id == project_id).count() == 0


def test_shared_service_requires_operator_for_global_search_creation(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", uuid.uuid4())
    with SessionLocal() as db:
        with pytest.raises(ConnectorCommandError) as refused:
            create_connector(
                db,
                actor=actor,
                visibility="global",
                project_id=None,
                kind="tavily",
                name="Unauthorized Tavily",
                configuration_raw={"schema_version": 1, "kind": "tavily"},
                api_key="synthetic-tavily-secret-sentinel",
            )
        assert refused.value.code == "global_management_forbidden"
        db.rollback()


def test_credential_replacement_activates_new_revision_without_exposing_secret(
    connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    created = _seed_legacy_project_connector(actor, project_id)
    connector_id = uuid.UUID(created["id"])
    request_id = uuid.uuid4()
    headers = {"Origin": ORIGIN, "Idempotency-Key": str(request_id)}

    async def replace():
        request, _ = _request(
            "POST",
            f"/api/projects/{project_id}/connectors/{connector_id}/credentials",
            headers=headers,
            content=json.dumps(
                {"expected_revision": 1, "api_key": "replacement-secret-sentinel"}
            ).encode(),
        )
        with SessionLocal() as db:
            return await project_connector_replace_credential(
                project_id, connector_id, request, db, actor
            )

    replaced = _invoke(replace())
    assert replaced["revision"] == 2
    assert replaced["status"] == "configured"
    assert "replacement-secret-sentinel" not in json.dumps(replaced)
    replay = _invoke(replace())
    assert _json_response(replay) == replaced
    with SessionLocal() as db:
        versions = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector_id)
            .all()
        )
        current = project_connector_detail(project_id, connector_id, db, actor)
    assert len(versions) == 2
    assert current["revision"] == 2
    assert all(b"replacement-secret-sentinel" not in row.ciphertext for row in versions)


def test_authentication_and_origin_checks_precede_secret_body_reads(
    connector_identity, monkeypatch, tmp_path
):
    _actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    body = b"not-json synthetic-secret-sentinel"
    anonymous, anonymous_consumed = _request(
        "POST",
        f"/api/projects/{project_id}/connectors",
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        content=body,
    )
    with pytest.raises(HTTPException) as unauthorized:
        require_jwt(anonymous)
    assert unauthorized.value.status_code == 401
    assert not anonymous_consumed()

    refused_origin, origin_consumed = _request(
        "POST",
        f"/api/projects/{project_id}/connectors",
        headers={"Origin": "https://attacker.example"},
        content=body,
    )
    with pytest.raises(HTTPException) as refused:
        _require_browser_origin(refused_origin)
    assert refused.value.status_code == 403
    assert not origin_consumed()
    assert "synthetic-secret-sentinel" not in str(refused.value.detail)


def test_non_operator_global_write_and_foreign_project_write_are_hidden(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", uuid.uuid4())
    global_request, global_consumed = _request(
        "POST",
        "/api/global-connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(_create_body("global-secret-sentinel")).encode(),
    )

    async def denied_global():
        with SessionLocal() as db:
            return await global_connector_create(global_request, db, actor)

    with pytest.raises(HTTPException) as denied:
        _invoke(denied_global())
    assert denied.value.status_code == 403
    assert not global_consumed()
    assert "global-secret-sentinel" not in str(denied.value.detail)

    foreign_request, foreign_consumed = _request(
        "POST",
        f"/api/projects/{uuid.uuid4()}/connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(_create_body("foreign-secret-sentinel")).encode(),
    )

    async def denied_project():
        with SessionLocal() as db:
            return await project_connector_create(
                uuid.uuid4(), foreign_request, db, actor
            )

    with pytest.raises(HTTPException) as foreign:
        _invoke(denied_project())
    assert foreign.value.status_code == 404
    assert not foreign_consumed()
    assert "foreign-secret-sentinel" not in str(foreign.value.detail)


def test_global_connector_is_visible_to_another_project_without_secret_or_management_access(
    connector_identity, monkeypatch, tmp_path
):
    operator, operator_project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", operator.id)
    global_idempotency = uuid.uuid4()
    global_request, _ = _request(
        "POST",
        "/api/global-connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(global_idempotency)},
        content=json.dumps(_create_body()).encode(),
    )

    async def create_global():
        with SessionLocal() as db:
            return await global_connector_create(global_request, db, operator)

    created = _json_response(_invoke(create_global()))
    consumer, consumer_project_id = _create_scope()
    with SessionLocal() as db:
        visible = project_connector_list(consumer_project_id, None, db, consumer)

    assert created["visibility"] == "global"
    assert "project_id" not in created
    assert any(item.id == uuid.UUID(created["id"]) and item.visibility == "global" for item in visible)
    assert "api_key" not in json.dumps([item.model_dump(mode="json") for item in visible])
    assert operator_project_id != consumer_project_id


def test_project_defaults_set_clear_and_advance_revision_monotonically(
    connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    create_request, _ = _request(
        "POST",
        "/api/global-connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(_create_body()).encode(),
    )

    async def create():
        with SessionLocal() as db:
            return await global_connector_create(create_request, db, actor)

    connector = _json_response(_invoke(create()))
    connector_id = uuid.UUID(connector["id"])

    async def set_connector(expected_revision, selected_id):
        request, _ = _request(
            "PUT",
            f"/api/projects/{project_id}/connector-defaults/brave",
            headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            content=json.dumps(
                {"expected_revision": expected_revision, "connector_id": selected_id}
            ).encode(),
        )
        with SessionLocal() as db:
            return await project_connector_default_set(
                project_id, "brave", request, db, actor
            )

    selected = _invoke(set_connector(0, str(connector_id)))
    assert selected == {
        "service": "brave",
        "connector_id": str(connector_id),
        "revision": 1,
    }
    cleared = _invoke(set_connector(1, None))
    assert cleared == {"service": "brave", "connector_id": None, "revision": 2}
    with SessionLocal() as db:
        current = project_connector_default_get(project_id, "brave", db, actor)
    assert current == {"service": "brave", "connector_id": None, "revision": 2}

    with pytest.raises(HTTPException) as stale:
        _invoke(set_connector(1, str(connector_id)))
    assert stale.value.status_code == 409


def test_database_backed_write_rate_limit_and_invalid_secret_errors_are_safe(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    monkeypatch.setattr(settings, "connector_write_rate_limit_per_minute", 1)

    async def create(secret, request_id):
        request, _ = _request(
            "POST",
            "/api/global-connectors",
            headers={"Origin": ORIGIN, "Idempotency-Key": str(request_id)},
            content=json.dumps(_create_body(secret)).encode(),
        )
        with SessionLocal() as db:
            return await global_connector_create(request, db, actor)

    first = _json_response(_invoke(create("valid-first-secret", uuid.uuid4())))
    assert first["status"] == "configured"
    with pytest.raises(HTTPException) as limited:
        _invoke(create("valid-second-secret", uuid.uuid4()))
    assert limited.value.status_code == 429
    assert "valid-second-secret" not in str(limited.value.detail)

    monkeypatch.setattr(settings, "connector_write_rate_limit_per_minute", 20)
    with pytest.raises(HTTPException) as invalid:
        _invoke(create("bad", uuid.uuid4()))
    assert invalid.value.status_code == 422
    assert "bad" not in str(invalid.value.detail)


def test_duplicate_properties_and_non_finite_json_are_rejected_without_echoing_secrets(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    path = "/api/global-connectors"
    malformed_bodies = (
        (
            b'{"kind":"brave","name":"first","name":"second",'
            b'"configuration":{"schema_version":1,"kind":"brave"},'
            b'"api_key":"duplicate-property-secret-sentinel"}'
        ),
        (
            b'{"kind":"brave","name":"NaN","configuration":{"value":NaN},'
            b'"api_key":"non-finite-secret-sentinel"}'
        ),
    )

    for body in malformed_bodies:
        request, _ = _request(
            "POST",
            path,
            headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            content=body,
        )
        with SessionLocal() as db, pytest.raises(HTTPException) as invalid:
            _invoke(global_connector_create(request, db, actor))
        assert invalid.value.status_code == 400
        assert "secret-sentinel" not in str(invalid.value.detail)


def test_stale_revision_precondition_does_not_overwrite_newer_configuration(
    connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    created = _seed_legacy_project_connector(actor, project_id)
    path = f"/api/projects/{project_id}/connectors"
    connector_id = uuid.UUID(created["id"])

    async def update(expected_revision, name):
        request, _ = _request(
            "PATCH",
            f"{path}/{connector_id}",
            headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            content=json.dumps(
                {"expected_revision": expected_revision, "name": name}
            ).encode(),
        )
        with SessionLocal() as db:
            return await project_connector_update(
                project_id, connector_id, request, db, actor
            )

    updated = _invoke(update(1, "Updated Brave"))
    assert updated["revision"] == 2
    with pytest.raises(HTTPException) as stale:
        _invoke(update(1, "Stale Brave"))
    assert stale.value.status_code == 409

    with SessionLocal() as db:
        current = project_connector_detail(project_id, connector_id, db, actor)
    assert current["name"] == "Updated Brave"
    assert current["revision"] == 2
