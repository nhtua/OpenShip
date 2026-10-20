"""Real durable chat-v2 tool loop over PostgreSQL and filesystem artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _read_calls,
    _run_migrations,
    _start_api,
    _stop,
    _wait_run,
)

pytestmark = [pytest.mark.full_stack]


def _profile_configuration() -> str:
    profile = {
        "id": "synthetic-tool-model",
        "label": "Synthetic tool model",
        "adapter": "openai",
        "model": "synthetic-tool-model-v1",
        "endpoint": "https://model.example.test/v1",
        "api_version": "chat-completions",
        "credential_ref": "OPENAI_API_KEY",
        "capabilities": {
            "streaming": True,
            "usage_reporting": False,
            "context_tokens": 32768,
            "max_output_tokens": 512,
            "token_counting": False,
            "tool_calls": True,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "allowed_fallback_profile_ids": [],
        "qualified_model_aliases": [],
        "adapter_options": {
            "adapter": "openai",
            "output_limit_parameter": "max_tokens",
        },
    }
    return json.dumps([profile], separators=(",", ":"))


def _read_model_calls(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _submit_chat_turn(client: httpx.Client, conversation_id: str) -> str:
    response = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={
            "client_request_id": str(uuid.uuid4()),
            "content": "Search for the synthetic automatic chat fixture.",
            "provider_profile_id": "synthetic-tool-model",
            "fallback_profile_ids": [],
            "enable_tools": True,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _worker(api_root: Path, workdir: Path, env: dict[str, str], worker_id: str):
    return subprocess.Popen(
        [
            sys.executable,
            str(api_root / "tests/full_stack/synthetic_chat_worker.py"),
            "--worker-id",
            worker_id,
            "--log-level",
            "WARNING",
        ],
        cwd=workdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_automatic_chat_tool_is_persisted_once_across_service_restart(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("TEST_POSTGRES_URL is required for the synthetic full-stack lane")

    api_root = Path(__file__).parents[2]
    database = f"phase4_chat_{uuid.uuid4().hex[:16]}"
    database_url = _create_database(service_url, database)
    workdir = tmp_path / "process"
    workdir.mkdir()
    artifact_root = tmp_path / "shared-artifacts"
    storage_id = str(uuid.uuid4())
    search_log = tmp_path / "synthetic-search-calls.jsonl"
    model_log = tmp_path / "synthetic-model-calls.jsonl"
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase4-chat-test-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "brave_web_search,tavily_web_search",
        "BRAVE_SEARCH_API_KEY": "synthetic-brave-test-key",
        "TAVILY_API_KEY": "synthetic-tavily-test-key",
        "OPENAI_API_KEY": "synthetic-model-test-key",
        "MODEL_PROFILES_JSON": _profile_configuration(),
        "MODEL_DEFAULT_PROFILE_ID": "synthetic-tool-model",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": storage_id,
        "SYNTHETIC_SEARCH_CALL_LOG": str(search_log),
        "SYNTHETIC_MODEL_CALL_LOG": str(model_log),
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
        worker_process = _worker(api_root, workdir, env, "phase4-chat-synthetic")

        with httpx.Client(base_url=base_url, timeout=5) as client:
            suffix = uuid.uuid4().hex[:12]
            registration = client.post(
                "/api/auth/register",
                json={
                    "username": f"phase4chat_{suffix}",
                    "email": f"phase4chat_{suffix}@example.test",
                    "password": uuid.uuid4().hex,
                },
            )
            assert registration.status_code == 201
            client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            conversation = client.post(
                "/api/conversations", json={"title": "Automatic search"}
            )
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]
            run_id = _submit_chat_turn(client, conversation_id)
            run = _wait_run(client, run_id, terminal={"completed", "failed", "cancelled"})
            assert run["status"] == "completed", json.dumps(
                {"status": run.get("status"), "error": run.get("error")}, sort_keys=True
            )
            assert run["kind"] == "chat"
            assert run["graph_version"] == 2
            assert len(run["operations"]) == 1
            operation = run["operations"][0]
            assert operation["status"] == "succeeded"
            assert operation["capability_id"] == "brave_web_search"
            assert operation["capability_version"] == "1.0.0"
            assert len(operation["artifacts"]) == 1
            artifact = operation["artifacts"][0]
            artifact_id = artifact["id"]
            content = client.get(f"/api/artifacts/{artifact_id}/content")
            assert content.status_code == 200
            original_bytes = content.content
            assert b"Synthetic Brave result" in original_bytes
            assert len(_read_calls(search_log)) == 1
            assert _read_calls(search_log)[0] == {
                "provider": "api.search.brave.com",
                "query": "synthetic automatic chat search",
            }
            model_calls = _read_model_calls(model_log)
            assert len(model_calls) == 2
            assert model_calls[0]["selected_tool"] == "brave_web_search"
            assert model_calls[1]["tool_result_count"] == 1
            assert model_calls[0]["model"] == model_calls[1]["model"]

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT ordinal, model_turn_ordinal, message_json "
                        "FROM run_transcript_entries WHERE run_id = :run_id "
                        "ORDER BY ordinal"
                    ),
                    {"run_id": run_id},
                ).all()
        finally:
            engine.dispose()
        transcript = [json.loads(row.message_json) for row in rows]
        assert [row.ordinal for row in rows] == [0, 1, 2]
        assert [message["role"] for message in transcript] == ["assistant", "tool", "assistant"]
        assert transcript[0]["tool_calls"][0]["function"]["name"] == "brave_web_search"
        assert transcript[1]["name"] == "brave_web_search"
        assert transcript[2]["content"] == "The synthetic search returned one result."

        _stop(worker_process)
        worker_process = _worker(api_root, workdir, env, "phase4-chat-synthetic-restarted")
        _stop(api_process)
        api_process, base_url = _start_api(api_root, workdir, env, port)
        time.sleep(2)
        with httpx.Client(base_url=base_url, timeout=5) as restarted:
            restarted.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            snapshot = restarted.get(f"/api/conversations/{conversation_id}/snapshot")
            assert snapshot.status_code == 200
            snapshot_data = snapshot.json()
            assert any(item["id"] == artifact_id for item in snapshot_data["artifacts"])
            assert any(item["id"] == run_id for item in snapshot_data["runs"])
            restored = restarted.get(f"/api/artifacts/{artifact_id}/content")
            assert restored.status_code == 200
            assert restored.content == original_bytes
        assert len(_read_calls(search_log)) == 1
        assert len(_read_model_calls(model_log)) == 2
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, database)
