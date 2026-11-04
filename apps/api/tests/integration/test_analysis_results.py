"""API → real worker → gateway sandbox analysis with only HTTP transport stubbed.

The FastAPI routes, run queue, real durable worker, gateway admission, active
slot, artifact store and fenced publication are production machinery. Only the
OpenSandbox provider answers through an in-memory transport speaking the
documented proxy routes, which proves request shape and current-input flow,
not provider isolation (that journey belongs to Task 6).
"""

import base64
import hashlib
import json
import re
import time
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ToolInvocationContext,
    decode_execution_config,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.providers.control import InvocationControl
from src.openship.runs.analysis_graph import decode_journey, resume_phase
from src.openship.runs.dispatch import build_analysis_graph
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import claim_next
from src.openship.runs.worker import Worker
from src.openship.sandboxes import tool as sandbox_tool
from src.openship.sandboxes.client import OpenSandboxClient
from src.openship.workspace.models import Project

ANALYSIS_ID = "sandbox_analysis"
ENDPOINT = "http://sandbox.lane.test"
IMAGE = "localhost/openship-python:phase6b-qualified"
PROFILE_ID = "python311"
DEPENDENCY = "stdlib"

PARSER_SOURCE = (
    b"import os, json\n"
    b"from pathlib import Path\n"
    b"log = (Path(os.environ['OPENSHIP_INPUT_DIR']) / 'logs.txt').read_bytes()\n"
    b"parameters = json.loads(Path(os.environ['OPENSHIP_PARAMETERS']).read_bytes())\n"
    b"lines = [line for line in log.decode().splitlines() if line.strip()]\n"
    b"matched = [line for line in lines if parameters['level'] in line]\n"
    b"out = Path(os.environ['OPENSHIP_OUTPUT_DIR'])\n"
    b"(out / 'summary.json').write_text(json.dumps({'lines': len(lines), "
    b"'matched': len(matched)}, sort_keys=True))\n"
    b"(out / 'report.txt').write_text(f'matched {len(matched)} of {len(lines)}\\n')\n"
)


def _expected_output(rows, level: str) -> dict[str, bytes]:
    """What the declared program must return for one selected log."""
    rows = [row for row in rows if row.strip()]
    matched = [row for row in rows if level in row]
    return {
        "summary.json": json.dumps(
            {"lines": len(rows), "matched": len(matched)}, sort_keys=True
        ).encode(),
        "report.txt": f"matched {len(matched)} of {len(rows)}\n".encode(),
    }


class Sandbox:
    """OpenSandbox stand-in answering only the documented client routes."""

    def __init__(self):
        self.created_ids: list[str] = []
        self.uploaded: dict[str, bytes] = {}
        self.produced: dict[str, dict[str, bytes]] = {}
        self.commands: list[dict] = []
        self.dead = False
        self.pending_path = None

    def _multipart(self, request) -> list[tuple[str, bytes]]:
        boundary = request.headers["content-type"].split("boundary=", 1)[1].encode()
        parts = []
        for raw in request.content.split(b"--" + boundary)[1:]:
            if raw.startswith(b"--"):
                continue
            head, _, body = raw.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]*)"', head)
            if name is None:
                continue
            parts.append(
                (name.group(1).decode(), body[:-2] if body.endswith(b"\r\n") else body)
            )
        return parts

    def _program(self, body: dict) -> dict[str, bytes]:
        """Derive the declared outputs from the bytes this request uploaded."""
        inputs_dir = body["envs"]["OPENSHIP_INPUT_DIR"]
        parameters = json.loads(self.uploaded[body["envs"]["OPENSHIP_PARAMETERS"]])
        log = self.uploaded[f"{inputs_dir}/logs.txt"]
        rows = [row for row in log.decode().splitlines() if row.strip()]
        level = parameters["level"]
        matched = [row for row in rows if level in row]
        return {
            "summary.json": json.dumps(
                {"lines": len(rows), "matched": len(matched)}, sort_keys=True
            ).encode(),
            "report.txt": f"matched {len(matched)} of {len(rows)}\n".encode(),
        }

    def route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.dead:
            raise httpx.TransportError("provider disappeared")
        if request.method == "POST" and path == "/v1/sandboxes":
            sandbox_id = f"sbx-lane-{len(self.created_ids) + 1}"
            self.created_ids.append(sandbox_id)
            return httpx.Response(
                202,
                json={
                    "id": sandbox_id,
                    "expiresAt": (
                        datetime.now(UTC) + timedelta(minutes=30)
                    ).isoformat(),
                    "status": {"state": "Running"},
                },
            )
        if (
            request.method == "GET"
            and path.startswith("/v1/sandboxes/")
            and path.count("/") == 3
        ):
            sandbox_id = path.rsplit("/", 1)[-1]
            if sandbox_id not in self.created_ids:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "id": sandbox_id,
                    "expiresAt": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                    "status": {"state": "Running"},
                },
            )
        if path.endswith("/proxy/44772/ping"):
            return httpx.Response(200, json={})
        if path.endswith("/directories"):
            return httpx.Response(200, json={})
        if path.endswith("/files/upload"):
            for name, content in self._multipart(request):
                if name == "metadata":
                    self.pending_path = json.loads(content)["path"]
                elif name == "file":
                    self.uploaded[self.pending_path] = content
            return httpx.Response(200, json={})
        if request.method == "POST" and path.endswith("/command"):
            body = json.loads(request.content)
            self.commands.append(body)
            self.produced[body["envs"]["OPENSHIP_OUTPUT_DIR"]] = self._program(body)
            return httpx.Response(
                200,
                content=b'{"type":"stdout","text":"parser ok"}\n'
                b'{"type":"execution_complete","exit_code":0}\n',
            )
        if path.endswith("/directories/list"):
            outputs_dir = dict(request.url.params)["path"]
            entries = [
                {
                    "path": f"{outputs_dir}/{alias}",
                    "size": len(data),
                    "type": "file",
                }
                for alias, data in self.produced[outputs_dir].items()
            ]
            return httpx.Response(200, json=entries)
        if path.endswith("/files/download"):
            outputs_dir, alias = dict(request.url.params)["path"].rsplit("/", 1)
            data = self.produced.get(outputs_dir, {}).get(alias)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)
        raise AssertionError(f"unexpected provider route: {request.method} {path}")


def install_sandbox(monkeypatch, tmp_path, provider: Sandbox) -> None:
    """Install the opt-in sandbox profile the analysis capability expects."""
    monkeypatch.setattr(settings, "tool_enabled_ids", ANALYSIS_ID)
    monkeypatch.setattr(settings, "sandbox_enabled", True)
    monkeypatch.setattr(settings, "sandbox_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "sandbox_image", IMAGE)
    monkeypatch.setattr(settings, "sandbox_profile_id", PROFILE_ID)
    monkeypatch.setattr(settings, "sandbox_dependency_identity", DEPENDENCY)
    key_file = tmp_path / "sandbox.key"
    key_file.write_bytes(b"lane-test-key\n")
    monkeypatch.setattr(settings, "sandbox_key_file", key_file)
    monkeypatch.setattr(
        sandbox_tool,
        "sandbox_client_factory",
        lambda: OpenSandboxClient(
            ENDPOINT,
            "lane-test-key",
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(provider.route),
                trust_env=False,
                follow_redirects=False,
            ),
        ),
    )


@pytest.fixture
def artifact_store(monkeypatch, tmp_path):
    """Configure the conversation's artifact volume for this test."""
    store_root = tmp_path / "artifacts"
    store = LocalArtifactStore(store_root, _uuid.uuid4())
    store.initialize()
    monkeypatch.setattr(settings, "artifact_root", store_root)
    monkeypatch.setattr(settings, "artifact_storage_id", store.storage_id)
    return store


def _authorize(client, auth_token: str) -> None:
    client.headers["Authorization"] = f"Bearer {auth_token}"


def _new_conversation(client) -> str:
    response = client.post(
        "/api/conversations", json={"title": "Sandbox analysis"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _upload(client, conversation_id: str, data: bytes, name: str) -> str:
    digest = "sha-256=" + base64.b64encode(hashlib.sha256(data).digest()).decode()
    response = client.post(
        f"/api/conversations/{conversation_id}/artifacts",
        content=data,
        headers={
            "Idempotency-Key": str(_uuid.uuid4()),
            "Digest": digest,
            "X-Artifact-Name": name,
            "Content-Type": "text/plain",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _submit_analysis(
    client, conversation_id: str, source_id: str, log_id: str, level: str
) -> str:
    response = client.post(
        f"/api/runs/{conversation_id}/tools",
        json={
            "client_request_id": str(_uuid.uuid4()),
            "capability": _analysis_ref(),
            "arguments": {
                "source_id": source_id,
                "input_ids": {"logs.txt": log_id},
                "parameters": {"level": level},
                "output_media": {
                    "report.txt": "text/plain",
                    "summary.json": "application/json",
                },
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _analysis_ref() -> dict:
    manifest = next(
        item for item in list_capability_manifests() if item.id == ANALYSIS_ID
    )
    return {
        "id": manifest.id,
        "version": manifest.version,
        "manifest_digest": manifest.manifest_digest,
    }


def _claim_one_run(run_id: str, worker_id: str):
    """Claim exactly the turn this test submitted."""
    with SessionLocal() as session:
        session.query(RunJob).filter(
            RunJob.run_id != _uuid.UUID(run_id)
        ).delete(synchronize_session=False)
        session.commit()
        claim = claim_next(session, worker_id=worker_id, lease_seconds=30)
    assert claim is not None and str(claim.run_id) == run_id
    return claim


def _execute(run_id: str) -> None:
    """Run one submitted turn through the real durable worker."""
    claim = _claim_one_run(run_id, "lane-worker")
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=SessionLocal,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim), "the worker did not publish a terminal result"
    finally:
        worker.engine.dispose()


def _retained_outputs(run_id: str) -> dict[str, Artifact]:
    with SessionLocal() as db:
        return {
            row.display_name: row
            for row in db.query(Artifact)
            .filter(
                Artifact.originating_run_id == _uuid.UUID(run_id),
                Artifact.kind == "analysis_output",
            )
            .all()
        }


def test_analysis_run_retains_declared_outputs(
    client, auth_token, artifact_store, monkeypatch, tmp_path
):
    provider = Sandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation_id = _new_conversation(client)
    source_id = _upload(client, conversation_id, PARSER_SOURCE, "parser.py")
    log_id = _upload(client, conversation_id, b"ERROR one\ninfo two\n", "logs.txt")

    run_id = _submit_analysis(client, conversation_id, source_id, log_id, "ERROR")
    _execute(run_id)

    with SessionLocal() as db:
        operation = db.query(ToolOperation).filter(
            ToolOperation.run_id == _uuid.UUID(run_id)
        ).one()
        attempt = db.query(ToolAttempt).filter(
            ToolAttempt.operation_id == operation.id
        ).one()
        stored = db.get(Run, _uuid.UUID(run_id))
        assert stored.status == "completed", (stored.error_code, provider.commands)
        assert operation.status == "succeeded"
        assert attempt.outcome == "succeeded"

    outputs = _retained_outputs(run_id)
    assert set(outputs) == {"report.txt", "summary.json"}
    expected = _expected_output(["ERROR one", "info two"], "ERROR")
    for alias, row in outputs.items():
        assert row.status == "ready"
        assert row.sha256 == hashlib.sha256(expected[alias]).hexdigest()
        assert row.size_bytes == len(expected[alias])
    assert provider.created_ids == ["sbx-lane-1"]
    assert len(provider.commands) == 1
    assert len(provider.produced) == 1


def test_changed_inputs_cannot_reuse_residual_sandbox_files(
    client, auth_token, artifact_store, monkeypatch, tmp_path
):
    provider = Sandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation_id = _new_conversation(client)
    source_id = _upload(client, conversation_id, PARSER_SOURCE, "parser.py")

    first = _submit_analysis(
        client,
        conversation_id,
        source_id,
        _upload(client, conversation_id, b"ERROR one\ninfo two\n", "logs.txt"),
        "ERROR",
    )
    _execute(first)
    second = _submit_analysis(
        client,
        conversation_id,
        source_id,
        _upload(client, conversation_id, b"ERROR a\nERROR b\nERROR c\n", "logs.txt"),
        "ERROR",
    )
    _execute(second)

    # Two runs in this conversation reuse its live sandbox pointer, but each
    # sends its complete current inputs into a fresh request directory.
    assert provider.created_ids == ["sbx-lane-1"]
    assert len(provider.commands) == 2
    assert len({body["cwd"] for body in provider.commands}) == 2
    assert len(provider.produced) == 2

    first_outputs = _retained_outputs(first)
    second_outputs = _retained_outputs(second)
    assert {row.id for row in first_outputs.values()}.isdisjoint(
        {row.id for row in second_outputs.values()}
    )
    for outputs, rows in (
        (first_outputs, ["ERROR one", "info two"]),
        (second_outputs, ["ERROR a", "ERROR b", "ERROR c"]),
    ):
        expected = _expected_output(rows, "ERROR")
        assert set(outputs) == set(expected)
        for alias, row in outputs.items():
            assert row.sha256 == hashlib.sha256(expected[alias]).hexdigest()
    assert (
        first_outputs["summary.json"].sha256
        != second_outputs["summary.json"].sha256
    )


def test_saved_outputs_survive_a_disappeared_provider(
    client, auth_token, artifact_store, monkeypatch, tmp_path
):
    provider = Sandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation_id = _new_conversation(client)
    source_id = _upload(client, conversation_id, PARSER_SOURCE, "parser.py")
    run_id = _submit_analysis(
        client,
        conversation_id,
        source_id,
        _upload(client, conversation_id, b"ERROR keep\n", "logs.txt"),
        "ERROR",
    )
    _execute(run_id)
    outputs = _retained_outputs(run_id)
    assert len(outputs) == 2

    provider.dead = True
    lost = _submit_analysis(
        client,
        conversation_id,
        source_id,
        _upload(client, conversation_id, b"ERROR late\n", "logs.txt"),
        "ERROR",
    )
    _execute(lost)
    with SessionLocal() as db:
        assert db.get(Run, _uuid.UUID(lost)).status == "failed"
    assert provider.commands, "the committed turn stays committed without a command"

    for alias, row in outputs.items():
        downloaded = client.get(f"/api/artifacts/{row.id}/content")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == _expected_output(["ERROR keep"], "ERROR")[alias]


def test_missing_selected_input_refuses_before_any_provider_request(
    client, auth_token, artifact_store, monkeypatch, tmp_path
):
    provider = Sandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation_id = _new_conversation(client)
    source_id = _upload(client, conversation_id, PARSER_SOURCE, "parser.py")
    _upload(client, conversation_id, b"ERROR one\n", "logs.txt")

    run_id = _submit_analysis(
        client, conversation_id, str(_uuid.uuid4()), source_id, "ERROR"
    )
    _execute(run_id)

    with SessionLocal() as db:
        assert db.get(Run, _uuid.UUID(run_id)).status == "failed"
    assert provider.created_ids == []
    assert provider.commands == []


def test_fixed_journey_consumes_saved_refs(
    client, auth_token, artifact_store, monkeypatch, tmp_path
):
    provider = Sandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation_id = _new_conversation(client)
    source_id = _upload(client, conversation_id, PARSER_SOURCE, "parser.py")
    log_id = _upload(client, conversation_id, b"ERROR one\n", "logs.txt")
    run_id = _submit_analysis(client, conversation_id, source_id, log_id, "ERROR")

    claim = _claim_one_run(run_id, "journey-worker")
    with SessionLocal() as db:
        run = db.get(Run, _uuid.UUID(run_id))
        user = db.get(User, claim.user_id)
        conversation = db.get(Conversation, run.conversation_id)
        project = db.get(Project, run.project_id)
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        ),
        execution=decode_execution_config(run.execution_configuration),
        credential_resolver=lambda _name: None,
        artifact_store=artifact_store,
        session_factory=SessionLocal,
        claim=claim,
    )
    control = InvocationControl(
        deadline=time.monotonic() + 30,
        clock=time.monotonic,
        stop=lambda: False,
    )
    graph = build_analysis_graph(
        MemorySaver(),
        gateway_factory=lambda: ToolGateway(SessionLocal),
        invocation_factory=lambda _state: (claim, context, control),
    )
    final = graph.invoke(
        {
            "run_id": str(run.id),
            "tool_command_json": run.tool_command_json,
            "execution_configuration": run.execution_configuration,
        },
        config={"configurable": {"thread_id": f"{run.id}-journey"}},
    )

    assert final["tool_result"]["status"] == "succeeded"
    journey = decode_journey(final["journey_json"])
    assert journey.root_run_id == run_id
    assert journey.completed_phases == ("prepare", "execute", "use_outputs")
    assert resume_phase(journey) is None
    assert len(journey.initial_refs) == 2
    assert len(journey.outputs) == 2

    expected = _expected_output(["ERROR one"], "ERROR")
    assert set(final["analysis_summary"]) == set(expected)
    for alias, body in expected.items():
        assert final["analysis_summary"][alias] == body.decode()
    assert len(provider.created_ids) == 1
    assert len(provider.commands) == 1
