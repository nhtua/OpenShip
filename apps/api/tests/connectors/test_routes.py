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
    ConnectorAuditEvent,
    ConnectorRequestLedger,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from src.openship.connectors.routes import (
    _require_browser_origin,
    connector_setup_context,
    global_connector_check,
    global_connector_create,
    global_connector_request_status,
    global_connector_update,
    project_connector_create,
    project_connector_default_get,
    project_connector_default_set,
    project_connector_detail,
    project_connector_list,
    project_connector_replace_credential,
    project_connector_request_status,
    project_connector_update,
)
from src.openship.connectors.routes import (
    router as connectors_router,
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
from src.openship.workspace.projects import archive_project, create_project

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


def _create_body(secret="synthetic-brave-secret-sentinel", *, kind="brave"):
    return {
        "kind": kind,
        "name": f"Primary {kind.title()}",
        "configuration": {"schema_version": 1, "kind": kind},
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
        stored = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == uuid.UUID(created_result["id"]))
            .one()
        )
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
        assert (
            db.query(Connector).filter(Connector.project_id == project_id).count() == 0
        )


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
        assert (
            db.query(Connector).filter(Connector.project_id == project_id).count() == 0
        )


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


def test_revoke_deletes_connector_credentials_and_clears_project_defaults(
    connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    with SessionLocal() as db:
        created = create_connector(
            db,
            actor=actor,
            visibility="global",
            project_id=None,
            kind="brave",
            name="Shared Brave",
            configuration_raw={"schema_version": 1, "kind": "brave"},
            api_key="synthetic-global-secret",  # rayloc:ignore
        )
        db.add(
            ProjectConnectorDefault(
                id=uuid.uuid4(),
                project_id=project_id,
                service="brave",
                connector_id=uuid.UUID(created["id"]),
                connector_revision=1,
                settings_revision=4,
                updated_by_user_id=actor.id,
            )
        )
        db.commit()

    request_id = uuid.uuid4()
    headers = {"Origin": ORIGIN, "Idempotency-Key": str(request_id)}

    async def revoke():
        request, _ = _request(
            "PATCH",
            f"/api/global-connectors/{created['id']}",
            headers=headers,
            content=json.dumps({"expected_revision": 1, "revoke": True}).encode(),
        )
        with SessionLocal() as db:
            return await global_connector_update(
                uuid.UUID(created["id"]), request, db, actor
            )

    result = _invoke(revoke())
    assert result == {"id": created["id"], "deleted": True}
    assert _json_response(_invoke(revoke())) == result

    with SessionLocal() as db:
        connector_id = uuid.UUID(created["id"])
        assert db.query(Connector).filter(Connector.id == connector_id).count() == 0
        assert (
            db.query(ConnectorRevision)
            .filter(ConnectorRevision.connector_id == connector_id)
            .count()
            == 0
        )
        assert (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector_id)
            .count()
            == 0
        )
        cleared = (
            db.query(ProjectConnectorDefault)
            .filter(ProjectConnectorDefault.project_id == project_id)
            .one()
        )
        assert (
            cleared.connector_id,
            cleared.connector_revision,
            cleared.settings_revision,
        ) == (None, None, 5)
        events = (
            db.query(ConnectorAuditEvent)
            .filter(ConnectorAuditEvent.action.in_(("deleted", "default_cleared")))
            .all()
        )
        assert {event.action for event in events} == {"deleted", "default_cleared"}
        assert all(event.connector_id is None for event in events)
        status = global_connector_request_status(request_id, db, actor)
        assert status["status"] == "complete"
        assert status["result"] == result
        assert (
            db.query(ConnectorRequestLedger)
            .filter(ConnectorRequestLedger.connector_id.is_(None))
            .count()
            >= 1
        )


@pytest.mark.parametrize(
    ("check_result", "expected"),
    [
        (
            {"status": "valid", "message": "Brave Search accepted this API key."},
            "valid",
        ),
        (
            {"status": "invalid", "message": "Brave Search rejected this API key."},
            "invalid",
        ),
    ],
)
def test_global_connector_check_returns_only_safe_provider_status(
    connector_identity, monkeypatch, tmp_path, check_result, expected
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    with SessionLocal() as db:
        created = create_connector(
            db,
            actor=actor,
            visibility="global",
            project_id=None,
            kind="brave",
            name="Shared Brave",
            configuration_raw={"schema_version": 1, "kind": "brave"},
            api_key="synthetic-global-secret",  # rayloc:ignore
        )
        db.commit()
    from src.openship.connectors import routes

    async def fake_check(_kind, api_key):
        assert api_key == "synthetic-global-secret"  # rayloc:ignore
        return check_result["status"]

    monkeypatch.setattr(routes, "check_search_api_key", fake_check)
    request, _ = _request(
        "POST",
        f"/api/global-connectors/{created['id']}/check",
        headers={"Origin": ORIGIN},
    )
    with SessionLocal() as db:
        result = _invoke(
            global_connector_check(uuid.UUID(created["id"]), request, db, actor)
        )

    assert result["status"] == expected
    assert result["message"] == check_result["message"]
    assert "synthetic-global-secret" not in json.dumps(result)


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
    assert refused.value.detail["error"]["code"] == "connector_origin_forbidden"
    assert not origin_consumed()
    assert "synthetic-secret-sentinel" not in str(refused.value.detail)


def test_global_origin_rejection_is_definite_and_precedes_secret_body_read(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    request, consumed = _request(
        "POST",
        "/api/global-connectors",
        headers={
            "Origin": "https://attacker.example",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        content=json.dumps(_create_body("origin-secret-sentinel")).encode(),
    )

    async def create():
        with SessionLocal() as db:
            return await global_connector_create(request, db, actor)

    with pytest.raises(HTTPException) as refused:
        _invoke(create())

    assert refused.value.status_code == 403
    assert refused.value.detail["error"]["code"] == "connector_origin_forbidden"
    assert not consumed()
    assert "origin-secret-sentinel" not in str(refused.value.detail)


@pytest.mark.parametrize(
    ("unavailable_part", "expected_reason"),
    [
        ("installation", "installation_id_missing"),
        ("encryption", "encryption_key_unavailable"),
        ("hmac", "idempotency_key_unavailable"),
    ],
)
def test_setup_context_reports_only_fixed_safe_readiness_reasons(
    connector_identity, monkeypatch, tmp_path, unavailable_part, expected_reason
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    if unavailable_part == "installation":
        monkeypatch.setattr(settings, "connector_installation_id", None)
    elif unavailable_part == "encryption":
        settings.connector_encryption_key_file.write_text("encryption-secret-sentinel")
        settings.connector_encryption_key_file.chmod(0o600)
    else:
        settings.connector_idempotency_hmac_key_file.write_text("hmac-secret-sentinel")
        settings.connector_idempotency_hmac_key_file.chmod(0o600)
    response = connector_setup_context(actor)

    assert response == {
        "can_manage_global": True,
        "credential_store_ready": False,
        "unavailable_reason": expected_reason,
    }
    safe_response = json.dumps(response)
    assert "secret-sentinel" not in safe_response
    assert str(settings.connector_encryption_key_file) not in safe_response
    assert str(settings.connector_idempotency_hmac_key_file) not in safe_response


def test_setup_context_route_is_authenticated():
    route = next(
        route
        for route in connectors_router.routes
        if route.path == "/api/connectors/setup-context"
    )

    assert any(
        dependency.call is require_jwt for dependency in route.dependant.dependencies
    )


def test_setup_context_exposes_operator_capability_and_readiness_without_key_metadata(
    connector_identity, monkeypatch, tmp_path
):
    actor, _project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", uuid.uuid4())
    response = connector_setup_context(actor)

    assert response == {
        "can_manage_global": False,
        "credential_store_ready": True,
        "unavailable_reason": None,
    }
    safe_response = json.dumps(response)
    assert "test-v1" not in safe_response
    assert "connector-encryption.json" not in safe_response
    assert "connector-idempotency.json" not in safe_response


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
    assert any(
        item.id == uuid.UUID(created["id"]) and item.visibility == "global"
        for item in visible
    )
    assert "api_key" not in json.dumps(
        [item.model_dump(mode="json") for item in visible]
    )
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

    tavily_request, _ = _request(
        "POST",
        "/api/global-connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(
            _create_body("synthetic-tavily-secret", kind="tavily")
        ).encode(),
    )

    async def create_tavily():
        with SessionLocal() as db:
            return await global_connector_create(tavily_request, db, actor)

    tavily = _json_response(_invoke(create_tavily()))
    tavily_id = uuid.UUID(tavily["id"])

    async def set_connector(service, expected_revision, selected_id):
        request, _ = _request(
            "PUT",
            f"/api/projects/{project_id}/connector-defaults/{service}",
            headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            content=json.dumps(
                {"expected_revision": expected_revision, "connector_id": selected_id}
            ).encode(),
        )
        with SessionLocal() as db:
            return await project_connector_default_set(
                project_id, service, request, db, actor
            )

    selected = _invoke(set_connector("brave", 0, str(connector_id)))
    assert selected == {
        "service": "brave",
        "connector_id": str(connector_id),
        "revision": 1,
    }
    with SessionLocal() as db:
        inactive_service = project_connector_default_get(
            project_id, "tavily", db, actor
        )
    assert inactive_service == {
        "service": "tavily",
        "connector_id": None,
        "revision": 1,
    }

    switched = _invoke(set_connector("tavily", 1, str(tavily_id)))
    assert switched == {
        "service": "tavily",
        "connector_id": str(tavily_id),
        "revision": 2,
    }
    with SessionLocal() as db:
        inactive_service = project_connector_default_get(project_id, "brave", db, actor)
    assert inactive_service == {"service": "brave", "connector_id": None, "revision": 2}
    with SessionLocal() as db:
        current = project_connector_default_get(project_id, "tavily", db, actor)
    assert current == {
        "service": "tavily",
        "connector_id": str(tavily_id),
        "revision": 2,
    }

    cleared = _invoke(set_connector("tavily", 2, None))
    assert cleared == {"service": "tavily", "connector_id": None, "revision": 3}
    with SessionLocal() as db:
        current = project_connector_default_get(project_id, "brave", db, actor)
    assert current == {"service": "brave", "connector_id": None, "revision": 3}

    with pytest.raises(HTTPException) as stale:
        _invoke(set_connector("tavily", 2, str(tavily_id)))
    assert stale.value.status_code == 409


@pytest.mark.parametrize("operation", ["default", "update", "credential"])
def test_archived_project_rejects_settings_mutations(
    operation, connector_identity, monkeypatch, tmp_path
):
    actor, project_id = connector_identity
    _install_keys(monkeypatch, tmp_path)
    with SessionLocal() as db:
        create_project(db, owner_id=actor.id, name="Keep active")
        db.commit()
    connector = _seed_legacy_project_connector(actor, project_id)
    connector_id = uuid.UUID(connector["id"])

    prior_request_id = uuid.uuid4()
    prior_request, _ = _request(
        "PATCH",
        f"/api/projects/{project_id}/connectors/{connector_id}",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(prior_request_id)},
        content=json.dumps(
            {"expected_revision": 1, "name": "Before archive"}
        ).encode(),
    )

    async def update_before_archive():
        with SessionLocal() as db:
            return await project_connector_update(
                project_id, connector_id, prior_request, db, actor
            )

    assert _invoke(update_before_archive())["revision"] == 2
    with SessionLocal() as db:
        archive_project(db, owner_id=actor.id, project_id=project_id)
        db.commit()

    with SessionLocal() as db:
        visible = project_connector_list(project_id, None, db, actor)
        status = project_connector_request_status(
            project_id, prior_request_id, db, actor
        )
        assert len(visible) == 1 and visible[0].status == "configured"
        assert status["status"] == "complete"
        assert status["result"]["name"] == "Before archive"

    if operation == "default":
        path = f"/api/projects/{project_id}/connector-defaults/brave"
        payload = {"expected_revision": 0, "connector_id": None}
        route = project_connector_default_set
        args = (project_id, "brave")
    elif operation == "update":
        path = f"/api/projects/{project_id}/connectors/{connector_id}"
        payload = {"expected_revision": 2, "name": "Must not change"}
        route = project_connector_update
        args = (project_id, connector_id)
    else:
        path = f"/api/projects/{project_id}/connectors/{connector_id}/credentials"
        payload = {"expected_revision": 2, "api_key": "replacement-secret-sentinel"}
        route = project_connector_replace_credential
        args = (project_id, connector_id)

    request, _ = _request(
        "PUT" if operation == "default" else ("PATCH" if operation == "update" else "POST"),
        path,
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(payload).encode(),
    )

    async def mutate():
        with SessionLocal() as db:
            return await route(*args, request, db, actor)

    with pytest.raises(HTTPException) as refused:
        _invoke(mutate())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "project_archived"
    assert "replacement-secret-sentinel" not in str(refused.value.detail)

    monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
    global_request, _ = _request(
        "POST",
        "/api/global-connectors",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        content=json.dumps(_create_body()).encode(),
    )

    async def create_global_while_project_archived():
        with SessionLocal() as db:
            return await global_connector_create(global_request, db, actor)

    global_result = _json_response(_invoke(create_global_while_project_archived()))
    assert global_result["visibility"] == "global"

    with SessionLocal() as db:
        current = db.query(Connector).filter(Connector.id == connector_id).one()
        assert current.current_revision == 2
        assert current.name == "Before archive"
        assert db.query(ProjectConnectorDefault).filter(
            ProjectConnectorDefault.project_id == project_id
        ).count() == 0
        assert db.query(ConnectorRequestLedger).filter(
            ConnectorRequestLedger.project_id == project_id
        ).count() == 1


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
