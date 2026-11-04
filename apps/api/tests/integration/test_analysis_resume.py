"""Authenticated resume API through the real worker and durable storage."""

import json
import uuid

import httpx
import pytest

from src.openship.database.session import SessionLocal
from src.openship.runs.models import Run
from tests.integration.test_analysis_results import (
    PARSER_SOURCE,
    Sandbox,
    _authorize,
    _execute,
    _new_conversation,
    _retained_outputs,
    _submit_analysis,
    _upload,
    install_sandbox,
)
from tests.integration.test_analysis_results import (
    artifact_store as shared_artifact_store,
)


@pytest.fixture(name="artifact_store")
def artifact_store_fixture(monkeypatch, tmp_path):
    return shared_artifact_store.__wrapped__(monkeypatch, tmp_path)


class ExpiringSandbox(Sandbox):
    expired_ids: set[str]

    def __init__(self):
        super().__init__()
        self.expired_ids = set()
        self.on_command = None

    def route(self, request):
        if (
            request.method == "GET"
            and request.url.path.count("/") == 3
            and request.url.path.rsplit("/", 1)[-1] in self.expired_ids
        ):
            return httpx.Response(404)
        response = super().route(request)
        if (
            request.method == "POST"
            and request.url.path.endswith("/command")
            and self.on_command
        ):
            self.on_command()
        return response


def setup_analysis(client, auth_token, monkeypatch, tmp_path):
    provider = ExpiringSandbox()
    install_sandbox(monkeypatch, tmp_path, provider)
    _authorize(client, auth_token)
    conversation = _new_conversation(client)
    source = _upload(client, conversation, PARSER_SOURCE, "parser.py")
    log = _upload(client, conversation, b"ERROR saved\n", "logs.txt")
    run_id = _submit_analysis(client, conversation, source, log, "ERROR")
    return provider, run_id


def resume(client, run_id, mode="last_step", request_id=None):
    return client.post(
        f"/api/runs/{run_id}/resume-analysis",
        json={
            "mode": mode,
            "client_request_id": request_id or str(uuid.uuid4()),
        },
    )


def test_a_reuses_reports_and_request_identity_without_provider(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    outputs = _retained_outputs(old)
    provider.dead = True
    request_id = str(uuid.uuid4())
    response = resume(client, old, request_id=request_id)
    assert response.status_code == 202, response.text
    new = response.json()["id"]
    assert new != old
    assert resume(client, old, request_id=request_id).json()["id"] == new
    assert resume(client, old, "from_beginning", request_id).status_code == 409
    _execute(new)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(new))
        assert row.status == "completed", row.error_code
        state = json.loads(row.analysis_state_json)
        assert state["tool_result"]["operation_id"]
        assert set(state["analysis_summary"]) == set(outputs)
    assert len(provider.commands) == 1
    assert {k: v.id for k, v in outputs.items()} == {
        k: v.id for k, v in _retained_outputs(old).items()
    }


def test_b_starts_new_root_and_preserves_old_results(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    outputs = _retained_outputs(old)
    response = resume(client, old, "from_beginning")
    assert response.status_code == 202, response.text
    assert set(response.json()["analysis"]["output_ids"]) == {
        str(v.id) for v in outputs.values()
    }
    new = response.json()["id"]
    with SessionLocal() as db:
        queued = db.get(Run, uuid.UUID(new))
        assert json.loads(json.loads(queued.analysis_state_json)["journey_json"])["root_run_id"] == new
    _execute(new)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(new))
        assert row.status == "completed", row.error_code
        journey = json.loads(json.loads(row.analysis_state_json)["journey_json"])
        assert journey["root_run_id"] == new
    assert len(provider.commands) == 2
    assert len(provider.created_ids) == 1
    assert {v.id for v in outputs.values()}.isdisjoint(
        {v.id for v in _retained_outputs(new).values()}
    )


@pytest.mark.parametrize("mode", ["last_step", "from_beginning"])
def test_resume_missing_refs_fails_without_recomputation(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
    mode,
):
    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        state = json.loads(row.analysis_state_json)
        journey = json.loads(state["journey_json"])
        target = (
            journey["outputs"][0] if mode == "last_step" else journey["initial_refs"][0]
        )
    from src.openship.artifacts.models import Artifact

    with SessionLocal() as db:
        db.get(Artifact, uuid.UUID(target["id"])).status = "deleted"
        db.commit()
    response = resume(client, old, mode)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "analysis_inputs_unavailable"
    assert len(provider.commands) == 1


def test_resume_requires_authentication(client):
    client.headers.pop("Authorization", None)
    assert resume(client, str(uuid.uuid4())).status_code == 401


def test_a_recovers_committed_operation_after_lost_phase_ack(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    from src.openship.execution.models import ToolOperation
    from src.openship.runs import analysis_graph

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    persist = analysis_graph.persist_analysis_state

    def lose_ack(context, claim, state, update):
        if "tool_result" in update:
            raise RuntimeError("lost phase acknowledgment after operation commit")
        return persist(context, claim, state, update)

    with monkeypatch.context() as patch:
        patch.setattr(analysis_graph, "persist_analysis_state", lose_ack)
        _execute(old)
    with SessionLocal() as db:
        operation_id = db.query(ToolOperation).filter_by(run_id=uuid.UUID(old)).one().id
        assert db.get(Run, uuid.UUID(old)).status == "failed"
    provider.dead = True
    response = resume(client, old)
    assert response.status_code == 202, response.text
    assert response.json()["analysis"]["prior_run_id"] == old
    assert response.json()["analysis"]["retained_error_code"] == "execution_interrupted"
    new = response.json()["id"]
    _execute(new)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(new))
        assert row.status == "completed", row.error_code
        assert json.loads(row.analysis_state_json)["tool_result"][
            "operation_id"
        ] == str(operation_id)
        assert db.query(ToolOperation).filter_by(run_id=row.id).count() == 0
    assert len(provider.commands) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_a_resumes_execute_using_saved_refs_and_scoped_handle(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
    expired,
):
    from datetime import UTC, datetime, timedelta

    from src.openship.execution.gateway import ToolGateway
    from src.openship.providers.errors import ProviderError
    from src.openship.sandboxes.models import WorkflowSandbox

    provider, first = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(first)
    with SessionLocal() as db:
        first_run = db.get(Run, uuid.UUID(first))
        state = json.loads(first_run.analysis_state_json)
        journey = json.loads(state["journey_json"])
        source, log = [r["id"] for r in journey["initial_refs"]]
        conversation = str(first_run.conversation_id)
        if expired:
            pointer = (
                db.query(WorkflowSandbox)
                .filter_by(conversation_id=first_run.conversation_id)
                .one()
            )
            pointer.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            provider.expired_ids.add(pointer.sandbox_id)
            db.commit()
    old = _submit_analysis(client, conversation, source, log, "ERROR")

    async def fail_before_admission(*args, **kwargs):
        raise ProviderError("execution_interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(ToolGateway, "execute_tool", fail_before_admission)
        _execute(old)
    response = resume(client, old)
    assert response.status_code == 202, response.text
    new = response.json()["id"]
    _execute(new)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(new))
        assert row.status == "completed", row.error_code
        assert (
            json.loads(json.loads(row.analysis_state_json)["journey_json"])[
                "root_run_id"
            ]
            == old
        )
    assert len(provider.commands) == 2
    assert len(provider.created_ids) == (2 if expired else 1)


def test_uncertain_operation_blocks_a_and_restart_does_not_dispatch(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    from src.openship.execution.models import ToolOperation

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    with SessionLocal() as db:
        operation = db.query(ToolOperation).filter_by(run_id=uuid.UUID(old)).one()
        operation.status = "interrupted"
        db.commit()
    response = resume(client, old)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "analysis_execution_interrupted"
    assert len(provider.commands) == 1


def test_b_is_blocked_while_previous_command_may_still_be_running(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    from datetime import UTC, datetime, timedelta

    from src.openship.execution.models import ToolOperation
    from src.openship.sandboxes.models import WorkflowSandbox

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        operation = db.query(ToolOperation).filter_by(run_id=row.id).one()
        pointer = (
            db.query(WorkflowSandbox)
            .filter_by(conversation_id=row.conversation_id)
            .one()
        )
        pointer.command_uncertain_until = datetime.now(UTC) + timedelta(minutes=10)
        pointer.command_operation_id = operation.id
        operation.status = "interrupted"
        db.commit()
    response = resume(client, old, "from_beginning")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "analysis_execution_interrupted"
    assert len(provider.commands) == 1


@pytest.mark.parametrize("mode", ["last_step", "from_beginning"])
def test_generated_source_is_reused_only_by_a(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
    mode,
):
    from src.openship.artifacts.contracts import ArtifactRef
    from src.openship.artifacts.models import Artifact
    from src.openship.runs import analysis_graph

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        command = json.loads(row.tool_command_json)
        source = db.get(Artifact, uuid.UUID(command["arguments"].pop("source_id")))
        source_ref = ArtifactRef(
            id=source.id,
            sha256=source.sha256,
            size_bytes=source.size_bytes,
            media_type=source.media_type,
        )
        command["arguments"]["source_prompt"] = "Analyze the selected log"
        row.tool_command_json = json.dumps(command)
        db.commit()
    generated = []

    def generate(*args):
        generated.append(True)
        return source_ref

    monkeypatch.setattr(analysis_graph, "_generate_source", generate)
    _execute(old)
    assert len(generated) == 1
    response = resume(client, old, mode)
    assert response.status_code == 202, response.text
    _execute(response.json()["id"])
    assert len(generated) == (2 if mode == "from_beginning" else 1)
    assert len(provider.commands) == (2 if mode == "from_beginning" else 1)


def test_missing_input_bytes_fail_before_source_generation(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    from src.openship.artifacts.models import Artifact
    from src.openship.runs import analysis_graph

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        command = json.loads(row.tool_command_json)
        command["arguments"].pop("source_id")
        command["arguments"]["source_prompt"] = "Analyze the log"
        input_id = next(iter(command["arguments"]["input_ids"].values()))
        row.tool_command_json = json.dumps(command)
        artifact = db.get(Artifact, uuid.UUID(input_id))
        path = artifact_store.root / artifact.relative_storage_key
        db.commit()
    path.unlink()
    generation_calls = []

    def generate(*args):
        generation_calls.append(True)
        raise RuntimeError("generation should not run for missing bytes")

    monkeypatch.setattr(analysis_graph, "_generate_source", generate)
    _execute(old)
    assert generation_calls == []
    assert provider.commands == []


def test_resume_ownership_and_active_run_admission(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    assert resume(client, old).status_code == 409
    _execute(old)
    response = resume(client, old)
    assert response.status_code == 202
    assert resume(client, old).status_code == 409
    assert len(provider.commands) == 1
    other = client.post(
        "/api/auth/register",
        json={
            "username": "resume_other",
            "email": "resume_other@example.com",
            "password": uuid.uuid4().hex,
        },
    )
    assert other.status_code == 201
    _authorize(client, other.json()["access_token"])
    assert resume(client, old).status_code == 404


def test_cancellation_rejects_late_sandbox_output_without_deleting_handle(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
):
    from src.openship.runs.worker import Worker
    from src.openship.sandboxes.models import WorkflowSandbox
    from tests.integration.test_analysis_results import _claim_one_run

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)

    def cancel():
        response = client.post(f"/api/runs/{old}/cancel")
        assert response.status_code == 200, response.text

    provider.on_command = cancel
    claim = _claim_one_run(old, "cancel-late-worker")
    worker = Worker(
        worker_id=claim.worker_id, session_factory=SessionLocal, checkpointer=None
    )
    try:
        # The cancel command published the terminal event and revoked this fence.
        assert not worker.process_job(claim)
    finally:
        worker.close()
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        assert row.status == "cancelled"
        pointer = (
            db.query(WorkflowSandbox)
            .filter_by(conversation_id=row.conversation_id)
            .one()
        )
        assert pointer.sandbox_id == provider.created_ids[0]
    for output in _retained_outputs(old).values():
        assert output.status != "ready"
        response = client.get(f"/api/artifacts/{output.id}/content")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "artifact_unavailable"
    assert len(provider.commands) == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_a_preserves_digest_bound_review_and_operation_identity(
    client,
    auth_token,
    artifact_store,
    monkeypatch,
    tmp_path,
    accepted,
):
    import hashlib

    from src.openship.execution.models import ToolOperation
    from src.openship.sandboxes import tool
    from src.openship.sandboxes.contracts import ReviewDecision

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(old))
        command = json.loads(row.tool_command_json)
        command["arguments"]["output_expectations"] = {
            "report.txt": "A complete analysis report"
        }
        row.tool_command_json = json.dumps(command)
        db.commit()
    reviews = []

    async def review(*, output, **kwargs):
        digest = hashlib.sha256(output).hexdigest()
        reviews.append(digest)
        return ReviewDecision(
            accepted=accepted, reason="Expectation judgment", output_sha256=digest
        )

    monkeypatch.setattr(tool, "review_source_output", review)
    _execute(old)
    with SessionLocal() as db:
        operation = db.query(ToolOperation).filter_by(run_id=uuid.UUID(old)).one()
        operation_id, result_json = operation.id, operation.result_json
        assert (
            json.loads(result_json)["reviews"]["report.txt"]["output_sha256"]
            == reviews[0]
        )
    provider.dead = True
    response = resume(client, old)
    assert response.status_code == 202, response.text
    new = response.json()["id"]
    _execute(new)
    with SessionLocal() as db:
        row = db.get(Run, uuid.UUID(new))
        assert row.status == ("completed" if accepted else "failed")
        result = json.loads(row.analysis_state_json)["tool_result"]
        assert result["operation_id"] == str(operation_id)
        assert result["result"] == json.loads(result_json)
        assert db.get(ToolOperation, operation_id).result_json == result_json
        assert (
            not result["artifacts"] if not accepted else len(result["artifacts"]) == 2
        )
    assert len(provider.commands) == 1
    assert len(reviews) == 1


def test_a_does_not_repin_changed_output_metadata(
    client, auth_token, artifact_store, monkeypatch, tmp_path,
):
    from src.openship.artifacts.models import Artifact

    provider, old = setup_analysis(client, auth_token, monkeypatch, tmp_path)
    _execute(old)
    output = next(iter(_retained_outputs(old).values()))
    with SessionLocal() as db:
        db.get(Artifact, output.id).sha256 = "b" * 64
        db.commit()
    response = resume(client, old)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "analysis_inputs_unavailable"
    assert len(provider.commands) == 1
