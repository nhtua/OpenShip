"""Authorized live agentic task journey: real model, real manager, real source.

Selected only with ``OPENSHIP_LIVE_AGENTIC_TASKS=1``. When unselected the lane
skips; when selected every missing prerequisite fails it. Nothing in this lane is
synthetic: the model is the operator's own tool-capable profile with fallbacks
disabled, the sandbox transport is the real OpenSandbox gateway, downloads go
through the product's own URL policy and capture, and the durable store is a
disposable PostgreSQL database with the broker the plan names. The profile
snapshot, endpoint, key file and provider tuple are named explicitly here and
never inferred from an unrelated installation.

Fixture contract the journeys assume about the operator-provided source URLs:

``OPENSHIP_LIVE_AGENTIC_SOURCE_URL``
    A city/month/temperature CSV whose London January rows average the first
    value of ``OPENSHIP_LIVE_AGENTIC_EXPECTED_MONTHS`` and so on, with at least
    one non-London row that must be excluded.
``OPENSHIP_LIVE_AGENTIC_DEFECTIVE_SOURCE_URL``
    The same data with one malformed numeric cell in a row the task must not
    include, so a known repair is required and the expected numbers stay the same.
``OPENSHIP_LIVE_AGENTIC_INVALID_SOURCE_URL``
    A URL the policy gate refuses (or that cannot be reached). No chart may be
    produced from it and the run must end honestly, not with a substitute source.
``OPENSHIP_LIVE_AGENTIC_EXPECTED_AVERAGES``
    The declared ground truth, e.g. ``{"January": 5, "February": 8}``. The tests
    also recompute the numbers from the bytes they fetch themselves, so neither
    the generated Python nor this declaration alone is trusted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import uuid as uuid_module
from collections import defaultdict
from io import StringIO
from pathlib import Path

import httpx
import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.providers.control import InvocationControl
from src.openship.sandboxes.client import OpenSandboxClient
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    OutputSpec,
    PreparedFiles,
    RuntimeRequirements,
)
from src.openship.sandboxes.profile_schema import decode_catalog
from tests.full_stack.test_agentic_tasks import (
    _artifact_json,
    _Client,
    _operation_pairs,
    _png_size,
    _start_task_api,
    _task_of,
    _upload,
)
from tests.full_stack.test_manual_search import (
    _create_database,
    _drop_database,
    _free_port,
    _run_migrations,
    _stop,
)

pytestmark = pytest.mark.provider_live

SELECTOR = "OPENSHIP_LIVE_AGENTIC_TASKS"
_LIVE_ROOT = Path(__file__).parents[2]
_LOGS_JSON = (
    '[{"service": "api", "duration_ms": 100},'
    ' {"service": "api", "duration_ms": 200},'
    ' {"service": "worker", "duration_ms": 50}]'
)
_LOGS_AVERAGES = {"api": 150, "worker": 50}

REQUIRED = (
    "MODEL_PROFILES_JSON",
    "OPENSHIP_LIVE_AGENTIC_DEFECTIVE_SOURCE_URL",
    "OPENSHIP_LIVE_AGENTIC_EXPECTED_AVERAGES",
    "OPENSHIP_LIVE_AGENTIC_INVALID_SOURCE_URL",
    "OPENSHIP_LIVE_AGENTIC_PROFILES_FILE",
    "OPENSHIP_LIVE_AGENTIC_SOURCE_URL",
    "OPENSHIP_LIVE_OPENSANDBOX_DEPENDENCY_IDENTITY",
    "OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT",
    "OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE",
    "OPENSHIP_LIVE_OPENSANDBOX_PROFILE_ID",
    "OPENSHIP_LIVE_TOOL_PROFILE_ID",
    "TEST_NATS_AUTH_FILE",
    "TEST_NATS_SERVER_CONFIG",
    "TEST_NATS_URL",
    "TEST_POSTGRES_URL",
)


def _selected() -> dict:
    """The named live settings, validated. Selected-and-missing fails the lane."""
    if os.environ.get(SELECTOR) != "1":
        pytest.skip(
            f"{SELECTOR}=1 is required, and only with an authorized provider tuple, "
            "endpoint and key; nothing here runs against a stub"
        )
    missing = sorted(name for name in REQUIRED if not os.environ.get(name))
    if missing:
        pytest.fail("the selected live agentic task lane is missing prerequisites: " + ", ".join(missing))

    snapshot = Path(os.environ["OPENSHIP_LIVE_AGENTIC_PROFILES_FILE"])
    if not snapshot.is_file():
        pytest.fail("OPENSHIP_LIVE_AGENTIC_PROFILES_FILE names a missing profiles file")
    catalog = decode_catalog(snapshot.read_bytes())
    profile_id = os.environ["OPENSHIP_LIVE_OPENSANDBOX_PROFILE_ID"]
    entry = catalog.profiles.get(profile_id)
    if entry is None:
        pytest.fail(f"the profiles file names no profile {profile_id}")
    if not entry.enabled:
        pytest.fail(f"profile {profile_id} is disabled in the profiles file")
    if not entry.runtime.smoke_passed:
        pytest.fail(f"profile {profile_id} is not smoke-qualified in the profiles file")
    if entry.runtime.dependency_identity != os.environ["OPENSHIP_LIVE_OPENSANDBOX_DEPENDENCY_IDENTITY"]:
        pytest.fail("the declared dependency identity does not match the profiles file")

    key_file = Path(os.environ["OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE"])
    if not key_file.is_file():
        pytest.fail("OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE names a missing key file")
    if not key_file.read_text().strip():
        pytest.fail("the live OpenSandbox key file is empty")

    profiles = json.loads(os.environ["MODEL_PROFILES_JSON"])
    tool_profile_id = os.environ["OPENSHIP_LIVE_TOOL_PROFILE_ID"]
    profile = next((item for item in profiles if item.get("id") == tool_profile_id), None)
    if profile is None:
        pytest.fail(f"OPENSHIP_LIVE_TOOL_PROFILE_ID names {tool_profile_id}, absent from MODEL_PROFILES_JSON")
    capabilities = profile.get("capabilities") or {}
    if not (capabilities.get("tool_calls") and capabilities.get("structured_output")):
        pytest.fail("the live task profile must be tool-capable and structured-output-capable")
    if profile.get("allowed_fallback_profile_ids"):
        pytest.fail("live agentic qualification runs with fallbacks disabled")
    credential_ref = profile.get("credential_ref") or ""
    if not os.environ.get(credential_ref):
        pytest.fail(f"the live task profile's credential reference {credential_ref} is absent")

    return {
        "artifact_averages": json.loads(os.environ["OPENSHIP_LIVE_AGENTIC_EXPECTED_AVERAGES"]),
        "broker_auth": os.environ["TEST_NATS_AUTH_FILE"],
        "broker_url": os.environ["TEST_NATS_URL"],
        "credential_ref": credential_ref,
        "dependency_identity": entry.runtime.dependency_identity,
        "defective_url": os.environ["OPENSHIP_LIVE_AGENTIC_DEFECTIVE_SOURCE_URL"],
        "endpoint": os.environ["OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT"],
        "image_id": entry.runtime.image_id,
        "image_tag": entry.runtime.image_tag,
        "invalid_url": os.environ["OPENSHIP_LIVE_AGENTIC_INVALID_SOURCE_URL"],
        "key_file": key_file,
        "platform": entry.runtime.platform,
        "postgres_url": os.environ["TEST_POSTGRES_URL"],
        "profile_id": profile_id,
        "snapshot": snapshot,
        "source_url": os.environ["OPENSHIP_LIVE_AGENTIC_SOURCE_URL"],
        "tool_profile_id": tool_profile_id,
    }


def _live_environment(reqs: dict, database_url: str, root: Path, key_path: Path) -> dict[str, str]:
    """The stack's environment: the operator's own profiles, endpoint and key."""
    env = {
        "AGENTIC_TASK_PROFILE_IDS": reqs["tool_profile_id"],
        "AGENTIC_TASKS_ENABLED": "true",
        "ARTIFACT_ROOT": str(root / "artifacts"),
        "ARTIFACT_STORAGE_ID": str(uuid_module.uuid4()),
        "DATABASE_URL": database_url,
        "JWT_EXPIRY_HOURS": "24",
        "JWT_SECRET": f"phase7a-live-{uuid_module.uuid4().hex}",
        "MODEL_DEFAULT_PROFILE_ID": reqs["tool_profile_id"],
        "MODEL_PROFILES_JSON": os.environ["MODEL_PROFILES_JSON"],
        "NATS_AUTH_FILE": reqs["broker_auth"],
        "NATS_SERVERS": reqs["broker_url"],
        "OPENSHIP_LIVE_AGENTIC_PROFILES_FILE": str(reqs["snapshot"]),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_LIVE_ROOT),
        "PYTHONUNBUFFERED": "1",
        "RECONCILER_INTERVAL": "3000",
        "SANDBOX_DEPENDENCY_IDENTITY": reqs["dependency_identity"],
        "SANDBOX_DEPLOYMENT_ID": "phase7a-live-journey",
        "SANDBOX_ENABLED": "true",
        "SANDBOX_ENDPOINT": reqs["endpoint"],
        "SANDBOX_IMAGE": reqs["image_tag"],
        "SANDBOX_KEY_FILE": str(key_path),
        "SANDBOX_PROFILE_ID": reqs["profile_id"],
        "SYNTHETIC_PROFILE_SNAPSHOT": str(reqs["snapshot"]),
        "TOOL_ENABLED_IDS": "download_file,python_execute",
        "WORKER_CANCELLATION_POLL_SECONDS": "0.2",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "WORKER_LEASE_SECONDS": "30",
        "WORKER_POLL_INTERVAL": "1",
    }
    env[reqs["credential_ref"]] = os.environ[reqs["credential_ref"]]
    return env


@pytest.fixture(scope="module")
def live_lane(tmp_path_factory):
    """One live stack: real API, real worker launches, one disposable database."""
    reqs = _selected()
    root = tmp_path_factory.mktemp("agentic-live")
    scratch = root / "process"
    scratch.mkdir()
    key_path = root / "openship-sandbox.key"
    key_path.write_bytes(reqs["key_file"].read_bytes())
    key_path.chmod(0o600)

    database = f"p7a_live_{uuid_module.uuid4().hex[:16]}"
    database_url = _create_database(reqs["postgres_url"], database)
    _run_migrations(_LIVE_ROOT, scratch, database_url)
    init = subprocess.run(
        [sys.executable, "-m", "src.openship.artifacts.maintenance", "init"],
        cwd=scratch,
        env=_live_environment(reqs, database_url, root, key_path),
        stdout=subprocess.DEVNULL,
        stderr=sys.stdout,
        timeout=60,
        check=False,
    )
    assert init.returncode == 0, "artifact storage initialization failed"

    env = _live_environment(reqs, database_url, root, key_path)
    api_port = _free_port()
    api_process, base_url = _start_task_api(scratch, env, api_port)
    holder = {
        "base_url": base_url,
        "env": env,
        "reqs": reqs,
        "root": scratch,
        "workers": [],
        "api": api_process,
    }
    try:
        yield holder
    finally:
        _stop_all_workers(holder)
        _stop(api_process)
        _drop_database(reqs["postgres_url"], database)


def _start_worker(lane: dict, worker_id: str) -> subprocess.Popen:
    """The product's own worker loop against the same services the API serves."""
    process = subprocess.Popen(
        [
            sys.executable,
            str(_LIVE_ROOT / "tests/live/live_agentic_worker.py"),
            "--worker-id",
            worker_id,
            "--log-level",
            "WARNING",
        ],
        cwd=lane["root"],
        env=lane["env"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    lane["workers"].append(process)
    return process


def _stop_all_workers(lane: dict) -> None:
    for process in lane["workers"]:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=20)
    lane["workers"].clear()


def _restart_api(lane: dict) -> str:
    """Recreate the API process alone: the durable records must survive it."""
    _stop(lane["api"])
    port = _free_port()
    process, base_url = _start_task_api(lane["root"], lane["env"], port)
    lane["api"] = process
    lane["base_url"] = base_url
    return base_url


class _Relogin(_Client):
    """Sign the same account in again: a new bearer, the same durable records."""

    def _login(self, tag: str) -> None:
        response = self.post("/api/auth/login", json={"username": self.username, "password": self.password})
        assert response.status_code == 200, response.text
        self.principal_id = response.json()["user"]["id"]
        self.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def _live_turn(
    lane: dict,
    client: _Client,
    conversation_id: str,
    content: str,
    mode: tuple[str, int],
    artifact_ids: list[str] | None = None,
) -> str:
    """One turn on the operator's declared tool-capable profile, fallbacks off."""
    payload = {
        "client_request_id": str(uuid_module.uuid4()),
        "content": content,
        "provider_profile_id": lane["reqs"]["tool_profile_id"],
        "fallback_profile_ids": [],
        "enable_tools": True,
        "agent_mode": mode[0],
        "agent_mode_revision": mode[1],
    }
    if artifact_ids:
        payload["artifact_ids"] = artifact_ids
    response = client.post(f"/api/runs/{conversation_id}/turns", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _fetch(url: str) -> bytes:
    with httpx.Client(timeout=30, trust_env=False) as http:
        response = http.get(url)
        response.raise_for_status()
        return response.content


def _monthly_averages(body: bytes) -> dict[str, float]:
    """London's monthly means, computed here from the bytes the lane fetched."""
    totals: dict[str, list[float]] = defaultdict(list)
    for row in csv.DictReader(StringIO(body.decode("utf-8"))):
        if row.get("city") != "London":
            continue
        totals[row["month"]].append(float(row["temperature_c"]))
    return {month: round(sum(values) / len(values), 4) for month, values in totals.items()}


def _chart_outputs(client: _Client, run: dict) -> tuple[dict, bytes]:
    pairs = _operation_pairs(run)
    assert pairs == [("download_file", "succeeded"), ("python_execute", "succeeded")], pairs
    python_operation = run["operations"][1]
    outputs = python_operation["result"]["outputs"]
    averages = _artifact_json(client, outputs["averages.json"])
    chart = client.content(outputs["chart.png"]["id"])
    assert chart.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = _png_size(chart)
    assert width > 0 and height > 0
    return averages, chart


@pytest.mark.asyncio
async def test_immutable_profile_reference_executes_through_manager():
    """The manager runs the snapshot's immutable image id, and proves it."""
    reqs = _selected()
    pinned = reqs["image_id"]
    creates: list[dict] = []

    async def capture(request: httpx.Request) -> None:
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            creates.append(json.loads(request.content))

    source = (
        b"import json, os\n"
        b"import matplotlib, numpy\n"
        b"from pathlib import Path\n"
        b"report = {'matplotlib': matplotlib.__version__, 'numpy': numpy.__version__}\n"
        b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.json').write_text(json.dumps(report))\n"
    )
    reference = ArtifactRef(
        id=uuid_module.uuid4(),
        sha256=hashlib.sha256(source).hexdigest(),
        size_bytes=len(source),
        media_type="text/plain",
    )
    requirements = RuntimeRequirements(
        provider_id="live-phase7a",
        profile_id=reqs["profile_id"],
        image=pinned,
        dependency_identity=reqs["dependency_identity"],
    )

    async with httpx.AsyncClient(trust_env=False, event_hooks={"request": [capture]}) as http:
        client = OpenSandboxClient(reqs["endpoint"], reqs["key_file"].read_text().strip(), http=http)
        try:
            handle = await client.create(requirements)
            assert creates[0]["image"] == {"uri": pinned}, "the create request must name the pinned immutable image id"

            record = await http.get(f"{reqs['endpoint']}/v1/sandboxes/{handle.sandbox_id}")
            assert record.status_code == 200, record.text
            reported = (record.json().get("image") or {}).get("uri")
            if reported != pinned:
                pytest.fail(
                    "the manager did not prove the selected image: it reports "
                    f"{reported!r} for the sandbox it created from {pinned!r}; a tag "
                    "or a SHA-shaped string without proof is not qualification"
                )

            request = ExecutionRequest(
                root_run_id=uuid_module.uuid4(),
                operation_id=uuid_module.uuid4(),
                requirements=requirements,
                source=reference,
                inputs=(),
                parameters={},
                outputs=(OutputSpec(alias="report.json", media_type="application/json", kind="data"),),
            )
            result = await client.execute(
                handle, request, PreparedFiles(source=source, inputs={}), InvocationControl()
            )
            assert result.exit_code == 0, result.stderr.decode("utf-8", "replace")[-400:]
            report = json.loads(result.outputs["report.json"])
            assert report.get("matplotlib") and report.get("numpy"), report
        finally:
            await client.aclose()

    if os.environ.get("OPENSHIP_LIVE_AGENTIC_ENGINE_INSPECTION") == "1":
        pytest.skip("engine identity is checked by the separate engine inspection case")


def test_pinned_image_id_matches_the_local_engine_identity():
    """The engine's own identity for the pinned reference, when the operator inspects it."""
    if os.environ.get("OPENSHIP_LIVE_AGENTIC_ENGINE_INSPECTION") != "1":
        pytest.skip("set OPENSHIP_LIVE_AGENTIC_ENGINE_INSPECTION=1 only where the manager's engine is inspectable")
    reqs = _selected()
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reqs["image_id"]],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert inspected.returncode == 0, inspected.stderr.strip()[-300:]
    assert inspected.stdout.strip() == reqs["image_id"], "the engine's image id differs from the pinned reference"


def test_live_outcome_chart_in_both_modes(live_lane):
    """Copilot confirms once, Auto confirms nothing, and the mode is the account's."""
    reqs = live_lane["reqs"]
    client = _Client(live_lane["base_url"], "live-chart")
    mode, revision = client.preferences()
    assert (mode, revision) == ("copilot", 1), "a fresh account starts in Copilot"

    conversation = client.conversation("Live London weather")
    turn = f"Chart the London monthly averages from {reqs['source_url']} and report them."
    source_bytes = _fetch(reqs["source_url"])
    expected = _monthly_averages(source_bytes)
    assert expected == reqs["artifact_averages"], "the source fixture and its declared ground truth differ"

    copilot_run = _live_turn(live_lane, client, conversation, turn, ("copilot", revision))
    _start_worker(live_lane, "phase7a-live-copilot")
    parked = client.drain(copilot_run)
    assert parked["status"] == "waiting_plan", parked["status"]
    task = _task_of(parked)
    assert [item["id"] for item in task["plan"]["steps"]] == ["fetch", "chart"]
    confirmed = client.decide(copilot_run, client.open_command(copilot_run, "confirm"))
    assert confirmed.status_code == 200, confirmed.text
    finished = client.drain(copilot_run)
    assert finished["status"] == "completed", finished["status"]

    averages, _chart = _chart_outputs(client, finished)
    assert averages == expected
    csv_ref = finished["operations"][0]["result"]["artifact"]
    assert client.content(csv_ref["id"]) == source_bytes
    verified = {str(item["id"]) for item in _task_of(finished)["verified_outputs"]}
    python_outputs = finished["operations"][1]["result"]["outputs"]
    assert verified >= {csv_ref["id"], python_outputs["averages.json"]["id"], python_outputs["chart.png"]["id"]}

    # The same account on Auto: the same journey with no plan confirmation.
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text
    assert (switched.json()["agent_mode"], switched.json()["revision"]) == ("auto", 2)
    auto_run = _live_turn(live_lane, client, conversation, turn, ("auto", 2))
    auto_finished = client.drain(auto_run)
    assert auto_finished["status"] == "completed", auto_finished["status"]
    auto_task = _task_of(auto_finished)
    assert auto_task["mode"] == "auto"
    assert auto_task["pending_decision"] is None, "Auto must not park a plan confirmation"
    assert _chart_outputs(client, auto_finished)[0] == expected

    # The account's mode is its own durable record: it outlives the API process.
    base_url = _restart_api(live_lane)
    again = _Relogin(base_url, "relogin", username=client.username, secret=client.password)
    assert again.preferences() == ("auto", 2), "the mode the account saved is the mode it reads back"
    _stop_all_workers(live_lane)


def test_live_known_repair_invalid_source_and_uploaded_logs(live_lane):
    """A known defect repairs inside its step, a refused source stays honest."""
    reqs = live_lane["reqs"]
    client = _Client(live_lane["base_url"], "live-repair")
    _, revision = client.preferences()
    switched = client.set_mode("auto", expected_revision=revision)
    assert switched.status_code == 200, switched.text

    # One known defect in a row the task must exclude: the numbers stay the same.
    expected = _monthly_averages(_fetch(reqs["source_url"]))
    repair_conversation = client.conversation("Live defective source")
    repair_turn = f"Chart the London monthly averages from {reqs['defective_url']} and report them."
    repair_run = _live_turn(live_lane, client, repair_conversation, repair_turn, ("auto", 2))
    _start_worker(live_lane, "phase7a-live-repair")
    repaired = client.drain(repair_run, timeout=180.0)
    assert repaired["status"] == "completed", repaired["status"]
    repair_task = _task_of(repaired)
    chart_step = next(item for item in repair_task["steps"] if item["id"] == "chart")
    assert len(chart_step["attempts"]) >= 2, "the known defect must show a repaired attempt, not a relabelled success"
    assert _chart_outputs(client, repaired)[0] == expected

    # The refused source produces no chart and no substitute.
    blocked_conversation = client.conversation("Live refused source")
    blocked_turn = f"Chart the London monthly averages from {reqs['invalid_url']} and report them."
    blocked_run = _live_turn(live_lane, client, blocked_conversation, blocked_turn, ("auto", 2))
    blocked = client.drain(blocked_run, timeout=180.0)
    assert blocked["status"] in {"failed", "cancelled", "blocked"}, blocked["status"]
    assert [name for name, status in _operation_pairs(blocked) if status == "succeeded"] == []
    assert _task_of(blocked)["verified_outputs"] == []

    # Uploaded records use the same plan, tool and profile door as a URL.
    logs_conversation = client.conversation("Live uploaded logs")
    artifact_id = _upload(client, logs_conversation, _LOGS_JSON.encode("utf-8"), "runs.json")
    logs_turn = f"Average the duration records in the uploaded logs per service and chart them. Uploaded log: {artifact_id}"
    logs_run = _live_turn(
        live_lane, client, logs_conversation, logs_turn, ("auto", 2), artifact_ids=[artifact_id]
    )
    logs_finished = client.drain(logs_run, timeout=180.0)
    assert logs_finished["status"] == "completed", logs_finished["status"]
    assert _operation_pairs(logs_finished) == [("python_execute", "succeeded")]
    averages = _chart_outputs(client, logs_finished)[0]
    assert averages == _LOGS_AVERAGES
    _stop_all_workers(live_lane)
