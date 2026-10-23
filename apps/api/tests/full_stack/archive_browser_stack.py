"""Disposable PostgreSQL/API/worker stack for the browser archive journey."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from dotenv import dotenv_values

from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _run_migrations,
    _start_api,
    _stop,
)
from tests.full_stack.test_tool_chat import _profile_configuration, _worker


def main() -> None:
    api_root = Path(__file__).parents[2]
    configured = dotenv_values(api_root / ".env")
    service_url = os.environ.get("TEST_POSTGRES_URL") or configured.get(
        "TEST_POSTGRES_URL"
    )
    if not service_url:
        raise RuntimeError("TEST_POSTGRES_URL must name the disposable PostgreSQL service")

    database = f"archive_browser_{uuid.uuid4().hex[:16]}"
    database_url = _create_database(service_url, database)
    temporary = tempfile.TemporaryDirectory(prefix="openship-archive-browser-")
    root = Path(temporary.name)
    workdir = root / "process"
    workdir.mkdir()
    artifact_root = root / "artifacts"
    search_log = root / "synthetic-search-calls.jsonl"
    model_log = root / "synthetic-model-calls.jsonl"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "archive-browser-test-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "TOOL_ENABLED_IDS": "brave_web_search,workspace_list_files,workspace_read_file",
        "BRAVE_SEARCH_API_KEY": "synthetic-archive-brave-key",
        "OPENAI_API_KEY": "synthetic-archive-model-key",
        "MODEL_PROFILES_JSON": _profile_configuration(),
        "MODEL_DEFAULT_PROFILE_ID": "synthetic-tool-model",
        "MODEL_MAX_TOKENS": "512",
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": str(uuid.uuid4()),
        "SYNTHETIC_SEARCH_CALL_LOG": str(search_log),
        "SYNTHETIC_MODEL_CALL_LOG": str(model_log),
        "WORKER_POLL_INTERVAL": "1",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_LEASE_SECONDS": "30",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "RECONCILER_INTERVAL": "5",
    }
    api_process: subprocess.Popen | None = None
    worker_process: subprocess.Popen | None = None
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    phase = "migrations"
    failure_kind = ""

    try:
        _run_migrations(api_root, workdir, database_url)
        phase = "artifact initialization"
        initialized = subprocess.run(
            [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
            cwd=workdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if initialized.returncode != 0:
            error_classes = re.findall(
                r"([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception))",
                initialized.stderr or "",
            )
            failure_kind = error_classes[-1] if error_classes else "exit_status"
            raise RuntimeError(failure_kind)

        phase = "API startup"
        port = _free_port()
        api_process, base_url = _start_api(api_root, workdir, env, port)
        phase = "worker startup"
        worker_process = _worker(
            api_root, workdir, env, "archive-browser-synthetic-worker"
        )
        print(json.dumps({"base_url": base_url}), flush=True)
        while not stop_event.is_set():
            line = sys.stdin.readline()
            if not line or line.strip() == "stop":
                break
    except Exception as exc:  # noqa: BLE001 - sanitize startup failures for the test harness
        print(
            f"ARCHIVE_STACK_ERROR:{phase}:{failure_kind or type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1) from None
    finally:
        _stop(worker_process)
        _stop(api_process)
        _drop_database(service_url, database)
        temporary.cleanup()


if __name__ == "__main__":
    main()
