"""Managed Connector lifecycle on the real API, worker, PostgreSQL and artifacts."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.config import settings
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    ProjectConnectorDefault,
)
from src.openship.connectors.service import _audit, _new_credential, detail
from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _read_calls,
    _run_migrations,
    _stop,
    _wait_for_api,
)

pytestmark = [pytest.mark.full_stack]

GLOBAL_BRAVE_V1 = "sample-brave"
GLOBAL_TAVILY_V1 = "sample-tavily"
GLOBAL_TAVILY_V2 = "sample-tavily-v2"
fixture_brave = "sample-legacy-brave"
fixture_tavily = "sample-legacy-tavily"
fixture_tavily_alt = "sample-legacy-tavily-alt"
GLOBAL_KEYS = {
    "global-brave-v1": GLOBAL_BRAVE_V1,
    "global-tavily-v1": GLOBAL_TAVILY_V1,
    "global-tavily-v2": GLOBAL_TAVILY_V2,
    "project-brave": fixture_brave,
    "project-tavily-v1": fixture_tavily,
    "project-tavily-alternate": fixture_tavily_alt,
}


def _remember(responses: list[bytes], response: httpx.Response) -> httpx.Response:
    headers = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    responses.append(response.content + b"\n" + headers.encode("utf-8"))
    return response


def _start_logged_api(api_root: Path, workdir: Path, env: dict[str, str], port: int, log: Path):
    with log.open("ab") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.openship.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=workdir,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_api(process, base_url)
    except BaseException:
        _stop(process)
        raise
    return process, base_url


def _start_logged_worker(api_root: Path, workdir: Path, env: dict[str, str], log: Path):
    with log.open("ab") as output:
        return subprocess.Popen(
            [
                sys.executable,
                str(api_root / "tests/full_stack/synthetic_worker.py"),
                "--worker-id",
                f"managed-{uuid.uuid4().hex[:12]}",
                "--log-level",
                "WARNING",
            ],
            cwd=workdir,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )


def _register(
    client: httpx.Client,
    responses: list[bytes],
    *,
    label: str,
) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    registration_value = uuid.uuid4().hex
    response = _remember(
        responses,
        client.post(
            "/api/auth/register",
            json={
                "username": f"managed-{label}-{suffix}",
                "email": f"managed-{label}-{suffix}@example.test",
                "password": registration_value,  # rayloc:ignore
            },
        ),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    return body["user"]["id"], body["access_token"]


def _owned_project(client: httpx.Client, responses: list[bytes]) -> str:
    response = _remember(responses, client.get("/api/projects"))
    assert response.status_code == 200, response.text
    projects = response.json()
    assert len(projects) == 1
    return projects[0]["id"]


def _seed_legacy_project_connector(
    database_url: str,
    *,
    actor_id: str,
    project_id: str,
    kind: str,
    name: str,
    api_key: str,
    make_default: bool = False,
) -> dict:
    """Insert an encrypted pre-Global-only record without using the create API."""
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            actor = db.get(User, uuid.UUID(actor_id))
            assert actor is not None
            connector = Connector(
                id=uuid.uuid4(),
                kind=kind,
                visibility="project",
                project_id=uuid.UUID(project_id),
                name=name,
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
                project_id=uuid.UUID(project_id),
                api_key=api_key,
            )
            db.flush()
            configuration = ConnectorConfiguration(kind=kind)
            db.add(
                ConnectorRevision(
                    id=uuid.uuid4(),
                    connector_id=connector.id,
                    revision=1,
                    schema_version=1,
                    visibility="project",
                    project_id=uuid.UUID(project_id),
                    configuration_json=configuration.canonical_bytes().decode("utf-8"),
                    configuration_digest=configuration.digest(),
                    credential_version_id=credential.id,
                    created_by_user_id=actor.id,
                )
            )
            if make_default:
                db.add(
                    ProjectConnectorDefault(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(project_id),
                        service=kind,
                        connector_id=connector.id,
                        connector_revision=1,
                        settings_revision=1,
                        updated_by_user_id=actor.id,
                    )
                )
            _audit(db, connector=connector, actor_user_id=actor.id, action="created", revision=1)
            db.commit()
            return {
                "connector": detail(db, connector),
                "credential_id": str(credential.id),
                "credential_ciphertext": bytes(credential.ciphertext),
            }
    finally:
        engine.dispose()


def _assert_run_and_artifact_scope(
    database_url: str,
    *,
    run_id: str,
    artifact_id: str,
    project_id: str,
    conversation_id: str,
) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            run = connection.execute(
                text("SELECT project_id, conversation_id FROM runs WHERE id=:id"),
                {"id": uuid.UUID(run_id)},
            ).one()
            artifact = connection.execute(
                text(
                    "SELECT project_id, conversation_id, originating_run_id "
                    "FROM artifacts WHERE id=:id"
                ),
                {"id": uuid.UUID(artifact_id)},
            ).one()
        assert str(run.project_id) == project_id
        assert str(run.conversation_id) == conversation_id
        assert str(artifact.project_id) == project_id
        assert str(artifact.conversation_id) == conversation_id
        assert str(artifact.originating_run_id) == run_id
    finally:
        engine.dispose()


def _run_browser_qualification(
    *,
    api_root: Path,
    base_url: str,
    operator_token: str,
    project_id: str,
    test_filter: str | None = None,
    excluded_filter: str | None = None,
) -> None:
    browser_env = os.environ.copy()
    browser_env.update(
        {
            "OPENSHIP_FULL_STACK_API_URL": base_url,
            "CONNECTOR_TEST_OPERATOR_TOKEN": operator_token,
            "CONNECTOR_TEST_PROJECT_ID": project_id,
        }
    )
    command = [
        "pnpm",
        "exec",
        "playwright",
        "test",
        "--config",
        "playwright.full-stack.config.ts",
        "tests/full-stack/managed-connectors.spec.ts",
        "--reporter=line",
    ]
    if test_filter:
        command.extend(["--grep", test_filter])
    if excluded_filter:
        command.extend(["--grep-invert", excluded_filter])
    result = subprocess.run(
        command,
        cwd=api_root.parent / "web",
        env=browser_env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"
    for secret in (*GLOBAL_KEYS.values(), operator_token):
        if secret:
            output = output.replace(secret, "[redacted]")
    if result.returncode != 0:
        pytest.fail(f"real-stack connector browser qualification failed:\n{output[-8000:]}")
    summary = re.search(r"(?m)^\s*(\d+) passed(?:\s+\([^)]+\))?\s*$", output)
    skipped = re.search(r"(?m)^\s*(\d+) skipped(?:\s+\([^)]+\))?\s*$", output)
    expected = 1 if test_filter else 2
    assert summary is not None, "Playwright browser result did not report a passing test count"
    assert int(summary.group(1)) == expected, "Playwright browser lane ran an unexpected test count"
    assert skipped is None or int(skipped.group(1)) == 0, "Playwright browser lane skipped a required test"


def _submit_managed(
    client: httpx.Client,
    responses: list[bytes],
    *,
    conversation_id: str,
    capability: dict,
    query: str,
    service: str,
    connector_id: str | None = None,
) -> tuple[int, dict]:
    selections = {service: connector_id} if connector_id is not None else {}
    response = _remember(
        responses,
        client.post(
            f"/api/runs/{conversation_id}/tools",
            json={
                "client_request_id": str(uuid.uuid4()),
                "capability": {
                    "id": capability["id"],
                    "version": capability["version"],
                    "manifest_digest": capability["manifest_digest"],
                },
                "arguments": {"query": query, "max_results": 3},
                "connector_selections": selections,
            },
        ),
    )
    return response.status_code, response.json()


def _wait_managed_run(
    client: httpx.Client,
    responses: list[bytes],
    run_id: str,
    *,
    terminal: set[str],
) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = _remember(responses, client.get(f"/api/runs/{run_id}"))
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in terminal:
            return run
        time.sleep(0.1)
    pytest.fail(f"managed run did not reach a terminal state: {run_id}")


def _run_and_artifact(
    client: httpx.Client,
    responses: list[bytes],
    *,
    run_id: str,
) -> tuple[dict, str, bytes]:
    run_response = _remember(responses, client.get(f"/api/runs/{run_id}"))
    assert run_response.status_code == 200, run_response.text
    run = _wait_managed_run(
        client, responses, run_id, terminal={"completed", "failed", "cancelled"}
    )
    assert run["status"] == "completed", json.dumps(run, sort_keys=True)
    artifact_id = run["operations"][0]["artifacts"][0]["id"]
    content = _remember(responses, client.get(f"/api/artifacts/{artifact_id}/content"))
    assert content.status_code == 200
    return run, artifact_id, content.content


def _assert_no_sentinels_in_database(database_url: str, sentinels: tuple[str, ...]) -> None:
    engine = create_engine(database_url)
    try:
        metadata_inspector = inspect(engine)
        with engine.connect() as connection:
            for name in metadata_inspector.get_table_names():
                reflected = Table(name, MetaData(), autoload_with=connection)
                for row in connection.execute(select(reflected)).all():
                    for value in row:
                        if isinstance(value, (bytes, bytearray, memoryview)):
                            observed = bytes(value)
                        else:
                            observed = str(value).encode("utf-8", errors="ignore")
                        for sentinel in sentinels:
                            assert sentinel.encode() not in observed, (
                                f"credential sentinel persisted in database table {name}"
                            )
    finally:
        engine.dispose()


def _assert_no_sentinels_in_files(paths: tuple[Path, ...], sentinels: tuple[str, ...]) -> None:
    for path in paths:
        files = (path,) if path.is_file() else tuple(item for item in path.rglob("*") if item.is_file())
        for file_path in files:
            content = file_path.read_bytes()
            for sentinel in sentinels:
                assert sentinel.encode() not in content, (
                    f"credential sentinel persisted in {file_path.name}"
                )


def test_managed_search_reuse_isolation_rotation_revocation_and_restart_safety(
    tmp_path, monkeypatch
):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("TEST_POSTGRES_URL is required for managed Connector full-stack tests")

    api_root = Path(__file__).parents[2]
    database = f"phase5_managed_{uuid.uuid4().hex[:12]}"
    database_url = _create_database(service_url, database)
    installation_id = str(uuid.uuid4())
    workdir = tmp_path / "process"
    workdir.mkdir()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o700)
    encryption_path = secrets_dir / "encryption.json"
    hmac_path = secrets_dir / "idempotency-hmac.json"
    for path, key_id, key in (
        (encryption_path, "enc-v1", b"e" * 32),
        (hmac_path, "hmac-v1", b"h" * 32),
    ):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": key_id,
                    "keys": {key_id: base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

    monkeypatch.setattr(settings, "connector_installation_id", uuid.UUID(installation_id))
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)

    artifact_root = tmp_path / "artifacts"
    call_log = tmp_path / "search-calls.jsonl"
    api_log = tmp_path / "api.log"
    worker_log = tmp_path / "worker.log"
    artifact_storage_id = str(uuid.uuid4())
    expected_keys = json.dumps(GLOBAL_KEYS, sort_keys=True)
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase5-managed-full-stack-test-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "brave_web_search,tavily_web_search",
        "CONNECTOR_INSTALLATION_ID": installation_id,
        "CONNECTOR_ENCRYPTION_KEY_FILE": str(encryption_path),
        "CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE": str(hmac_path),
        "CONNECTOR_DISPATCH_LOCK_FILE": str(secrets_dir / "managed-dispatch.lock"),
        "CONNECTOR_BROWSER_ORIGINS": "http://127.0.0.1:4173,http://127.0.0.1:5174",
        "CONNECTOR_OPERATOR_USER_ID": "",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": artifact_storage_id,
        "SYNTHETIC_SEARCH_CALL_LOG": str(call_log),
        "SYNTHETIC_SEARCH_CREDENTIALS_JSON": expected_keys,
        "SYNTHETIC_HOLD_SECONDS": "3",
        "WORKER_POLL_INTERVAL": "1",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_LEASE_SECONDS": "6",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "RECONCILER_INTERVAL": "2",
    }

    api_process = worker_process = None
    responses: list[bytes] = []
    retained_artifacts: dict[str, tuple[str, bytes]] = {}
    try:
        _run_migrations(api_root, workdir, database_url)
        artifact_init = subprocess.run(
            [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        assert artifact_init.returncode == 0, "test artifact store initialization failed"

        api_process, base_url = _start_logged_api(api_root, workdir, env, port, api_log)
        operator = httpx.Client(base_url=base_url, timeout=8)
        operator_id, operator_token = _register(operator, responses, label="operator")
        project_a = _owned_project(operator, responses)

        # Configure the account registered through the real API as the one
        # operator, then restart the API before any global secret is submitted.
        _stop(api_process)
        env["CONNECTOR_OPERATOR_USER_ID"] = operator_id
        api_process, base_url = _start_logged_api(api_root, workdir, env, port, api_log)
        operator.close()
        operator = httpx.Client(base_url=base_url, timeout=8)
        operator.headers["Authorization"] = f"Bearer {operator_token}"

        owner_b = httpx.Client(base_url=base_url, timeout=8)
        _owner_b_id, _owner_b_token = _register(owner_b, responses, label="consumer")
        project_b = _owned_project(owner_b, responses)

        legacy_brave = _seed_legacy_project_connector(
            database_url,
            actor_id=operator_id,
            project_id=project_a,
            kind="brave",
            name="Legacy Project Brave",
            api_key=fixture_brave,
        )
        legacy_tavily = _seed_legacy_project_connector(
            database_url,
            actor_id=operator_id,
            project_id=project_a,
            kind="tavily",
            name="Legacy Project Tavily",
            api_key=fixture_tavily,
            make_default=True,
        )
        legacy_tavily_alternate = _seed_legacy_project_connector(
            database_url,
            actor_id=operator_id,
            project_id=project_a,
            kind="tavily",
            name="Alternate Legacy Project Tavily",
            api_key=fixture_tavily_alt,
        )
        project_brave = legacy_brave["connector"]
        project_tavily = legacy_tavily["connector"]
        project_tavily_alternate = legacy_tavily_alternate["connector"]

        _run_browser_qualification(
            api_root=api_root,
            base_url=base_url,
            operator_token=operator_token,
            project_id=project_a,
            excluded_filter="unprovisioned connector setup",
        )
        global_list = _remember(responses, operator.get("/api/global-connectors"))
        assert global_list.status_code == 200, global_list.text
        global_connectors = global_list.json()
        global_brave = next(
            item for item in global_connectors if item["name"] == "Shared Brave from browser"
        )
        global_tavily = next(
            item for item in global_connectors if item["name"] == "Shared Tavily from browser"
        )
        assert global_brave["visibility"] == global_tavily["visibility"] == "global"

        operator_projects_response = _remember(responses, operator.get("/api/projects"))
        assert operator_projects_response.status_code == 200
        peer_project = next(
            project
            for project in operator_projects_response.json()
            if project["name"] == "Shared Search Peer"
        )
        project_a_peer = peer_project["id"]

        # The UI switched project A from its historical private Tavily default
        # to the shared instance. The old record and encrypted credential are
        # retained, unchanged and still scoped to project A.
        saved_legacy_cutover = _remember(
            responses,
            operator.get(f"/api/projects/{project_a}/connector-defaults/tavily"),
        )
        assert saved_legacy_cutover.status_code == 200
        assert saved_legacy_cutover.json()["connector_id"] == global_tavily["id"]
        legacy_listing = _remember(
            responses, operator.get(f"/api/projects/{project_a}/connectors")
        )
        legacy_after_cutover = next(
            item
            for item in legacy_listing.json()
            if item["id"] == project_tavily["id"]
        )
        assert legacy_after_cutover["visibility"] == "project"
        assert legacy_after_cutover["is_default"] is False
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                credential_after_cutover = connection.execute(
                    text(
                        "SELECT connector_id, ciphertext FROM connector_credentials "
                        "WHERE id=:id"
                    ),
                    {"id": uuid.UUID(legacy_tavily["credential_id"])},
                ).one()
            assert str(credential_after_cutover.connector_id) == project_tavily["id"]
            assert bytes(credential_after_cutover.ciphertext) == legacy_tavily[
                "credential_ciphertext"
            ]
        finally:
            engine.dispose()

        private_create = _remember(
            responses,
            operator.post(
                f"/api/projects/{project_a}/connectors",
                headers={
                    "Origin": "http://127.0.0.1:5174",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "kind": "brave",
                    "name": "Rejected private Brave",
                    "configuration": {"schema_version": 1, "kind": "brave"},
                    "api_key": "sample-rejected",
                },
            ),
        )
        assert private_create.status_code == 422
        assert private_create.json()["error"]["code"] == "connector_visibility_unsupported"
        assert "sample-rejected" not in private_create.text

        denied_global_create = _remember(
            responses,
            owner_b.post(
                "/api/global-connectors",
                headers={
                    "Origin": "http://127.0.0.1:5174",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "kind": "brave",
                    "name": "Rejected non-operator global connector",
                    "configuration": {},
                    "api_key": "sample-rejected",
                },
            ),
        )
        assert denied_global_create.status_code == 403
        assert denied_global_create.json()["error"]["code"] == "global_management_forbidden"

        caps_response = _remember(responses, operator.get("/api/capabilities"))
        assert caps_response.status_code == 200, caps_response.text
        capabilities = {item["id"]: item for item in caps_response.json()}
        brave = capabilities["brave_web_search"]
        tavily = capabilities["tavily_web_search"]
        conversation_a_response = _remember(
            responses,
            operator.post(
                "/api/conversations",
                json={"title": "Managed owner A", "project_id": project_a},
            ),
        )
        conversation_b_response = _remember(
            responses,
            owner_b.post(
                "/api/conversations",
                json={"title": "Managed owner B", "project_id": project_b},
            ),
        )
        assert conversation_a_response.status_code == 201
        assert conversation_b_response.status_code == 201
        conversation_a = conversation_a_response.json()["id"]
        conversation_b = conversation_b_response.json()["id"]
        peer_conversation_response = _remember(
            responses,
            operator.post(
                "/api/conversations",
                json={"title": "Managed same-owner peer", "project_id": project_a_peer},
            ),
        )
        assert peer_conversation_response.status_code == 201
        peer_conversation = peer_conversation_response.json()["id"]

        # The queued run survives an API restart before the worker sees it.
        status, queued = _submit_managed(
            owner_b,
            responses,
            conversation_id=conversation_b,
            capability=brave,
            query="global reuse project B after API restart",
            service="brave",
            connector_id=global_brave["id"],
        )
        assert status == 202, queued
        queued_run = _remember(responses, owner_b.get(f"/api/runs/{queued['id']}"))
        assert queued_run.json()["status"] == "queued"
        _stop(api_process)
        api_process, base_url = _start_logged_api(api_root, workdir, env, port, api_log)
        operator.close()
        owner_b.close()
        operator = httpx.Client(base_url=base_url, timeout=8)
        owner_b = httpx.Client(base_url=base_url, timeout=8)
        operator.headers["Authorization"] = f"Bearer {operator_token}"
        owner_b.headers["Authorization"] = f"Bearer {_owner_b_token}"
        worker_process = _start_logged_worker(api_root, workdir, env, worker_log)

        run_b, artifact_b_id, artifact_b = _run_and_artifact(
            owner_b, responses, run_id=queued["id"]
        )
        retained_artifacts[artifact_b_id] = ("owner_b", artifact_b)
        _assert_run_and_artifact_scope(
            database_url,
            run_id=queued["id"],
            artifact_id=artifact_b_id,
            project_id=project_b,
            conversation_id=conversation_b,
        )
        queued_result = run_b["operations"][0]["result"]
        assert queued_result["query"] == "global reuse project B after API restart"
        assert queued_result["items"][0]["title"] == "Synthetic Brave result"

        # One Global credential is reused across owners and across two projects
        # owned by the same user, with each run and artifact pinned to its project.
        status, run_a = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=brave,
            query="global reuse project A",
            service="brave",
            connector_id=global_brave["id"],
        )
        assert status == 202, run_a
        _completed_a, artifact_a_id, artifact_a = _run_and_artifact(
            operator, responses, run_id=run_a["id"]
        )
        retained_artifacts[artifact_a_id] = ("operator", artifact_a)
        _assert_run_and_artifact_scope(
            database_url,
            run_id=run_a["id"],
            artifact_id=artifact_a_id,
            project_id=project_a,
            conversation_id=conversation_a,
        )

        peer_brave_status, peer_brave_run = _submit_managed(
            operator,
            responses,
            conversation_id=peer_conversation,
            capability=brave,
            query="same-owner peer project Brave default",
            service="brave",
        )
        assert peer_brave_status == 202, peer_brave_run
        _peer_brave, peer_brave_artifact_id, peer_brave_artifact = _run_and_artifact(
            operator, responses, run_id=peer_brave_run["id"]
        )
        retained_artifacts[peer_brave_artifact_id] = ("operator", peer_brave_artifact)
        _assert_run_and_artifact_scope(
            database_url,
            run_id=peer_brave_run["id"],
            artifact_id=peer_brave_artifact_id,
            project_id=project_a_peer,
            conversation_id=peer_conversation,
        )

        owner_b_global_list = _remember(responses, owner_b.get("/api/global-connectors"))
        assert owner_b_global_list.status_code == 403
        denied_global_update = _remember(
            responses,
            owner_b.patch(
                f"/api/global-connectors/{global_brave['id']}",
                headers={
                    "Origin": "http://127.0.0.1:5174",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={"expected_revision": 1, "enabled": False},
            ),
        )
        assert denied_global_update.status_code == 403
        assert denied_global_update.json()["error"]["code"] == "global_management_forbidden"
        owner_b_project_list = _remember(
            responses, owner_b.get(f"/api/projects/{project_b}/connectors")
        )
        assert owner_b_project_list.status_code == 200
        assert {item["id"] for item in owner_b_project_list.json()} == {
            global_brave["id"],
            global_tavily["id"],
        }
        owner_a_project_list = _remember(
            responses, operator.get(f"/api/projects/{project_a}/connectors")
        )
        assert owner_a_project_list.status_code == 200
        assert {item["id"] for item in owner_a_project_list.json()} == {
            global_brave["id"],
            global_tavily["id"],
            project_brave["id"],
            project_tavily["id"],
            project_tavily_alternate["id"],
        }
        foreign_project_list = _remember(
            responses, owner_b.get(f"/api/projects/{project_a}/connectors")
        )
        assert foreign_project_list.status_code == 404

        status, project_brave_run = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=brave,
            query="project brave stays in owner project",
            service="brave",
            connector_id=project_brave["id"],
        )
        assert status == 202, project_brave_run
        _completed_project_brave, project_brave_artifact_id, project_brave_artifact = (
            _run_and_artifact(operator, responses, run_id=project_brave_run["id"])
        )
        retained_artifacts[project_brave_artifact_id] = ("operator", project_brave_artifact)

        # A foreign project connector is rejected at admission; the synthetic
        # provider sees no request for the rejected command.
        calls_before_foreign = len(_read_calls(call_log))
        status, foreign = _submit_managed(
            owner_b,
            responses,
            conversation_id=conversation_b,
            capability=brave,
            query="foreign project connector must not dispatch",
            service="brave",
            connector_id=project_brave["id"],
        )
        assert status == 422
        assert foreign["error"]["code"] == "connector_scope_denied"
        time.sleep(0.2)
        assert len(_read_calls(call_log)) == calls_before_foreign

        project_caps = _remember(
            responses, operator.get(f"/api/projects/{project_a}/capabilities")
        )
        assert project_caps.status_code == 200
        project_brave_status = next(
            item for item in project_caps.json() if item["id"] == "brave_web_search"
        )
        assert project_brave_status["permitted"] is True
        # Both the shared connector and retained legacy Project connector are
        # eligible, and this project has not selected a Brave default.
        assert project_brave_status["connector_readiness"]["brave"] == "ambiguous"

        # The project's Global default selects Tavily without a manual ID.
        status, tavily_run = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=tavily,
            query="project default tavily v1",
            service="tavily",
        )
        assert status == 202, tavily_run
        _completed_tavily_v1, artifact_v1_id, artifact_v1 = _run_and_artifact(
            operator, responses, run_id=tavily_run["id"]
        )
        retained_artifacts[artifact_v1_id] = ("operator", artifact_v1)
        assert next(
            call for call in _read_calls(call_log) if call["query"] == "project default tavily v1"
        )["credential_label"] == "global-tavily-v1"

        replacement = _remember(
            responses,
            operator.post(
                f"/api/global-connectors/{global_tavily['id']}/credentials",
                headers={
                    "Origin": "http://127.0.0.1:5174",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={"expected_revision": 1, "api_key": GLOBAL_TAVILY_V2},
            ),
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["revision"] == 2
        assert GLOBAL_TAVILY_V2.encode() not in replacement.content
        status, tavily_v2_run = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=tavily,
            query="project default tavily v2",
            service="tavily",
        )
        assert status == 202, tavily_v2_run
        _completed_tavily_v2, artifact_v2_id, artifact_v2 = _run_and_artifact(
            operator, responses, run_id=tavily_v2_run["id"]
        )
        retained_artifacts[artifact_v2_id] = ("operator", artifact_v2)
        assert next(
            call for call in _read_calls(call_log) if call["query"] == "project default tavily v2"
        )["credential_label"] == "global-tavily-v2"

        peer_tavily_status, peer_tavily_run = _submit_managed(
            operator,
            responses,
            conversation_id=peer_conversation,
            capability=tavily,
            query="same-owner peer project Tavily default",
            service="tavily",
        )
        assert peer_tavily_status == 202, peer_tavily_run
        _peer_tavily, peer_tavily_artifact_id, peer_tavily_artifact = _run_and_artifact(
            operator, responses, run_id=peer_tavily_run["id"]
        )
        retained_artifacts[peer_tavily_artifact_id] = ("operator", peer_tavily_artifact)
        _assert_run_and_artifact_scope(
            database_url,
            run_id=peer_tavily_run["id"],
            artifact_id=peer_tavily_artifact_id,
            project_id=project_a_peer,
            conversation_id=peer_conversation,
        )

        # Revoke globally while another owner's selected request is in flight.
        status, delayed = _submit_managed(
            owner_b,
            responses,
            conversation_id=conversation_b,
            capability=brave,
            query="hold: revoke global connector during dispatch",
            service="brave",
            connector_id=global_brave["id"],
        )
        assert status == 202, delayed
        wait_deadline = time.monotonic() + 15
        while time.monotonic() < wait_deadline:
            if any(
                call["query"] == "hold: revoke global connector during dispatch"
                for call in _read_calls(call_log)
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("synthetic provider did not receive the in-flight global request")
        revoked = _remember(
            responses,
            operator.patch(
                f"/api/global-connectors/{global_brave['id']}",
                headers={
                    "Origin": "http://127.0.0.1:5174",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={"expected_revision": 1, "revoke": True},
            ),
        )
        assert revoked.status_code == 200, revoked.text
        revoked_run = _wait_managed_run(
            owner_b,
            responses,
            delayed["id"],
            terminal={"completed", "failed", "cancelled"},
        )
        assert revoked_run["status"] == "failed"
        revoked_operation = revoked_run["operations"][0]
        assert revoked_operation["status"] == "failed"
        assert revoked_operation["error_code"] == "connector_revoked"
        assert revoked_operation["result"] is None
        assert revoked_operation["artifacts"] == []
        calls_before_revoked_retry = len(_read_calls(call_log))
        retry_status, retry = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=brave,
            query="revoked global connector must not dispatch again",
            service="brave",
            connector_id=global_brave["id"],
        )
        assert retry_status == 422
        assert retry["error"]["code"] == "connector_revoked"
        time.sleep(0.2)
        assert len(_read_calls(call_log)) == calls_before_revoked_retry

        # Kill a worker after it has sent one request. Lease recovery must not
        # replay that dispatched request on the replacement worker.
        status, crash_run = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=tavily,
            query="cancel full-stack dispatch",
            service="tavily",
            connector_id=project_tavily["id"],
        )
        assert status == 202, crash_run
        wait_deadline = time.monotonic() + 15
        while time.monotonic() < wait_deadline:
            if any(call["query"] == "cancel full-stack dispatch" for call in _read_calls(call_log)):
                break
            time.sleep(0.05)
        else:
            pytest.fail("synthetic provider did not receive the dispatched request")
        worker_process.kill()
        worker_process.wait(timeout=5)
        worker_process = _start_logged_worker(api_root, workdir, env, worker_log)
        recovered = _wait_managed_run(
            operator,
            responses,
            crash_run["id"],
            terminal={"completed", "failed", "cancelled"},
        )
        assert recovered["status"] != "completed"
        time.sleep(1)
        assert sum(
            call["query"] == "cancel full-stack dispatch" for call in _read_calls(call_log)
        ) == 1

        # Restart after successful publications and reopen the same evidence.
        _stop(api_process)
        api_process, base_url = _start_logged_api(api_root, workdir, env, port, api_log)
        operator.close()
        owner_b.close()
        operator = httpx.Client(base_url=base_url, timeout=8)
        owner_b = httpx.Client(base_url=base_url, timeout=8)
        operator.headers["Authorization"] = f"Bearer {operator_token}"
        owner_b.headers["Authorization"] = f"Bearer {_owner_b_token}"
        snapshot = _remember(
            responses, operator.get(f"/api/conversations/{conversation_a}/snapshot")
        )
        assert snapshot.status_code == 200
        for artifact_id, (owner, expected) in retained_artifacts.items():
            artifact_client = owner_b if owner == "owner_b" else operator
            if owner == "owner_b":
                denied = _remember(
                    responses, operator.get(f"/api/artifacts/{artifact_id}/content")
                )
                assert denied.status_code == 404
            else:
                denied = _remember(
                    responses, owner_b.get(f"/api/artifacts/{artifact_id}/content")
                )
                assert denied.status_code == 404
            reopened = _remember(
                responses, artifact_client.get(f"/api/artifacts/{artifact_id}/content")
            )
            assert reopened.status_code == 200
            assert reopened.content == expected

        calls = _read_calls(call_log)
        assert [call["credential_label"] for call in calls if "global reuse" in call["query"]] == [
            "global-brave-v1",
            "global-brave-v1",
        ]
        assert next(call for call in calls if call["query"] == "project default tavily v1")[
            "credential_label"
        ] == "global-tavily-v1"
        assert next(call for call in calls if call["query"] == "project default tavily v2")[
            "credential_label"
        ] == "global-tavily-v2"
        assert next(
            call for call in calls if call["query"] == "same-owner peer project Brave default"
        )["credential_label"] == "global-brave-v1"
        assert next(
            call for call in calls if call["query"] == "same-owner peer project Tavily default"
        )["credential_label"] == "global-tavily-v2"
        assert next(call for call in calls if call["query"] == "global reuse project B after API restart")[
            "credential_label"
        ] == "global-brave-v1"
        assert next(call for call in calls if call["query"] == "global reuse project A")[
            "credential_label"
        ] == "global-brave-v1"

        _stop(worker_process)
        worker_process = None
        _stop(api_process)
        api_process = None
        env["CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE"] = str(secrets_dir / "missing-hmac.json")
        api_process, base_url = _start_logged_api(api_root, workdir, env, port, api_log)
        _run_browser_qualification(
            api_root=api_root,
            base_url=base_url,
            operator_token=operator_token,
            project_id=project_a,
            test_filter="unprovisioned connector setup",
        )
        _stop(api_process)
        api_process = None

        sentinel_values = (
            GLOBAL_BRAVE_V1,
            GLOBAL_TAVILY_V1,
            GLOBAL_TAVILY_V2,
            fixture_brave,
            fixture_tavily,
            fixture_tavily_alt,
        )
        assert all(
            sentinel.encode() not in body
            for sentinel in sentinel_values
            for body in responses
        )
        _assert_no_sentinels_in_database(database_url, sentinel_values)
        _assert_no_sentinels_in_files(
            (api_log, worker_log, call_log, artifact_root), sentinel_values
        )
        call_log_bytes = call_log.read_bytes()
        assert b"global-brave-v1" in call_log_bytes
        assert b"global-tavily-v1" in call_log_bytes
        assert b"global-tavily-v2" in call_log_bytes
        assert b"project-tavily-v1" in call_log_bytes
        assert b"project-brave" in call_log_bytes
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, database)
