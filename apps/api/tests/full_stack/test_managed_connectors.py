"""Managed Connector lifecycle on the real API, worker, PostgreSQL and artifacts."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select

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

GLOBAL_BRAVE_V1 = "synthetic-global-brave-key-v1-sentinel"  # rayloc:ignore
PROJECT_BRAVE = "synthetic-project-brave-key-sentinel"  # rayloc:ignore
PROJECT_TAVILY_V1 = "synthetic-project-tavily-key-v1-sentinel"  # rayloc:ignore
PROJECT_TAVILY_V2 = "synthetic-project-tavily-key-v2-sentinel"  # rayloc:ignore
PROJECT_TAVILY_ALTERNATE = "synthetic-project-tavily-alternate-key-sentinel"  # rayloc:ignore
GLOBAL_KEYS = {
    "global-brave-v1": GLOBAL_BRAVE_V1,
    "project-brave": PROJECT_BRAVE,
    "project-tavily-v1": PROJECT_TAVILY_V1,
    "project-tavily-v2": PROJECT_TAVILY_V2,
    "project-tavily-alternate": PROJECT_TAVILY_ALTERNATE,
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


def _create_connector(
    client: httpx.Client,
    responses: list[bytes],
    *,
    path: str,
    kind: str,
    name: str,
    api_key: str,
) -> dict:
    response = _remember(
        responses,
        client.post(
            path,
            headers={
                "Origin": "http://localhost:5173",
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={
                "kind": kind,
                "name": name,
                "configuration": {"schema_version": 1, "kind": kind},
                "api_key": api_key,
            },
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def test_managed_search_reuse_isolation_rotation_revocation_and_restart_safety(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("TEST_POSTGRES_URL is required for managed Connector full-stack tests")

    api_root = Path(__file__).parents[2]
    database = f"phase5_managed_{uuid.uuid4().hex[:12]}"
    database_url = _create_database(service_url, database)
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

    artifact_root = tmp_path / "artifacts"
    call_log = tmp_path / "search-calls.jsonl"
    api_log = tmp_path / "api.log"
    worker_log = tmp_path / "worker.log"
    artifact_storage_id = str(uuid.uuid4())
    installation_id = str(uuid.uuid4())
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
        "CONNECTOR_BROWSER_ORIGINS": "http://localhost:5173",
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

        global_connector = _create_connector(
            operator,
            responses,
            path="/api/global-connectors",
            kind="brave",
            name="Shared Brave",
            api_key=GLOBAL_BRAVE_V1,
        )
        project_brave = _create_connector(
            operator,
            responses,
            path=f"/api/projects/{project_a}/connectors",
            kind="brave",
            name="Project Brave",
            api_key=PROJECT_BRAVE,
        )
        project_tavily = _create_connector(
            operator,
            responses,
            path=f"/api/projects/{project_a}/connectors",
            kind="tavily",
            name="Project Tavily",
            api_key=PROJECT_TAVILY_V1,  # rayloc:ignore
        )
        project_tavily_alternate = _create_connector(
            operator,
            responses,
            path=f"/api/projects/{project_a}/connectors",
            kind="tavily",
            name="Alternate Project Tavily",
            api_key=PROJECT_TAVILY_ALTERNATE,  # rayloc:ignore
        )
        default_response = _remember(
            responses,
            operator.put(
                f"/api/projects/{project_a}/connector-defaults/tavily",
                headers={
                    "Origin": "http://localhost:5173",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "expected_revision": 0,
                    "connector_id": project_tavily["id"],
                },
            ),
        )
        assert default_response.status_code == 200, default_response.text

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

        # The queued run survives an API restart before the worker sees it.
        status, queued = _submit_managed(
            owner_b,
            responses,
            conversation_id=conversation_b,
            capability=brave,
            query="global reuse project B after API restart",
            service="brave",
            connector_id=global_connector["id"],
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
        queued_result = run_b["operations"][0]["result"]
        assert queued_result["query"] == "global reuse project B after API restart"
        assert queued_result["items"][0]["title"] == "Synthetic Brave result"

        # One global credential is reused in both owners' distinct projects.
        status, run_a = _submit_managed(
            operator,
            responses,
            conversation_id=conversation_a,
            capability=brave,
            query="global reuse project A",
            service="brave",
            connector_id=global_connector["id"],
        )
        assert status == 202, run_a
        _completed_a, artifact_a_id, artifact_a = _run_and_artifact(
            operator, responses, run_id=run_a["id"]
        )
        retained_artifacts[artifact_a_id] = ("operator", artifact_a)

        owner_b_global_list = _remember(responses, owner_b.get("/api/global-connectors"))
        assert owner_b_global_list.status_code == 403
        owner_b_project_list = _remember(
            responses, owner_b.get(f"/api/projects/{project_b}/connectors")
        )
        assert owner_b_project_list.status_code == 200
        assert {item["id"] for item in owner_b_project_list.json()} == {
            global_connector["id"]
        }
        owner_a_project_list = _remember(
            responses, operator.get(f"/api/projects/{project_a}/connectors")
        )
        assert owner_a_project_list.status_code == 200
        assert {item["id"] for item in owner_a_project_list.json()} == {
            global_connector["id"],
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
        assert project_brave_status["connector_readiness"]["brave"] == "ambiguous"

        # The saved project default selects Tavily without a manual ID. A
        # replacement changes the active credential while preserving Connector ID.
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

        replacement = _remember(
            responses,
            operator.post(
                f"/api/projects/{project_a}/connectors/{project_tavily['id']}/credentials",
                headers={
                    "Origin": "http://localhost:5173",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={"expected_revision": 1, "api_key": PROJECT_TAVILY_V2},  # rayloc:ignore
            ),
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["revision"] == 2
        assert PROJECT_TAVILY_V2.encode() not in replacement.content
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

        # Revoke globally while another owner's selected request is in flight.
        status, delayed = _submit_managed(
            owner_b,
            responses,
            conversation_id=conversation_b,
            capability=brave,
            query="hold: revoke global connector during dispatch",
            service="brave",
            connector_id=global_connector["id"],
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
                f"/api/global-connectors/{global_connector['id']}",
                headers={
                    "Origin": "http://localhost:5173",
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
            connector_id=global_connector["id"],
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
        ] == "project-tavily-v1"
        assert next(call for call in calls if call["query"] == "project default tavily v2")[
            "credential_label"
        ] == "project-tavily-v2"
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
        sentinel_values = (
            GLOBAL_BRAVE_V1,
            PROJECT_BRAVE,
            PROJECT_TAVILY_V1,
            PROJECT_TAVILY_V2,
            PROJECT_TAVILY_ALTERNATE,
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
        assert b"project-tavily-v1" in call_log_bytes
        assert b"project-tavily-v2" in call_log_bytes
        assert b"project-brave" in call_log_bytes
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, database)
