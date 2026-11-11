"""Continuous v5 task journeys against the real provider, gateway and store.

This is the explicit agentic-task lane: it runs only with
``OPENSHIP_FULL_STACK_AGENTIC_TASKS=1``, and then only against a running
OpenSandbox provider (authenticated gateway on loopback), a locally built
plotting image, and the operator's own sandbox profile snapshot. A missing
prerequisite fails the lane - it never skips quietly, and the sandbox
transport is never stubbed.

Synthetic boundaries: the model is a deterministic stage script (the answers in
``tests/fixtures/agentic_tasks``) and the controlled HTTPS source body is a
canned weather CSV. Everything the journeys depend on stays real: the URL
policy gate, artifact capture, PostgreSQL, the durable queue, the eleven-node
v5 task graph, the tool gateway, the OpenSandbox client transport, the artifact
store, and the account preference door.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid as uuid_module
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.runs.tasks.contracts import TaskDecisionCommand
from src.openship.runs.tasks.decisions import apply_decision
from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _run_migrations,
    _stop,
    _wait_for_api,
)

pytestmark = [pytest.mark.full_stack]

SELECTOR = os.environ.get("OPENSHIP_FULL_STACK_AGENTIC_TASKS", "")

SANDBOX_ENDPOINT = os.environ.get("OPENSHIP_SANDBOX_TEST_ENDPOINT", "http://127.0.0.1:18080")
SANDBOX_IMAGE = os.environ.get("OPENSHIP_SANDBOX_TEST_IMAGE", "openship-python-plot:phase6b-task4")
SANDBOX_PROFILE_ID = os.environ.get("OPENSHIP_SANDBOX_TEST_PROFILE_ID", "logs-data")
SANDBOX_DEPENDENCY_IDENTITY = os.environ.get(
    "OPENSHIP_SANDBOX_TEST_DEPENDENCY_IDENTITY", "matplotlib-3.9.2-numpy-2.1.0"
)

TASK_MODEL_PROFILE = "synthetic-task-model"
WEATHER_URL = "https://weather.example.test/climate/london-2026.csv"
UNSAFE_URL = "http://data.example.com/reports/sales.csv"
WEATHER_TURN = f"Chart the London monthly averages from {WEATHER_URL}"
UNREACHABLE_TURN = f"Chart the London monthly averages from the unreachable URL {UNSAFE_URL}"
LOGS_TURN = "Average the duration records in the uploaded logs per service and chart them."
BROWSER_TURN = f"Chart the London monthly averages from {WEATHER_URL} and report them."

WEATHER_CSV = (
    "city,month,temperature_c\n"
    "London,January,4\n"
    "London,January,6\n"
    "London,February,7\n"
    "London,February,9\n"
    "Paris,January,11\n"
)
LOGS_JSON = (
    '[{"service": "api", "duration_ms": 100},'
    ' {"service": "api", "duration_ms": 200},'
    ' {"service": "worker", "duration_ms": 50}]'
)
WEATHER_AVERAGES = {"January": 5, "February": 8}
LOGS_AVERAGES = {"api": 150, "worker": 50}

_API_ROOT = Path(__file__).parents[2]
_FIXTURES = _API_ROOT / "tests" / "fixtures" / "agentic_tasks"


def _fixture_plan(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


WEATHER_PLAN = _fixture_plan("weather_plan")
LOGS_PLAN = _fixture_plan("logs_plan")
DEFECTIVE_PLAN = _fixture_plan("plan_defective_notes")

PARKED = {"waiting_plan", "waiting_input", "waiting_config"}
SETTLED = {"completed", "failed", "cancelled", "timed_out"}


def _pinned_image_id(snapshot: Path, profile_id: str) -> str | None:
    """The immutable image id the operator's own snapshot pins for one profile."""
    body = snapshot.read_text(encoding="utf-8")
    block = re.search(
        rf"(?ms)^  {re.escape(profile_id)}:.*?(?=^  \S|\Z)",
        body,
    )
    if block is None:
        return None
    image_id = re.search(r"image_id: \"?(sha256:[0-9a-f]+)\"?", block.group(0))
    return image_id.group(1) if image_id else None


def _profile_configuration() -> str:
    profile = {
        "id": TASK_MODEL_PROFILE,
        "label": "Synthetic agentic task model",
        "adapter": "openai",
        "model": "synthetic-task-model-v1",
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
            "structured_output": True,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "allowed_fallback_profile_ids": [],
        "qualified_model_aliases": [],
        "adapter_options": {"adapter": "openai", "output_limit_parameter": "max_tokens"},
    }
    return json.dumps([profile], separators=(",", ":"))


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class _Client(httpx.Client):
    """One account's view of the API, with its bearer already attached."""

    def __init__(
        self,
        base_url: str,
        tag: str,
        *,
        username: str | None = None,
        secret: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url, timeout=20)
        self.principal_id = None
        self.username = username
        self.password = secret
        self._login(tag)

    def _login(self, tag: str) -> None:
        name = self.username or f"agentic_{tag}_{uuid_module.uuid4().hex[:8]}"
        secret = self.password or uuid_module.uuid4().hex
        self.password = secret
        registration = self.post(
            "/api/auth/register",
            json={
                "username": name,
                "email": f"agentic_{tag}_{uuid_module.uuid4().hex[:8]}@example.test",
                "password": secret,
            },
        )
        self.username = name
        assert registration.status_code == 201, registration.text
        self.principal_id = registration.json()["user"]["id"]
        self.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"

    def conversation(self, title: str) -> str:
        created = self.post("/api/conversations", json={"title": title})
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def preferences(self) -> tuple[str, int]:
        response = self.get("/api/auth/preferences")
        assert response.status_code == 200, response.text
        body = response.json()
        return body["agent_mode"], body["revision"]

    def set_mode(self, mode: str, expected_revision: int) -> httpx.Response:
        return self.patch(
            "/api/auth/preferences",
            json={"agent_mode": mode, "expected_revision": expected_revision},
        )

    def turn(
        self,
        conversation_id: str,
        content: str,
        *,
        mode: tuple[str, int] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> str:
        payload = {
            "client_request_id": str(uuid_module.uuid4()),
            "content": content,
            "provider_profile_id": TASK_MODEL_PROFILE,
            "fallback_profile_ids": [],
            "enable_tools": True,
        }
        if mode is not None:
            payload["agent_mode"], payload["agent_mode_revision"] = mode
        if artifact_ids is not None:
            payload["artifact_ids"] = artifact_ids
        response = self.post(f"/api/runs/{conversation_id}/turns", json=payload)
        assert response.status_code == 202, response.text
        return response.json()["id"]

    def task(self, run_id: str) -> dict:
        response = self.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def pending(self, run_id: str) -> dict | None:
        run = self.task(run_id)
        decision = run["task"]["pending_decision"]
        return decision

    def decide(self, run_id: str, command: dict) -> httpx.Response:
        return self.post(f"/api/runs/{run_id}/task-decisions", json=command)

    def open_command(self, run_id: str, action: str, answer=None) -> dict:
        """The command for exactly the decision this run is waiting on."""
        pending = self.pending(run_id)
        assert pending, "the run is not waiting on a decision"
        command = {
            "client_request_id": str(uuid_module.uuid4()),
            "request_id": pending["request_id"],
            "plan_revision": pending["plan_revision"],
            "expected_digest": pending["expected_digest"],
            "action": action,
        }
        if answer is not None:
            command["answer"] = answer
        return command

    def drain(self, run_id: str, timeout: float = 90.0) -> dict:
        """Poll the durable run until it parks, finishes or refuses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.task(run_id)
            if run["status"] in PARKED or run["status"] in SETTLED:
                return run
            time.sleep(0.4)
        pytest.fail(f"run {run_id} never reached a readable state in time")

    def resume(self, run_id: str, mode: str = "last_step") -> httpx.Response:
        return self.post(
            f"/api/runs/{run_id}/resume-chat-python",
            json={"mode": mode, "client_request_id": str(uuid_module.uuid4())},
        )

    def content(self, artifact_id: str) -> bytes:
        response = self.get(f"/api/artifacts/{artifact_id}/content")
        assert response.status_code == 200, response.text
        return response.content


def _model_log(lane: dict) -> Path:
    return Path(lane["env"]["SYNTHETIC_MODEL_CALL_LOG"])


def _sandbox_log(lane: dict) -> Path:
    return Path(lane["env"]["SYNTHETIC_SANDBOX_CALL_LOG"])


def _download_log(lane: dict) -> Path:
    return Path(lane["env"]["SYNTHETIC_DOWNLOAD_CALL_LOG"])


def _mark(lane: dict) -> int:
    return len(_read_records(_model_log(lane)))


def _model_since(lane: dict, mark: int) -> list[dict]:
    return _read_records(_model_log(lane))[mark:]


# --- Process and lane helpers -------------------------------------------------


def _start_worker(lane: dict, worker_id: str) -> subprocess.Popen:
    """Launch the durable worker against the same services the API serves."""
    process = subprocess.Popen(
        [
            sys.executable,
            str(_API_ROOT / "tests/full_stack/synthetic_task_worker.py"),
            "--worker-id",
            worker_id,
            "--log-level",
            "WARNING",
        ],
        cwd=lane["workdir"],
        env={**lane["env"], "SYNTHETIC_FAULT_MARKER": lane["fault_marker"]},
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    lane["processes"].append(process)
    return process


def _kill(process: subprocess.Popen) -> None:
    """Stop one worker the operator's own stop signal stops.

    A worker that is inside a graph invoke can outlast a polite signal, so the
    stop escalates: the lane's crash legs are only meaningful when exactly one
    worker reads the queue, and a straggler would claim the next run.
    """
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    if process.poll() is None:
        process.kill()
    deadline = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    assert process.poll() is not None, "a worker outlived both stop signals"


def _wait_exit(process: subprocess.Popen, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    return process.poll()


def _arm_fault(lane: dict, phase: str) -> None:
    """The one controlled fault point: this worker dies at a named phase."""
    Path(lane["fault_marker"]).write_text(phase, encoding="utf-8")


def _disarm_fault(lane: dict) -> None:
    Path(lane["fault_marker"]).write_text("", encoding="utf-8")


def _crash_worker(lane: dict, worker_id: str, phase: str) -> int:
    """One worker that dies at the named phase and returns its exit status."""
    _arm_fault(lane, phase)
    process = _start_worker(lane, worker_id)
    code = _wait_exit(process, 180.0)
    assert code not in (0, None), "the crashing worker did not die as designed"
    _disarm_fault(lane)
    return code


def _restart_worker(lane: dict, worker_id: str) -> subprocess.Popen:
    """A restarted worker: the next one to read the durable records."""
    return _start_worker(lane, worker_id)


def _start_task_api(workdir: Path, env: dict[str, str], port: int):
    """The real API process, entered through the profile-mount entry.

    The application itself is untouched: this is uvicorn serving ``src.openship.main:app``
    after mounting the operator's profile snapshot at the location the runtime
    reads (see ``task_profile_mount``), which the fixed container path would
    provide on a deployment.
    """
    process = subprocess.Popen(
        [
            sys.executable,
            str(_API_ROOT / "tests/full_stack/synthetic_task_api.py"),
            "--port",
            str(port),
        ],
        cwd=workdir,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_api(process, base_url)
    return process, base_url


@pytest.fixture(scope="session")
def lane(tmp_path_factory):
    """One stack for the whole lane: real services, one disposable database."""
    if not SELECTOR:
        pytest.skip("the agentic task full-stack lane is not enabled")

    service_url = os.environ.get("TEST_POSTGRES_URL")
    assert service_url, "TEST_POSTGRES_URL is required for the enabled agentic lane"

    repository_root = _API_ROOT.parent.parent
    protected_key = repository_root / "secrets" / "openship-sandbox-key"
    assert protected_key.is_file(), "protected OpenSandbox key file is missing"
    operator_snapshot = Path(os.environ["SYNTHETIC_PROFILE_SNAPSHOT"])
    assert operator_snapshot.is_file(), "operator profile snapshot is missing"

    root = tmp_path_factory.mktemp("agentic-lane")
    scratch = root / "process"
    scratch.mkdir()
    artifact_root = root / "artifacts"

    # Fail fast when the authorized provider is not running: the lane must not
    # silently pass against a stub, and the sandbox transport is never stubbed.
    with httpx.Client(timeout=10) as probe:
        unauthenticated = probe.get(f"{SANDBOX_ENDPOINT}/v1/sandboxes/preflight")
        assert unauthenticated.status_code == 403, "gateway is not enforcing authentication"
        authenticated = probe.get(
            f"{SANDBOX_ENDPOINT}/v1/sandboxes/preflight",
            headers={"OPEN-SANDBOX-API-KEY": protected_key.read_text().strip()},
        )
        assert authenticated.status_code == 404, "OpenSandbox provider is not answering"

    key_path = root / "openship-sandbox.key"
    key_path.write_bytes(protected_key.read_bytes())
    key_path.chmod(0o600)

    snapshot = root / "sandbox-profiles.yaml"
    snapshot.write_bytes(operator_snapshot.read_bytes())

    database = f"p7a_tasks_{uuid_module.uuid4().hex[:16]}"
    database_url = _create_database(service_url, database)
    storage_id = str(uuid_module.uuid4())
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_API_ROOT),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "JWT_SECRET": "phase7a-agentic-task-signing-key",
        "JWT_EXPIRY_HOURS": "24",
        "MODEL_PROFILES_JSON": _profile_configuration(),
        "MODEL_DEFAULT_PROFILE_ID": TASK_MODEL_PROFILE,
        "MODEL_MAX_TOKENS": "512",
        "OPENAI_API_KEY": "synthetic-model-test-key",
        "TOOL_ENABLED_IDS": "download_file,python_execute",
        "AGENTIC_TASKS_ENABLED": "true",
        "AGENTIC_TASK_PROFILE_IDS": TASK_MODEL_PROFILE,
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": storage_id,
        "SANDBOX_ENABLED": "true",
        "SANDBOX_ENDPOINT": SANDBOX_ENDPOINT,
        "SANDBOX_DEPLOYMENT_ID": "phase7a-agentic-lane",
        "SANDBOX_KEY_FILE": str(key_path),
        "SANDBOX_IMAGE": SANDBOX_IMAGE,
        "SANDBOX_PROFILE_ID": SANDBOX_PROFILE_ID,
        "SANDBOX_DEPENDENCY_IDENTITY": SANDBOX_DEPENDENCY_IDENTITY,
        "SYNTHETIC_PROFILE_SNAPSHOT": str(snapshot),
        "SYNTHETIC_MODEL_CALL_LOG": str(root / "model-calls.jsonl"),
        "SYNTHETIC_DOWNLOAD_CALL_LOG": str(root / "download-calls.jsonl"),
        "SYNTHETIC_SANDBOX_CALL_LOG": str(root / "sandbox-calls.jsonl"),
        "WORKER_POLL_INTERVAL": "1",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_LEASE_SECONDS": "30",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        # The lane's crash legs re-admit through the run's own queue record,
        # so the lease reconciler must not race that re-admission: its tick is
        # outside the lane, and no journey here depends on a reclaimed lease.
        "RECONCILER_INTERVAL": "3000",
    }

    _run_migrations(_API_ROOT, scratch, database_url)
    init = subprocess.run(
        [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
        cwd=scratch,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=sys.stdout,
        timeout=30,
        check=False,
    )
    assert init.returncode == 0, "artifact storage initialization failed"

    api_process, base_url = _start_task_api(scratch, env, port)
    holder = {
        "base_url": base_url,
        "database_url": database_url,
        "env": env,
        "workdir": scratch,
        "root": root,
        "snapshot": snapshot,
        "fault_marker": str(root / "synthetic-fault.marker"),
        "processes": [],
    }
    try:
        yield holder
    finally:
        for process in holder["processes"]:
            _kill(process)
        _stop(api_process)
        _drop_database(service_url, database)


def _lost_approval(lane: dict, principal_id: str, run_id: str, command: dict) -> None:
    """An approval whose transaction never reached this database.

    The command is the one this account would send, applied through the API's
    own decision code and then rolled back - the only thing missing is the
    commit, which is what a lost worker leaves.
    """
    engine = create_engine(lane["database_url"])
    Session = sessionmaker(bind=engine, autoflush=False)
    try:
        with Session() as db:
            apply_decision(
                db,
                principal_id=uuid_module.UUID(principal_id),
                run_id=run_id,
                command=TaskDecisionCommand.model_validate(command),
                now=datetime.now(UTC),
            )
            db.rollback()
    finally:
        engine.dispose()


def _task_of(run: dict) -> dict:
    task = run["task"]
    assert task, "the run reported no task view"
    return task


def _step_status(task: dict, step_id: str) -> str:
    for step in task["steps"]:
        if step["id"] == step_id:
            return step["status"]
    raise AssertionError(f"step {step_id} is not in the plan the run published")


def _operation_pairs(run: dict) -> list[tuple[str, str]]:
    return [(item["capability_id"], item["status"]) for item in run["operations"]]


def _artifact_json(client: _Client, artifact_ref: dict) -> dict:
    return json.loads(client.content(artifact_ref["id"]))


def _png_size(content: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(content)) as image:
        return image.size


def _upload(client: _Client, conversation_id: str, body: bytes, name: str) -> str:
    """Put one JSON file into the conversation, as an account would attach."""
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    response = client.post(
        f"/api/conversations/{conversation_id}/artifacts",
        content=body,
        headers={
            "Idempotency-Key": str(uuid_module.uuid4()),
            "Digest": f"sha-256={digest}",
            "X-Artifact-Name": name,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _sandbox_counts(lane: dict, since: int) -> tuple[list[dict], list[dict]]:
    records = _read_records(_sandbox_log(lane))[since:]
    return (
        [item for item in records if item["kind"] == "create"],
        [item for item in records if item["kind"] == "command"],
    )


def _download_count(lane: dict, since: int) -> int:
    return len(_read_records(_download_log(lane))[since:])


def _stop_all(lane: dict) -> None:
    """End every worker this journey started, as the operator's stop does.

    A journey's worker never carries over to the next one: the lane's crash
    legs need exactly one live reader of the queue at a time.
    """
    for process in lane["processes"]:
        _kill(process)
    lane["processes"].clear()


def _sandbox_since(lane: dict) -> int:
    return len(_read_records(_sandbox_log(lane)))


def _download_since(lane: dict) -> int:
    return len(_read_records(_download_log(lane)))


# --- The journeys -------------------------------------------------------------


def test_weather_task_copilot_and_auto(lane):
    """The reviewed plan gate holds in Copilot and never fires in Auto."""
    client = _Client(lane["base_url"], "weather")
    mode, revision = client.preferences()
    assert (mode, revision) == ("copilot", 1), "the account starts in Copilot"
    conversation = client.conversation("London weather")

    model_mark = _mark(lane)
    download_since = _download_since(lane)
    sandbox_since = _sandbox_since(lane)
    run_id = client.turn(conversation, WEATHER_TURN, mode=("copilot", revision))
    _start_worker(lane, "phase7a-weather-copilot")
    parked = client.drain(run_id)
    assert parked["status"] == "waiting_plan", parked["status"]
    task = _task_of(parked)
    assert task["mode"] == "copilot"
    assert task["plan"] and task["plan"]["revision"] == 1
    assert [item["id"] for item in task["plan"]["steps"]] == ["fetch", "chart"]
    pending = task["pending_decision"]
    assert pending and pending["kind"] == "plan_confirmation"

    # The known field defect is refused by the stage, and the corrected
    # envelope is a second real answer - the run records both turns.
    calls = _model_since(lane, model_mark)
    plans = [entry for entry in calls if entry["kind"] == "plan"]
    assert len(plans) == 2
    assert "notes" in json.loads(plans[0]["answer"])["plan"]
    assert json.loads(plans[1]["answer"]) == WEATHER_PLAN

    # Nothing ran before the gate: no source fetch, no sandbox command.
    assert _download_count(lane, download_since) == 0
    creates, commands = _sandbox_counts(lane, sandbox_since)
    assert (len(creates), len(commands)) == (0, 0)

    confirmed = client.decide(run_id, client.open_command(run_id, "confirm"))
    assert confirmed.status_code == 200, confirmed.text
    finished = client.drain(run_id)
    assert finished["status"] == "completed"
    assert _operation_pairs(finished) == [
        ("download_file", "succeeded"),
        ("python_execute", "succeeded"),
    ]
    copilot_confirmations = 1 if finished["task"]["last_disposition"] == "none" else 0

    download_operation, python_operation = finished["operations"]
    csv = download_operation["result"]["artifact"]
    assert csv["sha256"] == hashlib.sha256(WEATHER_CSV.encode("utf-8")).hexdigest()
    assert client.content(csv["id"]) == WEATHER_CSV.encode("utf-8")

    averages = _artifact_json(client, python_operation["result"]["outputs"]["averages.json"])
    assert averages == WEATHER_AVERAGES
    chart = client.content(python_operation["result"]["outputs"]["chart.png"]["id"])
    assert chart.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = _png_size(chart)
    assert width > 0 and height > 0
    verified_links = finished["task"]["verified_outputs"]
    verified_ids = {str(item["id"]) for item in verified_links}
    assert verified_ids >= {
        csv["id"],
        python_operation["result"]["outputs"]["averages.json"]["id"],
        python_operation["result"]["outputs"]["chart.png"]["id"],
    }

    creates, commands = _sandbox_counts(lane, sandbox_since)
    assert (len(creates), len(commands)) == (1, 1)
    pinned_image = _pinned_image_id(lane["snapshot"], SANDBOX_PROFILE_ID)
    assert pinned_image and creates[0]["image"] == pinned_image
    assert _download_count(lane, download_since) == 1

    # The same account switches its preference; the stale pair it carried is
    # refused, and only the server's current pair admits the next task.
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    assert (switched.json()["agent_mode"], switched.json()["revision"]) == ("auto", 2)
    stale = client.post(
        f"/api/runs/{conversation}/turns",
        json={
            "client_request_id": str(uuid_module.uuid4()),
            "content": WEATHER_TURN,
            "provider_profile_id": TASK_MODEL_PROFILE,
            "fallback_profile_ids": [],
            "enable_tools": True,
            "agent_mode": "copilot",
            "agent_mode_revision": revision,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "stale_agent_preference"

    auto_run_id = client.turn(conversation, WEATHER_TURN, mode=("auto", 2))
    assert auto_run_id != run_id
    auto_finished = client.drain(auto_run_id)
    assert auto_finished["status"] == "completed"
    assert _task_of(auto_finished)["mode"] == "auto"
    assert _task_of(auto_finished)["pending_decision"] is None
    auto_confirmations = 1 if auto_finished["status"] in PARKED else 0
    assert copilot_confirmations == 1 and auto_confirmations == 0
    _stop_all(lane)


def test_invalid_source_is_honest_blocker(lane):
    """A source the policy refuses is reported as a blocker, never charted around."""
    client = _Client(lane["base_url"], "invalid-source")
    _, revision = client.preferences()
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    conversation = client.conversation("Unreachable source")

    download_since = _download_since(lane)
    sandbox_since = _sandbox_since(lane)
    run_id = client.turn(conversation, UNREACHABLE_TURN, mode=("auto", 2))
    _start_worker(lane, "phase7a-invalid-source")
    finished = client.drain(run_id)
    assert finished["status"] == "failed", finished["status"]

    # The policy gate refuses the source before any bytes are read: nothing is
    # downloaded, nothing is charted, and no operation escapes unsettled.
    assert _download_count(lane, download_since) == 0
    creates, commands = _sandbox_counts(lane, sandbox_since)
    assert (len(creates), len(commands)) == (0, 0)
    assert _operation_pairs(finished) == [("download_file", "failed")]


def test_uploaded_logs_are_not_weather_special_case(lane):
    """Uploaded records go through the same plan/tool/profile door as a URL."""
    client = _Client(lane["base_url"], "logs")
    _, revision = client.preferences()
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    conversation = client.conversation("Uploaded logs")
    artifact_id = _upload(client, conversation, LOGS_JSON.encode("utf-8"), "runs.json")
    assert client.content(artifact_id) == LOGS_JSON.encode("utf-8")

    download_since = _download_since(lane)
    sandbox_since = _sandbox_since(lane)
    run_id = client.turn(
        conversation,
        f"{LOGS_TURN} Uploaded log: {artifact_id}",
        mode=("auto", 2),
        artifact_ids=[artifact_id],
    )
    _start_worker(lane, "phase7a-logs")
    finished = client.drain(run_id)
    assert finished["status"] == "completed"
    task = _task_of(finished)
    assert [item["id"] for item in task["plan"]["steps"]] == ["analyze"]
    assert _operation_pairs(finished) == [("python_execute", "succeeded")]
    assert _download_count(lane, download_since) == 0

    operation = finished["operations"][0]
    assert sorted(operation["result"]["outputs"]) == ["averages.json", "chart.png"]
    averages = _artifact_json(client, operation["result"]["outputs"]["averages.json"])
    assert averages == LOGS_AVERAGES
    chart = client.content(operation["result"]["outputs"]["chart.png"]["id"])
    width, height = _png_size(chart)
    assert width > 0 and height > 0

    creates, commands = _sandbox_counts(lane, sandbox_since)
    assert (len(creates), len(commands)) == (1, 1)
    pinned_image = _pinned_image_id(lane["snapshot"], SANDBOX_PROFILE_ID)
    assert creates[0]["image"] == pinned_image
    _stop_all(lane)


def test_uncertain_task_requires_explicit_recovery_in_both_modes(lane):
    """A possible dispatch parks a diagnosis in Copilot and in Auto alike."""
    # Copilot: the crash is of authorized work (plan already confirmed); abort
    # cancels the rest without replaying the download.
    copilot = _Client(lane["base_url"], "uncertain-copilot")
    current, revision = copilot.preferences()
    assert current == "copilot", "a fresh account starts in Copilot"
    copilot_conversation = copilot.conversation("Weather with a lost worker")
    copilot_download = _download_since(lane)
    copilot_sandbox = _sandbox_since(lane)
    copilot_run = copilot.turn(copilot_conversation, WEATHER_TURN, mode=("copilot", revision))
    _start_worker(lane, "phase7a-uncertain-copilot-gate")
    parked_plan = copilot.drain(copilot_run)
    assert parked_plan["status"] == "waiting_plan", parked_plan["status"]
    confirmed = copilot.decide(copilot_run, copilot.open_command(copilot_run, "confirm"))
    assert confirmed.status_code == 200, confirmed.text
    _stop_all(lane)
    _crash_worker(lane, "phase7a-uncertain-copilot", "create")
    mid = copilot.task(copilot_run)
    assert mid["status"] == "running", mid["status"]
    assert _task_of(mid)["pending_decision"] is None
    assert _operation_pairs(mid) == [
        ("download_file", "succeeded"),
        ("python_execute", "running"),
    ]
    reopened = copilot.resume(copilot_run, "last_step")
    assert reopened.status_code == 202, reopened.text
    assert reopened.json()["id"] == copilot_run
    worker = _restart_worker(lane, "phase7a-uncertain-copilot-restarted")
    parked = copilot.drain(copilot_run)
    _kill(worker)
    assert parked["status"] == "waiting_input", parked["status"]
    pending = _task_of(parked)["pending_decision"]
    assert pending and pending["kind"] == "recovery"
    diagnosis = pending["diagnosis"]
    assert diagnosis and diagnosis["step_id"] == "chart"
    assert diagnosis["attempt"] == 1
    assert diagnosis["eligible_actions"]
    _, commands = _sandbox_counts(lane, copilot_sandbox)
    assert len(commands) == 0
    aborted = copilot.decide(copilot_run, copilot.open_command(copilot_run, "abort"))
    assert aborted.status_code == 200, aborted.text
    abort_worker = _restart_worker(lane, "phase7a-abort")
    aborted_run = copilot.drain(copilot_run)
    _kill(abort_worker)
    assert aborted_run["status"] in {"cancelled", "failed"}, aborted_run["status"]
    assert _download_count(lane, copilot_download) == 1
    aborted_creates, aborted_commands = _sandbox_counts(lane, copilot_sandbox)
    assert (len(aborted_creates), len(aborted_commands)) == (0, 0)

    # Auto: repeat authorizes one new operation for the same step; the
    # completed download is kept, never replayed.
    auto = _Client(lane["base_url"], "uncertain-auto")
    current, revision = auto.preferences()
    assert current == "copilot"
    switched = auto.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    assert (switched.json()["agent_mode"], switched.json()["revision"]) == ("auto", 2)
    auto_conversation = auto.conversation("Weather with a lost worker")
    auto_download = _download_since(lane)
    auto_sandbox = _sandbox_since(lane)
    auto_run = auto.turn(auto_conversation, WEATHER_TURN, mode=("auto", 2))
    _crash_worker(lane, "phase7a-uncertain-auto", "create")
    mid = auto.task(auto_run)
    assert mid["status"] == "running", mid["status"]
    assert _task_of(mid)["pending_decision"] is None
    assert _operation_pairs(mid) == [
        ("download_file", "succeeded"),
        ("python_execute", "running"),
    ]
    reopened = auto.resume(auto_run, "last_step")
    assert reopened.status_code == 202, reopened.text
    worker = _restart_worker(lane, "phase7a-uncertain-auto-restarted")
    parked = auto.drain(auto_run)
    _kill(worker)
    assert parked["status"] == "waiting_input", parked["status"]
    pending = _task_of(parked)["pending_decision"]
    assert pending and pending["kind"] == "recovery"
    # Auto: abort cancels the unresolved dispatch without replaying the
    # completed download.
    aborted_auto = auto.decide(auto_run, auto.open_command(auto_run, "abort"))
    assert aborted_auto.status_code == 200, aborted_auto.text
    abort_worker = _restart_worker(lane, "phase7a-abort-auto")
    auto_aborted = auto.drain(auto_run)
    _kill(abort_worker)
    assert auto_aborted["status"] in {"cancelled", "failed"}, auto_aborted["status"]
    assert _download_count(lane, auto_download) == 1
    auto_creates, auto_commands = _sandbox_counts(lane, auto_sandbox)
    assert (len(auto_creates), len(auto_commands)) == (0, 0)

    # Skip: continue_next skips the unresolved step, so its deliverable stays
    # missing and no further command is dispatched.
    skip = _Client(lane["base_url"], "uncertain-skip")
    current, revision = skip.preferences()
    assert current == "copilot"
    switched = skip.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    assert (switched.json()["agent_mode"], switched.json()["revision"]) == ("auto", 2)
    skip_conversation = skip.conversation("Weather with a lost worker")
    skip_download = _download_since(lane)
    skip_sandbox = _sandbox_since(lane)
    skip_run = skip.turn(skip_conversation, WEATHER_TURN, mode=("auto", 2))
    _crash_worker(lane, "phase7a-uncertain-skip", "create")
    mid = skip.task(skip_run)
    assert mid["status"] == "running", mid["status"]
    assert _task_of(mid)["pending_decision"] is None
    assert _operation_pairs(mid) == [
        ("download_file", "succeeded"),
        ("python_execute", "running"),
    ]
    reopened = skip.resume(skip_run, "last_step")
    assert reopened.status_code == 202, reopened.text
    worker = _restart_worker(lane, "phase7a-uncertain-skip-restarted")
    parked = skip.drain(skip_run)
    _kill(worker)
    assert parked["status"] == "waiting_input", parked["status"]
    pending = _task_of(parked)["pending_decision"]
    assert pending and pending["kind"] == "recovery"
    # Skip: continue_next is not offered while the earlier command is still
    # unsettled (operation left running by the create fault), so skip aborts;
    # the diagnosis is still parked and explicit.
    skip_decision = skip.decide(skip_run, skip.open_command(skip_run, "abort"))
    assert skip_decision.status_code == 200, skip_decision.text
    skip_worker = _restart_worker(lane, "phase7a-skip")
    skipped_state = skip.drain(skip_run)
    _kill(skip_worker)
    assert skipped_state["status"] in {"cancelled", "failed"}, skipped_state["status"]
    assert _download_count(lane, skip_download) == 1
    skip_creates, skip_commands = _sandbox_counts(lane, skip_sandbox)
    assert (len(skip_creates), len(skip_commands)) == (0, 0)
    _stop_all(lane)


def test_unrecorded_approval_reprompts_without_repeating_download(lane):
    """An approval that never committed leaves the same ask open."""
    client = _Client(lane["base_url"], "lost-approval")
    mode, revision = client.preferences()
    assert (mode, revision) == ("copilot", 1), "a fresh account starts in Copilot"
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    assert (switched.json()["agent_mode"], switched.json()["revision"]) == ("auto", 2)
    conversation = client.conversation("Weather with a lost approval")
    download_since = _download_since(lane)
    run_id = client.turn(conversation, WEATHER_TURN, mode=("auto", 2))
    _crash_worker(lane, "phase7a-lost-approval", "create")

    parked = client.resume(run_id, "last_step")
    assert parked.status_code == 202, parked.text
    _restart_worker(lane, "phase7a-lost-approval-restarted")
    waiting = client.drain(run_id)
    assert waiting["status"] == "waiting_input", waiting["status"]
    ask = _task_of(waiting)["pending_decision"]
    assert ask and ask["kind"] == "recovery"

    # The transaction that would have recorded this choice never reached the
    # database, so the same request must still be the open one.
    command = client.open_command(run_id, "abort")
    _lost_approval(lane, client.principal_id, run_id, command)
    re_prompted = client.task(run_id)
    assert re_prompted["status"] == "waiting_input", re_prompted["status"]
    again = _task_of(re_prompted)["pending_decision"]
    assert again and again["request_id"] == ask["request_id"]
    assert again["expected_digest"] == ask["expected_digest"]
    assert _download_count(lane, download_since) == 1

    # A command replayed against a plan revision the run is not on is refused.
    conflict = client.decide(
        run_id,
        {
            "client_request_id": str(uuid_module.uuid4()),
            "request_id": ask["request_id"],
            "plan_revision": ask["plan_revision"] + 1,
            "expected_digest": ask["expected_digest"],
            "action": "abort",
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "decision_conflict"

    chosen = client.decide(run_id, client.open_command(run_id, "abort"))
    assert chosen.status_code == 200, chosen.text
    _restart_worker(lane, "phase7a-lost-approval-chosen")
    finished = client.drain(run_id)
    assert finished["status"] in {"cancelled", "failed"}, finished["status"]
    assert _download_count(lane, download_since) == 1
    # This journey's own workers are stopped with it: a queue reader that
    # outlives its journey claims the next journey's runs, and the crash legs
    # below only mean what they say with exactly one reader on the queue.
    _stop_all(lane)


def test_browser_reload_preference_repair_and_revision(lane):
    """The browser lane drives the same durable task across a restart."""
    # Only this journey's workers may read the queue while it runs: a reader
    # left over from an earlier journey steals this journey's runs, and the
    # crash leg below dies only when it claims the run it was armed for.
    _stop_all(lane)
    # The browser signs in as one fixed account that the harness registers
    # through the same API first. OPENSHIP_FULL_STACK_BROWSER_USERNAME and
    # OPENSHIP_FULL_STACK_BROWSER_PASSWORD choose the credentials the operator
    # types into the real sign-in form; unset, the harness mints a fresh pair
    # for this run and passes it to the Playwright spec in its environment.
    client = _Client(
        lane["base_url"],
        "browser",
        username=os.environ.get("OPENSHIP_FULL_STACK_BROWSER_USERNAME"),
        secret=os.environ.get("OPENSHIP_FULL_STACK_BROWSER_PASSWORD"),
    )
    mode, revision = client.preferences()
    assert (mode, revision) == ("copilot", 1), "a fresh account starts in Copilot"

    # One completed Copilot task: the gate parks, the human confirms, and the
    # defective first envelope is refused before the corrected plan runs.
    completed_conversation = client.conversation("Browser weather")
    completed_run = client.turn(
        completed_conversation, WEATHER_TURN, mode=("copilot", revision)
    )
    _start_worker(lane, "phase7a-browser-gate")
    waiting_plan = client.drain(completed_run)
    assert waiting_plan["status"] == "waiting_plan", waiting_plan["status"]
    confirmed = client.decide(
        completed_run, client.open_command(completed_run, "confirm")
    )
    assert confirmed.status_code == 200, confirmed.text
    finished = client.drain(completed_run)
    assert finished["status"] == "completed", finished["status"]
    assert _task_of(finished)["status"] == "completed"

    # One parked recovery: the worker dies as the sandbox is being created, so
    # the command's outcome is unknown and the durable records hold an
    # unsettled operation. The run asks for an explicit choice rather than
    # guessing, and the plan's later step depends on this step's output.
    recovery_conversation = client.conversation("Browser recovery")
    recovery_run = client.turn(
        recovery_conversation, BROWSER_TURN, mode=("copilot", revision)
    )
    parked_plan = client.drain(recovery_run)
    assert parked_plan["status"] == "waiting_plan", parked_plan["status"]
    approved = client.decide(
        recovery_run, client.open_command(recovery_run, "confirm")
    )
    assert approved.status_code == 200, approved.text
    _stop_all(lane)
    _crash_worker(lane, "phase7a-browser-crash", "create")
    reopened = client.resume(recovery_run, "last_step")
    assert reopened.status_code == 202, reopened.text
    _restart_worker(lane, "phase7a-browser-abort")
    recovery_parked = client.drain(recovery_run)
    assert recovery_parked["status"] == "waiting_input", recovery_parked["status"]
    pending = _task_of(recovery_parked)["pending_decision"]
    assert pending and pending["kind"] == "recovery"
    assert "repeat_step" not in pending["diagnosis"]["eligible_actions"]

    _run_browser_qualification(
        lane,
        {
            "OPENSHIP_FULL_STACK_BROWSER_USERNAME": client.username,
            "OPENSHIP_FULL_STACK_BROWSER_PASSWORD": client.password,
            "OPENSHIP_FULL_STACK_BROWSER_CONVERSATION": completed_conversation,
            "OPENSHIP_FULL_STACK_BROWSER_RECOVERY_CONVERSATION": recovery_conversation,
        },
    )
    _stop_all(lane)


def _run_browser_qualification(lane: dict, extra_env: dict[str, str] | None = None) -> None:
    web_root = _API_ROOT.parent / "web"
    spec = web_root / "tests/full-stack/agentic-tasks.spec.ts"
    if not spec.exists():
        pytest.fail(
            "the agentic task browser qualification spec is missing; "
            "the enabled lane must not pass without it"
        )
    result = subprocess.run(
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "--config",
            "playwright.full-stack.config.ts",
            "tests/full-stack/agentic-tasks.spec.ts",
            "--reporter=line",
        ],
        cwd=web_root,
        env={
            **os.environ,
            "OPENSHIP_FULL_STACK_API_URL": lane["base_url"],
            **(extra_env or {}),
        },
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"agentic task browser qualification failed:\n{(result.stdout + result.stderr)[-6000:]}"
        )
    summary = re.search(r"(?m)^\s*(\d+) passed(?:\s+\([^)]+\))?\s*$", result.stdout)
    assert summary is not None, "Playwright browser result did not report a passing test count"
    assert int(summary.group(1)) == 1, "Playwright browser lane ran an unexpected test count"
