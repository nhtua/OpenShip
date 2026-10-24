"""Full API/runtime/PostgreSQL/filesystem proof with synthetic search HTTP."""

from __future__ import annotations

import configparser
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.full_stack]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_api(process: subprocess.Popen, base_url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("API process exited before becoming healthy")
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    pytest.fail("API process did not become healthy in time")


def _start_api(api_root: Path, workdir: Path, env: dict[str, str], port: int):
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.openship.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=workdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_api(process, base_url)
    return process, base_url


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_run(client: httpx.Client, run_id: str, *, terminal: set[str]) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in terminal:
            return run
        time.sleep(0.1)
    pytest.fail(f"run did not reach a terminal state: {run_id}")


def _create_database(service_url: str, database: str) -> str:
    bounded = bounded_postgres_url(service_url)
    parsed = make_url(bounded)
    database_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()
    return database_url


def _drop_database(service_url: str, database: str) -> None:
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
    finally:
        admin.dispose()


def _run_migrations(api_root: Path, workdir: Path, database_url: str) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(api_root / "alembic.ini")
    parser.set("alembic", "script_location", str(api_root / "alembic"))
    parser.set("alembic", "sqlalchemy.url", database_url.replace("%", "%%"))
    ini_path = workdir / "alembic.ini"
    with ini_path.open("w", encoding="utf-8") as output:
        parser.write(output)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), "upgrade", "head"],
        cwd=workdir,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(api_root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, "test database migration failed"


def _read_calls(call_log: Path) -> list[dict]:
    if not call_log.exists():
        return []
    return [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]


def _submit_tool(client: httpx.Client, conversation_id: str, capability: dict, query: str) -> str:
    response = client.post(
        f"/api/runs/{conversation_id}/tools",
        json={
            "client_request_id": str(uuid.uuid4()),
            "capability": {
                "id": capability["id"],
                "version": capability["version"],
                "manifest_digest": capability["manifest_digest"],
            },
            "arguments": {"query": query, "max_results": 3},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_manual_search_survives_restart_and_cancel_is_not_redispatched(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("TEST_POSTGRES_URL is required for the synthetic full-stack lane")

    api_root = Path(__file__).parents[2]
    database = f"phase4_fs_{uuid.uuid4().hex[:16]}"
    database_url = _create_database(service_url, database)
    workdir = tmp_path / "process"
    workdir.mkdir()
    artifact_root = tmp_path / "shared-artifacts"
    artifact_storage_id = str(uuid.uuid4())
    call_log = tmp_path / "synthetic-search-calls.jsonl"
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase4-full-stack-test-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "brave_web_search,tavily_web_search",
        "BRAVE_SEARCH_API_KEY": "synthetic-brave-test-key",
        "TAVILY_API_KEY": "synthetic-tavily-test-key",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": artifact_storage_id,
        "SYNTHETIC_SEARCH_CALL_LOG": str(call_log),
        "WORKER_POLL_INTERVAL": "1",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_LEASE_SECONDS": "30",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "RECONCILER_INTERVAL": "5",
    }
    api_process = worker_process = None
    try:
        _run_migrations(api_root, workdir, database_url)
        init = subprocess.run(
            [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        assert init.returncode == 0, "artifact storage initialization failed"

        api_process, base_url = _start_api(api_root, workdir, env, port)
        worker_process = subprocess.Popen(
            [sys.executable, str(api_root / "tests/full_stack/synthetic_worker.py"), "--worker-id", "phase4-full-stack", "--log-level", "WARNING"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with httpx.Client(base_url=base_url, timeout=5) as client:
            suffix = uuid.uuid4().hex[:12]
            registration_password = uuid.uuid4().hex
            registration = client.post(
                "/api/auth/register",
                json={
                    "username": f"phase4_{suffix}",
                    "email": f"phase4_{suffix}@example.test",
                    "password": registration_password,
                },
            )
            assert registration.status_code == 201
            token = registration.json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            conversation = client.post("/api/conversations", json={"title": "Search acceptance"})
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]

            # The first request validates the pinned Botocore operation catalog;
            # allow for that cold initialization on slower CI Python versions.
            capabilities_response = client.get("/api/capabilities", timeout=20)
            assert capabilities_response.status_code == 200
            capabilities = {item["id"]: item for item in capabilities_response.json()}
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not all(
                capabilities[item]["available"]
                for item in ("brave_web_search", "tavily_web_search")
            ):
                time.sleep(0.1)
                capabilities_response = client.get("/api/capabilities", timeout=20)
                capabilities = {item["id"]: item for item in capabilities_response.json()}
            assert capabilities["brave_web_search"]["available"] is True
            assert capabilities["tavily_web_search"]["available"] is True
            assert "synthetic-brave-test-key" not in capabilities_response.text
            assert "synthetic-tavily-test-key" not in capabilities_response.text

            downloaded_before_restart = {}
            for tool_id, query, title in (
                ("brave_web_search", "synthetic brave query", "Synthetic Brave result"),
                ("tavily_web_search", "synthetic tavily query", "Synthetic Tavily result"),
            ):
                run_id = _submit_tool(client, conversation_id, capabilities[tool_id], query)
                run = _wait_run(client, run_id, terminal={"completed", "failed", "cancelled"})
                assert run["status"] == "completed", json.dumps(
                    {
                        "status": run.get("status"),
                        "error": run.get("error"),
                        "operations": run.get("operations"),
                    },
                    sort_keys=True,
                )
                assert run["kind"] == "tool"
                assert run["operations"][0]["status"] == "succeeded"
                artifact = run["operations"][0]["artifacts"][0]
                content = client.get(f"/api/artifacts/{artifact['id']}/content")
                assert content.status_code == 200
                downloaded_before_restart[artifact["id"]] = content.content
                assert title.encode() in content.content
                serialized = run["operations"][0].__str__()
                assert "synthetic-brave-test-key" not in serialized
                assert "synthetic-tavily-test-key" not in serialized

            _stop(api_process)
            api_process, base_url = _start_api(api_root, workdir, env, port)
            with httpx.Client(base_url=base_url, timeout=5) as restarted:
                restarted.headers["Authorization"] = f"Bearer {token}"
                snapshot = restarted.get(f"/api/conversations/{conversation_id}/snapshot")
                assert snapshot.status_code == 200
                for artifact_id, expected_bytes in downloaded_before_restart.items():
                    restored = restarted.get(f"/api/artifacts/{artifact_id}/content")
                    assert restored.status_code == 200
                    assert restored.content == expected_bytes
                assert len(snapshot.json()["artifacts"]) == 2
                assert len(snapshot.json()["runs"]) == 2

                cancel_run = _submit_tool(
                    restarted,
                    conversation_id,
                    capabilities["brave_web_search"],
                    "cancel full-stack dispatch",
                )
                wait_deadline = time.monotonic() + 15
                while time.monotonic() < wait_deadline:
                    if any(call["query"] == "cancel full-stack dispatch" for call in _read_calls(call_log)):
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("synthetic provider transport did not receive the cancellable request")

                cancelled = restarted.post(f"/api/runs/{cancel_run}/cancel")
                assert cancelled.status_code == 200
                final = _wait_run(restarted, cancel_run, terminal={"cancelled", "completed", "failed"})
                assert final["status"] == "cancelled"

        _stop(worker_process)
        worker_process = subprocess.Popen(
            [sys.executable, str(api_root / "tests/full_stack/synthetic_worker.py"), "--worker-id", "phase4-full-stack-restarted", "--log-level", "WARNING"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        calls = _read_calls(call_log)
        assert len(calls) == 3
        assert [call["provider"] for call in calls] == [
            "api.search.brave.com",
            "api.tavily.com",
            "api.search.brave.com",
        ]
        assert calls[-1]["query"] == "cancel full-stack dispatch"
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, database)
