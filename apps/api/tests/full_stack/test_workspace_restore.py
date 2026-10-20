"""A database/filesystem restore keeps exact snapshots and never replays search."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _read_calls,
    _run_migrations,
    _start_api,
    _stop,
    _submit_tool,
    _wait_run,
)

pytestmark = [pytest.mark.full_stack]


def _clone_database(service_url: str, source: str, destination: str) -> None:
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{destination}"'))
            connection.execute(text(f'CREATE DATABASE "{destination}" TEMPLATE "{source}"'))
    finally:
        admin.dispose()


def test_coordinated_restore_verifies_bytes_and_does_not_reissue_search(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("TEST_POSTGRES_URL is required for the synthetic restore lane")

    api_root = Path(__file__).parents[2]
    source_database = f"phase4_restore_source_{uuid.uuid4().hex[:12]}"
    restored_database = f"phase4_restore_copy_{uuid.uuid4().hex[:12]}"
    database_url = _create_database(service_url, source_database)
    restored_url = make_url(database_url).set(database=restored_database).render_as_string(
        hide_password=False
    )
    workdir = tmp_path / "process"
    workdir.mkdir()
    artifact_root = tmp_path / "source-artifacts"
    restored_root = tmp_path / "restored-artifacts"
    storage_id = str(uuid.uuid4())
    search_log = tmp_path / "synthetic-search-calls.jsonl"
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase4-restore-test-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "brave_web_search,tavily_web_search,workspace_list_files,workspace_read_file",
        "BRAVE_SEARCH_API_KEY": "synthetic-brave-test-key",
        "TAVILY_API_KEY": "synthetic-tavily-test-key",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": storage_id,
        "SYNTHETIC_SEARCH_CALL_LOG": str(search_log),
        "WORKER_POLL_INTERVAL": "1",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_LEASE_SECONDS": "30",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "RECONCILER_INTERVAL": "5",
    }
    api_process = worker_process = None
    try:
        _run_migrations(api_root, workdir, database_url)
        initialized = subprocess.run(
            [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert initialized.returncode == 0, "artifact storage initialization failed"
        api_process, base_url = _start_api(api_root, workdir, env, port)
        worker_process = subprocess.Popen(
            [sys.executable, str(api_root / "tests/full_stack/synthetic_worker.py"), "--worker-id", "phase4-restore-source", "--log-level", "WARNING"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with httpx.Client(base_url=base_url, timeout=5) as client:
            suffix = uuid.uuid4().hex[:12]
            registration = client.post(
                "/api/auth/register",
                json={
                    "username": f"restore_{suffix}",
                    "email": f"restore_{suffix}@example.test",
                    "password": uuid.uuid4().hex,
                },
            )
            assert registration.status_code == 201
            client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            conversation = client.post("/api/conversations", json={"title": "Restore files"})
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]
            catalog = client.get("/api/capabilities")
            assert catalog.status_code == 200
            capabilities = {item["id"]: item for item in catalog.json()}
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not all(
                capabilities[item]["available"]
                for item in ("brave_web_search", "tavily_web_search")
            ):
                time.sleep(0.1)
                catalog = client.get("/api/capabilities")
                capabilities = {item["id"]: item for item in catalog.json()}
            assert capabilities["brave_web_search"]["available"] is True
            assert capabilities["tavily_web_search"]["available"] is True

            contents: dict[str, bytes] = {}
            for tool_id, query in (
                ("brave_web_search", "restore synthetic brave"),
                ("tavily_web_search", "restore synthetic tavily"),
            ):
                run_id = _submit_tool(client, conversation_id, capabilities[tool_id], query)
                run = _wait_run(client, run_id, terminal={"completed", "failed", "cancelled"})
                assert run["status"] == "completed", json.dumps(
                    {"status": run.get("status"), "error": run.get("error")}, sort_keys=True
                )
                artifact = run["operations"][0]["artifacts"][0]
                download = client.get(f"/api/artifacts/{artifact['id']}/content")
                assert download.status_code == 200
                contents[artifact["id"]] = download.content

            workspace = client.get(f"/api/conversations/{conversation_id}/workspace")
            assert workspace.status_code == 200
            assert workspace.json()["workspace"]["storage_id"] == storage_id
            assert len(workspace.json()["files"]["items"]) == 2

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT id, relative_storage_key, sha256 FROM artifacts "
                        "WHERE conversation_id = :conversation_id AND status = 'ready'"
                    ),
                    {"conversation_id": conversation_id},
                ).all()
        finally:
            engine.dispose()
        assert len(rows) == 2
        missing_id = str(rows[0].id)
        missing_key = rows[0].relative_storage_key
        digests = {str(row.id): row.sha256 for row in rows}

        # Stop writers, clone the database and copy the volume as one backup set.
        _stop(worker_process)
        worker_process = None
        _stop(api_process)
        api_process = None
        _clone_database(service_url, source_database, restored_database)
        shutil.copytree(artifact_root, restored_root)
        (restored_root / missing_key).unlink()
        env["DATABASE_URL"] = restored_url
        env["ARTIFACT_ROOT"] = str(restored_root)

        verification = subprocess.run(
            [sys.executable, "-m", "src.openship.artifacts.maintenance", "verify-restored"],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert verification.returncode == 2
        report = json.loads(verification.stdout)
        assert report["verified"] == 1
        assert report["recovered"] == 0
        assert report["unavailable"] == 1
        assert report["workspaces_available"] == 1

        api_process, restored_base_url = _start_api(api_root, workdir, env, port)
        worker_process = subprocess.Popen(
            [sys.executable, str(api_root / "tests/full_stack/synthetic_worker.py"), "--worker-id", "phase4-restore-reopened", "--log-level", "WARNING"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with httpx.Client(base_url=restored_base_url, timeout=5) as restored_client:
            restored_client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            snapshot = restored_client.get(f"/api/conversations/{conversation_id}/workspace")
            assert snapshot.status_code == 200
            files = {item["id"]: item for item in snapshot.json()["files"]["items"]}
            assert files[missing_id]["status"] == "unavailable"
            assert files[missing_id]["sha256"] == digests[missing_id]
            assert snapshot.json()["workspace"]["status"] == "available"
            missing_download = restored_client.get(f"/api/artifacts/{missing_id}/content")
            assert missing_download.status_code == 503

            for artifact_id, expected in contents.items():
                if artifact_id == missing_id:
                    continue
                available = restored_client.get(f"/api/artifacts/{artifact_id}/content")
                assert available.status_code == 200
                assert available.content == expected
        assert len(_read_calls(search_log)) == 2
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, restored_database)
        _drop_database(service_url, source_database)
