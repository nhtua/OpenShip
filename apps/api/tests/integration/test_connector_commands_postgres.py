"""Managed Connector route transactions against PostgreSQL."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from alembic import command
from src.openship.auth.models import User
from src.openship.config import settings
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    ProjectConnectorDefault,
)
from src.openship.connectors.routes import (
    global_connector_create,
    global_connector_request_status,
    project_connector_create,
    project_connector_default_get,
    project_connector_default_set,
    project_connector_list,
    project_connector_replace_credential,
    project_connector_update,
)
from src.openship.connectors.selection import ConnectorSelectionError, resolve_connector
from src.openship.connectors.service import _audit, _new_credential, detail
from src.openship.database.models import Base
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

ORIGIN = "http://localhost:5173"
SECRET = "synthetic-postgres-connector-secret"  # rayloc:ignore
GLOBAL_SECRET = "synthetic-global-secret"  # rayloc:ignore
REPLACEMENT_SECRET = "synthetic-replacement-secret"  # rayloc:ignore


def _request(method: str, path: str, request_id: uuid.UUID, payload: dict) -> Request:
    body = json.dumps(payload).encode()
    headers = [
        (b"origin", ORIGIN.encode()),
        (b"idempotency-key", str(request_id).encode()),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1234),
        "server": ("test", 80),
        "root_path": "",
    }
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return Request(scope, receive)


def _install_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, user_id: uuid.UUID) -> None:
    encryption_path = tmp_path / "connector-encryption.json"
    hmac_path = tmp_path / "connector-idempotency.json"
    for path, key in ((encryption_path, b"e" * 32), (hmac_path, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "postgres-test-v1",
                    "keys": {"postgres-test-v1": base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", user_id)
    monkeypatch.setattr(settings, "connector_browser_origins", ORIGIN)
    monkeypatch.setattr(settings, "connector_write_rate_limit_per_minute", 100)


def _cleanup(
    engine,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    second_user_id: uuid.UUID,
    second_project_id: uuid.UUID,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE connector_availability, connector_audit_events, "
                "connector_request_ledger, project_connector_defaults, "
                "connector_revisions, connector_credentials, connectors CASCADE"
            )
        )
        parameters = {"user_id": str(user_id), "project_id": str(project_id)}
        connection.execute(text("DELETE FROM projects WHERE id = :project_id"), parameters)
        connection.execute(text("DELETE FROM users WHERE id = :user_id"), parameters)
        connection.execute(
            text("DELETE FROM projects WHERE id = :project_id"),
            {"project_id": str(second_project_id)},
        )
        connection.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": str(second_user_id)},
        )


@pytest.mark.postgres
def test_connector_commands_commit_scoped_revisions_and_idempotency(monkeypatch, tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; Connector PostgreSQL checks are opt-in")

    database_url = bounded_postgres_url(service_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    api_root = Path(__file__).parents[2]
    config = Config()
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE connector_availability, connector_audit_events, "
                "connector_request_ledger, project_connector_defaults, "
                "connector_revisions, connector_credentials, connectors CASCADE"
            )
        )
    assert {"connectors", "connector_revisions", "project_connector_defaults"} <= set(
        Base.metadata.tables
    )

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    second_user_id = uuid.uuid4()
    second_project_id = uuid.uuid4()
    username = f"connector-pg-{user_id.hex[:16]}"
    second_username = f"connector-pg-{second_user_id.hex[:16]}"
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with session_factory() as db:
            actor = User(
                id=user_id,
                username=username,
                email=f"{username}@example.test",
                password_hash="unused",
            )
            project = Project(id=project_id, owner_user_id=user_id, name="Connector PG test")
            second_actor_row = User(
                id=second_user_id,
                username=second_username,
                email=f"{second_username}@example.test",
                password_hash="unused",
            )
            second_project = Project(
                id=second_project_id,
                owner_user_id=second_user_id,
                name="Connector PG second project",
            )
            db.add_all([actor, project, second_actor_row, second_project])
            db.commit()
            db.refresh(actor)

        actor = User(
            id=user_id,
            username=username,
            email=f"{username}@example.test",
            password_hash="unused",
        )
        second_actor = User(
            id=second_user_id,
            username=second_username,
            email=f"{second_username}@example.test",
            password_hash="unused",
        )
        _install_keys(monkeypatch, tmp_path, user_id)

        create_path = f"/api/projects/{project_id}/connectors"
        create_payload = {
            "kind": "brave",
            "name": "New Private Brave",
            "configuration": {"schema_version": 1, "kind": "brave"},
            "api_key": SECRET,
        }

        def create_project_connector(payload: dict, request_id: uuid.UUID):
            with session_factory() as db:
                return asyncio.run(
                    project_connector_create(
                        project_id,
                        _request("POST", create_path, request_id, payload),
                        db,
                        actor,
                    )
                )

        with pytest.raises(HTTPException) as refused:
            create_project_connector(create_payload, uuid.uuid4())
        assert refused.value.status_code == 422
        assert refused.value.detail["error"]["code"] == "connector_visibility_unsupported"
        assert SECRET not in json.dumps(refused.value.detail)

        # Simulate an immutable record admitted before the global-only policy.
        with session_factory() as db:
            legacy_connector = Connector(
                id=uuid.uuid4(),
                kind="brave",
                visibility="project",
                project_id=project_id,
                name="Historical Project Brave",
                current_revision=1,
                enabled=True,
                managed_by_user_id=user_id,
                created_by_user_id=user_id,
            )
            db.add(legacy_connector)
            db.flush()
            legacy_credential = _new_credential(
                db,
                connector=legacy_connector,
                project_id=project_id,
                api_key=SECRET,
            )
            db.flush()
            configuration = ConnectorConfiguration(kind="brave")
            db.add(
                ConnectorRevision(
                    id=uuid.uuid4(),
                    connector_id=legacy_connector.id,
                    revision=1,
                    schema_version=1,
                    visibility="project",
                    project_id=project_id,
                    configuration_json=configuration.canonical_bytes().decode("utf-8"),
                    configuration_digest=configuration.digest(),
                    credential_version_id=legacy_credential.id,
                    created_by_user_id=user_id,
                )
            )
            db.add(
                ProjectConnectorDefault(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    service="brave",
                    connector_id=legacy_connector.id,
                    connector_revision=1,
                    settings_revision=1,
                    updated_by_user_id=user_id,
                )
            )
            _audit(
                db,
                connector=legacy_connector,
                actor_user_id=user_id,
                action="created",
                revision=1,
            )
            db.commit()
            project_result = detail(db, legacy_connector)
        assert project_result["visibility"] == "project"
        assert project_result["revision"] == 1

        global_request_id = uuid.uuid4()
        global_payload = {
            "kind": "brave",
            "name": "Installation Brave",
            "configuration": {"schema_version": 1, "kind": "brave"},
            "api_key": GLOBAL_SECRET,
        }

        def create_global_connector():
            with session_factory() as db:
                return asyncio.run(
                    global_connector_create(
                        _request(
                            "POST", "/api/global-connectors", global_request_id, global_payload
                        ),
                        db,
                        actor,
                    )
                )

        global_response = create_global_connector()
        global_replay = create_global_connector()
        global_result = json.loads(global_response.body)
        global_id = uuid.UUID(global_result["id"])
        assert global_response.status_code == 201
        assert global_replay.status_code == 200
        assert json.loads(global_replay.body) == global_result
        assert global_result["visibility"] == "global"
        assert global_id != uuid.UUID(project_result["id"])
        assert GLOBAL_SECRET not in global_response.body.decode()

        with session_factory() as db:
            listed = project_connector_list(project_id, None, db, actor)
            assert {(item.visibility, item.id) for item in listed} == {
                ("project", uuid.UUID(project_result["id"])),
                ("global", global_id),
            }
            historical_default = resolve_connector(db, project_id=project_id, service="brave")
            assert historical_default.connector_id == uuid.UUID(project_result["id"])
            assert historical_default.visibility == "project"

        def set_project_default(connector_id: str | None, expected_revision: int):
            with session_factory() as db:
                return asyncio.run(
                    project_connector_default_set(
                        project_id,
                        "brave",
                        _request(
                            "PUT",
                            f"/api/projects/{project_id}/connector-defaults/brave",
                            uuid.uuid4(),
                            {
                                "expected_revision": expected_revision,
                                "connector_id": connector_id,
                            },
                        ),
                        db,
                        actor,
                    )
                )

        with pytest.raises(HTTPException) as reassignment_refused:
            set_project_default(project_result["id"], 1)
        assert reassignment_refused.value.status_code == 422
        assert (
            reassignment_refused.value.detail["error"]["code"]
            == "connector_visibility_unsupported"
        )

        default = set_project_default(global_result["id"], 1)
        assert default["revision"] == 2
        assert default["connector_id"] == global_result["id"]

        with session_factory() as db:
            second_default = asyncio.run(
                project_connector_default_set(
                    second_project_id,
                    "brave",
                    _request(
                        "PUT",
                        f"/api/projects/{second_project_id}/connector-defaults/brave",
                        uuid.uuid4(),
                        {"expected_revision": 0, "connector_id": global_result["id"]},
                    ),
                    db,
                    second_actor,
                )
            )
            assert second_default == {
                "service": "brave",
                "connector_id": global_result["id"],
                "revision": 1,
            }

        with session_factory() as db:
            selected = resolve_connector(db, project_id=project_id, service="brave")
            assert selected.connector_id == global_id
            assert selected.visibility == "global"

        update_id = uuid.uuid4()
        with session_factory() as db:
            updated = asyncio.run(
                project_connector_update(
                    project_id,
                    uuid.UUID(project_result["id"]),
                    _request(
                        "PATCH",
                        f"/api/projects/{project_id}/connectors/{project_result['id']}",
                        update_id,
                        {"expected_revision": 1, "name": "Project Brave Updated"},
                    ),
                    db,
                    actor,
                )
            )
            assert updated["revision"] == 2

        credential_id = uuid.uuid4()
        with session_factory() as db:
            replaced = asyncio.run(
                project_connector_replace_credential(
                    project_id,
                    uuid.UUID(project_result["id"]),
                    _request(
                        "POST",
                        f"/api/projects/{project_id}/connectors/{project_result['id']}/credentials",
                        credential_id,
                        {"expected_revision": 2, "api_key": REPLACEMENT_SECRET},
                    ),
                    db,
                    actor,
                )
            )
            assert replaced["revision"] == 3
            assert REPLACEMENT_SECRET not in json.dumps(replaced)

        cleared = set_project_default(None, 2)
        with session_factory() as db:
            assert cleared == {"service": "brave", "connector_id": None, "revision": 3}
            assert project_connector_default_get(project_id, "brave", db, actor) == cleared
            with pytest.raises(ConnectorSelectionError, match="connector_ambiguous"):
                resolve_connector(db, project_id=project_id, service="brave")
            assert project_connector_default_get(
                second_project_id, "brave", db, second_actor
            ) == {
                "service": "brave",
                "connector_id": global_result["id"],
                "revision": 1,
            }
            assert resolve_connector(
                db, project_id=second_project_id, service="brave"
            ).connector_id == global_id

        with session_factory() as db:
            status = global_connector_request_status(global_request_id, db, actor)
            assert status["status"] == "complete"
            assert status["result"]["id"] == global_result["id"]
            rows = db.execute(
                text(
                    "SELECT result_json FROM connector_request_ledger "
                    "WHERE actor_user_id = :user_id"
                ),
                {"user_id": user_id},
            ).scalars().all()
            assert rows
            assert all(SECRET not in (row or "") for row in rows)
            ciphertexts = db.execute(
                text("SELECT ciphertext FROM connector_credentials WHERE connector_id = :id"),
                {"id": uuid.UUID(project_result["id"])},
            ).scalars().all()
            assert len(ciphertexts) == 2
            assert all(SECRET.encode() not in bytes(value) for value in ciphertexts)
    finally:
        _cleanup(
            engine,
            user_id=user_id,
            project_id=project_id,
            second_user_id=second_user_id,
            second_project_id=second_project_id,
        )
        engine.dispose()
