"""Real chat journey: URL -> download -> model-written Python -> PNG -> resume.

Requires a running OpenSandbox provider (authenticated gateway on loopback), a
disposable PostgreSQL service, and a locally built plotting image. These are
explicitly enabled lanes: missing prerequisites fail, they never skip quietly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid as uuid_module
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from tests.full_stack.synthetic_sandbox_chat_worker import (
    DOWNLOAD_CSV,
    PYTHON_SOURCE,
)
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

SANDBOX_ENDPOINT = os.environ.get("OPENSHIP_SANDBOX_TEST_ENDPOINT", "http://127.0.0.1:18080")
def _runtime_settings(environ):
    return {
        "SANDBOX_IMAGE": environ.get("OPENSHIP_SANDBOX_TEST_IMAGE", "openship-python-plot:phase6b-task4"),
        "SANDBOX_PROFILE_ID": environ.get("OPENSHIP_SANDBOX_TEST_PROFILE_ID", "python311-plot"),
        "SANDBOX_DEPENDENCY_IDENTITY": environ.get(
            "OPENSHIP_SANDBOX_TEST_DEPENDENCY_IDENTITY", "matplotlib-3.9.2-numpy-2.1.0"
        ),
    }


SANDBOX_IMAGE = _runtime_settings(os.environ)["SANDBOX_IMAGE"]
DOWNLOAD_URL = "https://data.example.com/reports/sales.csv"
USER_TURN = f"Chart the CSV at {DOWNLOAD_URL} as a bar chart."


def _profile_configuration() -> str:
    profile = {
        "id": "synthetic-sandbox-model",
        "label": "Synthetic sandbox chat model",
        "adapter": "openai",
        "model": "synthetic-sandbox-model-v1",
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
        "adapter_options": {"adapter": "openai", "output_limit_parameter": "max_tokens"},
    }
    return json.dumps([profile], separators=(",", ":"))


def _worker(api_root: Path, workdir: Path, env: dict[str, str], worker_id: str):
    return subprocess.Popen(
        [
            sys.executable,
            str(api_root / "tests/full_stack/synthetic_sandbox_chat_worker.py"),
            "--worker-id",
            worker_id,
            "--log-level",
            "WARNING",
        ],
        cwd=workdir,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _run_browser_qualification(api_root: Path, base_url: str, sandbox_key: str) -> None:
    browser_env = os.environ.copy()
    browser_env.update(
        {
            "OPENSHIP_FULL_STACK_API_URL": base_url,
            "OPENSHIP_FULL_STACK_URL": "",
        }
    )
    result = subprocess.run(
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "--config",
            "playwright.full-stack.config.ts",
            "tests/full-stack/sandbox-python.spec.ts",
            "--reporter=line",
        ],
        cwd=api_root.parent / "web",
        env=browser_env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"
    if sandbox_key:
        output = output.replace(sandbox_key, "[redacted]")
    if result.returncode != 0:
        pytest.fail(f"sandbox python browser qualification failed:\n{output[-6000:]}")
    summary = re.search(r"(?m)^\s*(\d+) passed(?:\s+\([^)]+\))?\s*$", output)
    assert summary is not None, "Playwright browser result did not report a passing test count"
    assert int(summary.group(1)) == 1, "Playwright browser lane ran an unexpected test count"


def _submit_turn(client: httpx.Client, conversation_id: str, content: str) -> str:
    response = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={
            "client_request_id": str(uuid_module.uuid4()),
            "content": content,
            "provider_profile_id": "synthetic-sandbox-model",
            "fallback_profile_ids": [],
            "enable_tools": True,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_chat_download_python_chart_resume_journey(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    assert service_url, "TEST_POSTGRES_URL is required for the enabled full-stack lane"

    api_root = Path(__file__).parents[2]
    repository_root = api_root.parent.parent
    key_file = tmp_path / "openship-sandbox.key"
    protected_key = repository_root / "secrets" / "openship-sandbox-key"
    assert protected_key.is_file(), "protected OpenSandbox key file is missing"
    key_file.write_bytes(protected_key.read_bytes())
    key_file.chmod(0o600)

    # Fail fast when the authorized provider is not running: the lane must not
    # silently pass against a stub, and the sandbox transport is never stubbed.
    with httpx.Client(timeout=10) as probe:
        unauthenticated = probe.get(f"{SANDBOX_ENDPOINT}/v1/sandboxes/preflight")
        assert unauthenticated.status_code == 403, "gateway is not enforcing authentication"
        authenticated = probe.get(
            f"{SANDBOX_ENDPOINT}/v1/sandboxes/preflight",
            headers={"OPEN-SANDBOX-API-KEY": key_file.read_text().strip()},
        )
        assert authenticated.status_code == 404, "OpenSandbox provider is not answering"

    database = f"p6b_pychat_{uuid_module.uuid4().hex[:16]}"
    database_url = _create_database(service_url, database)
    workdir = tmp_path / "process"
    workdir.mkdir()
    artifact_root = tmp_path / "artifacts"
    storage_id = str(uuid_module.uuid4())
    model_log = tmp_path / "model-calls.jsonl"
    download_log = tmp_path / "download-calls.jsonl"
    sandbox_log = tmp_path / "sandbox-calls.jsonl"
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase6b-chat-python-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "download_file,python_execute",
        "OPENAI_API_KEY": "synthetic-model-test-key",
        "MODEL_PROFILES_JSON": _profile_configuration(),
        "MODEL_DEFAULT_PROFILE_ID": "synthetic-sandbox-model",
        "MODEL_MAX_TOKENS": "512",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": storage_id,
        "SANDBOX_ENABLED": "true",
        "SANDBOX_ENDPOINT": SANDBOX_ENDPOINT,
        "SANDBOX_DEPLOYMENT_ID": "phase6b-full-stack-lane",
        "SANDBOX_KEY_FILE": str(key_file),
        **_runtime_settings(os.environ),
        "SYNTHETIC_MODEL_CALL_LOG": str(model_log),
        "SYNTHETIC_DOWNLOAD_CALL_LOG": str(download_log),
        "SYNTHETIC_SANDBOX_CALL_LOG": str(sandbox_log),
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
            stderr=sys.stdout,
            timeout=30,
            check=False,
        )
        assert init.returncode == 0, "artifact storage initialization failed"
        api_process, base_url = _start_api(api_root, workdir, env, port)
        worker_process = _worker(api_root, workdir, env, "phase6b-python-chat")

        with httpx.Client(base_url=base_url, timeout=10) as client:
            suffix = uuid_module.uuid4().hex[:12]
            registration = client.post(
                "/api/auth/register",
                json={
                    "username": f"phase6b_{suffix}",
                    "email": f"phase6b_{suffix}@example.test",
                    "password": uuid_module.uuid4().hex,
                },
            )
            assert registration.status_code == 201
            client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            conversation = client.post("/api/conversations", json={"title": "Charted data"})
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]

            run_id = _submit_turn(client, conversation_id, USER_TURN)
            run = _wait_run(client, run_id, terminal={"completed", "failed", "cancelled"})
            assert run["status"] == "completed", json.dumps(
                {"status": run.get("status"), "error": run.get("error")}, sort_keys=True
            )
            assert run["kind"] == "chat"
            assert run["graph_version"] == 2
            operations = run["operations"]
            assert [item["capability_id"] for item in operations] == [
                "download_file",
                "python_execute",
            ]
            assert [item["status"] for item in operations] == ["succeeded", "succeeded"]
            download_operation, python_operation = operations

            csv_digest = hashlib.sha256(DOWNLOAD_CSV.encode("utf-8")).hexdigest()
            fetched = download_operation["result"]["artifact"]
            assert fetched["sha256"] == csv_digest
            csv_artifact_id = fetched["id"]
            csv_artifacts = [item for item in download_operation["artifacts"] if item["id"] == csv_artifact_id]
            assert len(csv_artifacts) == 1
            assert csv_artifacts[0]["media_type"] == "text/plain"
            csv_content = client.get(f"/api/artifacts/{csv_artifact_id}/content")
            assert csv_content.status_code == 200
            assert csv_content.content == DOWNLOAD_CSV.encode("utf-8")
            assert _read_calls(download_log) == [{"url": DOWNLOAD_URL, "bytes": len(DOWNLOAD_CSV.encode())}]

            executed = python_operation["result"]
            assert executed["exit_code"] == 0
            assert executed["stdout"] == "4 rows charted\n"
            assert executed["stderr"] == ""
            chart_ref = executed["outputs"]["chart.png"]
            chart_artifact_id = chart_ref["id"]
            assert chart_ref["media_type"] == "image/png"
            source_digest = hashlib.sha256(PYTHON_SOURCE.encode("utf-8")).hexdigest()
            python_artifact_ids = {item["id"]: item for item in python_operation["artifacts"]}
            assert chart_artifact_id in python_artifact_ids
            assert python_artifact_ids[chart_artifact_id]["media_type"] == "image/png"
            assert any(
                item["sha256"] == source_digest and item["media_type"] == "text/plain"
                for item in python_artifact_ids.values()
            )
            chart_content = client.get(f"/api/artifacts/{chart_artifact_id}/content")
            assert chart_content.status_code == 200
            chart_bytes = chart_content.content
            assert chart_bytes.startswith(b"\x89PNG\r\n\x1a\n")

            # The model could only reference the committed download output of
            # this run; the recorded selection names exactly that artifact.
            model_calls = _read_calls(model_log)
            assert len(model_calls) == 3
            assert [item["selected_tool"] for item in model_calls] == [
                "download_file",
                "python_execute",
                None,
            ]
            assert model_calls[0]["arguments"] == {"url": "https://data.example.com/reports/sales.csv"}
            assert model_calls[1]["arguments"]["input_ids"] == {"sales.csv": csv_artifact_id}
            assert model_calls[1]["arguments"]["output_media"] == {"chart.png": "image/png"}
            assert model_calls[2]["tool_result_count"] == 2
            assert model_calls[0]["model"] == model_calls[2]["model"]

            # Real provider traffic: one sandbox created from the pinned image
            # and exactly one command, in a fresh operation-scoped directory.
            sandbox_records = _read_calls(sandbox_log)
            creates = [item for item in sandbox_records if item["kind"] == "create"]
            commands = [item for item in sandbox_records if item["kind"] == "command"]
            assert len(creates) == 1
            assert len(commands) == 1
            runtime = _runtime_settings(os.environ)
            assert creates[0]["image"] == SANDBOX_IMAGE
            assert creates[0]["profile_id"] == runtime["SANDBOX_PROFILE_ID"]
            assert creates[0]["dependency_identity"] == runtime["SANDBOX_DEPENDENCY_IDENTITY"]
            if runtime["SANDBOX_PROFILE_ID"] != "python311-plot":
                assert creates[0]["image"] != "openship-python-plot:phase6b-task4"
            sandbox_id = creates[0]["sandbox_id"]
            assert sandbox_id
            command_directory = commands[0]["cwd"]
            assert command_directory.startswith(f"/outputs/{python_operation['id']}-")
            assert commands[0]["argv"] == ["python", f"{command_directory}/main.py"]

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                transcript_rows = connection.execute(
                    text("SELECT ordinal, message_json FROM run_transcript_entries "
                         "WHERE run_id = :run_id ORDER BY ordinal"),
                    {"run_id": run_id},
                ).all()
        finally:
            engine.dispose()
        assert [json.loads(row.message_json)["role"] for row in transcript_rows] == [
            "assistant", "tool", "assistant", "tool", "assistant",
        ]
        transcript = [json.loads(row.message_json) for row in transcript_rows]
        assert transcript[0]["tool_calls"][0]["name"] == "download_file"
        assert transcript[0]["tool_calls"][0]["provider_call_id"] == "synthetic-sandbox-call-0"
        assert transcript[2]["tool_calls"][0]["name"] == "python_execute"
        assert transcript[2]["tool_calls"][0]["provider_call_id"] == "synthetic-sandbox-call-1"
        final_text = transcript[-1]["content"]
        assert final_text.startswith("Fetched")
        assert "exit 0" in final_text

        # Durable continuation: one replay of the recorded steps, no refetch
        # and no re-executed sandbox command.
        with httpx.Client(base_url=base_url, timeout=10) as client:
            client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            continuation_request_id = str(uuid_module.uuid4())
            continuation = client.post(
                f"/api/runs/{run_id}/resume-chat-python",
                json={"mode": "last_step", "client_request_id": continuation_request_id},
            )
            assert continuation.status_code == 202, continuation.text
            continuation_run_id = continuation.json()["id"]
            assert continuation_run_id != run_id
            resumed = _wait_run(client, continuation_run_id, terminal={"completed", "failed", "cancelled"})
            assert resumed["status"] == "completed", json.dumps(
                {"status": resumed.get("status"), "error": resumed.get("error")}, sort_keys=True
            )
            assert [item["capability_id"] for item in resumed["operations"]] == [
                "download_file",
                "python_execute",
            ]
            assert [item["status"] for item in resumed["operations"]] == ["succeeded", "succeeded"]
            assert resumed["operations"][1]["result"]["outputs"]["chart.png"]["id"] == chart_artifact_id
            assert _read_calls(download_log) == [{"url": DOWNLOAD_URL, "bytes": len(DOWNLOAD_CSV.encode())}]
            assert len(_read_calls(sandbox_log)) == 2

            # Idempotent replay of the same continuation request.
            replay = client.post(
                f"/api/runs/{run_id}/resume-chat-python",
                json={"mode": "last_step", "client_request_id": continuation_request_id},
            )
            assert replay.status_code == 202, replay.text
            assert replay.json()["id"] == continuation_run_id

            model_calls = _read_calls(model_log)
            assert len(model_calls) == 6
            assert [item["selected_tool"] for item in model_calls[3:6]] == [
                "download_file",
                "python_execute",
                None,
            ]
            assert model_calls[3]["arguments"] == {"url": DOWNLOAD_URL}
            assert model_calls[4]["arguments"]["input_ids"] == {"sales.csv": csv_artifact_id}
            resumed_transcript = client.get(f"/api/conversations/{conversation_id}/messages")
            assert resumed_transcript.status_code == 200

        _stop(worker_process)
        worker_process = _worker(api_root, workdir, env, "phase6b-python-chat-restarted")
        _stop(api_process)
        api_process, base_url = _start_api(api_root, workdir, env, port)
        time.sleep(2)
        with httpx.Client(base_url=base_url, timeout=10) as restarted:
            restarted.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
            snapshot = restarted.get(f"/api/conversations/{conversation_id}/snapshot")
            assert snapshot.status_code == 200
            assert any(item["id"] == chart_artifact_id for item in snapshot.json()["artifacts"])
            assert any(item["id"] == csv_artifact_id for item in snapshot.json()["artifacts"])
            restored = restarted.get(f"/api/artifacts/{chart_artifact_id}/content")
            assert restored.status_code == 200
            assert restored.content == chart_bytes

        # Authenticated browser journey against the same live services: the
        # chart previews and downloads in the chat surface and the durable
        # continuation button.
        _run_browser_qualification(api_root, base_url, key_file.read_text().strip())
    finally:
        for process in (api_process, worker_process):
            if process is not None:
                _stop(process)
        _drop_database(service_url, database)
