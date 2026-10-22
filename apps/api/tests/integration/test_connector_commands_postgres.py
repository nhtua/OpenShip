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
from src.openship.connectors.routes import (
    global_connector_create,
    project_connector_create,
    project_connector_default_get,
    project_connector_default_set,
    project_connector_list,
    project_connector_replace_credential,
    project_connector_request_status,
    project_connector_update,
)
from src.openship.connectors.selection import ConnectorSelectionError, resolve_connector
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


def _cleanup(engine, *, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
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
    username = f"connector-pg-{user_id.hex[:16]}"
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
            db.add_all([actor, project])
            db.commit()
            db.refresh(actor)

        actor = User(
            id=user_id,
            username=username,
            email=f"{username}@example.test",
            password_hash="unused",
        )
        _install_keys(monkeypatch, tmp_path, user_id)

        create_request_id = uuid.uuid4()
        create_path = f"/api/projects/{project_id}/connectors"
        create_payload = {
            "kind": "brave",
            "name": "Project Brave",
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

        created = create_project_connector(create_payload, create_request_id)
        replayed = create_project_connector(create_payload, create_request_id)
        project_result = json.loads(created.body)
        assert created.status_code == 201
        assert replayed.status_code == 200
        assert json.loads(replayed.body) == project_result
        assert project_result["visibility"] == "project"
        assert project_result["revision"] == 1
        assert SECRET not in created.body.decode()

        with pytest.raises(HTTPException) as conflict:
            create_project_connector(
                {**create_payload, "name": "Changed retry"}, create_request_id
            )
        assert conflict.value.status_code == 409
        assert "Changed retry" not in json.dumps(conflict.value.detail)

        global_request_id = uuid.uuid4()
        with session_factory() as db:
            global_response = asyncio.run(
                global_connector_create(
                    _request(
                        "POST",
                        "/api/global-connectors",
                        global_request_id,
                        {
                            "kind": "brave",
                            "name": "Installation Brave",
                            "configuration": {"schema_version": 1, "kind": "brave"},
                            "api_key": GLOBAL_SECRET,
                        },
                    ),
                    db,
                    actor,
                )
            )
        global_result = json.loads(global_response.body)
        global_id = uuid.UUID(global_result["id"])
        assert global_response.status_code == 201
        assert global_result["visibility"] == "global"
        assert GLOBAL_SECRET not in global_response.body.decode()

        with session_factory() as db:
            listed = project_connector_list(project_id, None, db, actor)
            assert {(item.visibility, item.id) for item in listed} == {
                ("project", uuid.UUID(project_result["id"])),
                ("global", global_id),
            }
            with pytest.raises(ConnectorSelectionError, match="connector_ambiguous"):
                resolve_connector(db, project_id=project_id, service="brave")

        default_id = uuid.uuid4()
        with session_factory() as db:
            default = asyncio.run(
                project_connector_default_set(
                    project_id,
                    "brave",
                    _request(
                        "PUT",
                        f"/api/projects/{project_id}/connector-defaults/brave",
                        default_id,
                        {"expected_revision": 0, "connector_id": project_result["id"]},
                    ),
                    db,
                    actor,
                )
            )
            assert default["revision"] == 1
            assert default["connector_id"] == project_result["id"]
            selected = resolve_connector(db, project_id=project_id, service="brave")
            assert selected.connector_id == uuid.UUID(project_result["id"])

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

        clear_id = uuid.uuid4()
        with session_factory() as db:
            cleared = asyncio.run(
                project_connector_default_set(
                    project_id,
                    "brave",
                    _request(
                        "PUT",
                        f"/api/projects/{project_id}/connector-defaults/brave",
                        clear_id,
                        {"expected_revision": 1, "connector_id": None},
                    ),
                    db,
                    actor,
                )
            )
            assert cleared == {"service": "brave", "connector_id": None, "revision": 2}
            assert project_connector_default_get(project_id, "brave", db, actor) == cleared
            with pytest.raises(ConnectorSelectionError, match="connector_ambiguous"):
                resolve_connector(db, project_id=project_id, service="brave")

        with session_factory() as db:
            status = project_connector_request_status(
                project_id, create_request_id, db, actor
            )
            assert status["status"] == "complete"
            assert status["result"]["id"] == project_result["id"]
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
        _cleanup(engine, user_id=user_id, project_id=project_id)
        engine.dispose()
