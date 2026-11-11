"""The v5 task graph runs one admitted request to durable task progress.

Every case drives the compiled graph itself (``runs.dispatch`` resolves it only
as ``("chat", 5)`` against an ``ExecutionConfigV5`` snapshot), so the records
under test are the ones the worker would write: task, plan revision, call
binding, committed ``ToolOperation``, published artifacts, step attempt,
transcript turn. The sandbox and HTTP legs of a capability already have their
own tests, so the gateway here is a lane that performs the real authority check
(``admit_operation``), commits a real operation and stages real bytes for the
artifacts it records: a case about progress is never a case about transport,
and a verdict here is only ever a reading of those records.

Verification verdicts come from Task 12's authority, and this lane uses it: the
``verify`` node asks for one step's verdict per pass and commits it, so a step
whose declared outputs really are published, ready and backed by stored bytes
becomes verified, and an approved plan that covers every step ends completed on
the records - not because any answer said so.

One deferral stays honest here: uncertain-operation diagnosis and the abort/repeat/continue
choice are Task 15's, so uncertain work stays visibly blocked with no automatic
repeat or skip.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
    PublishedToolResult,
    ToolInvocationContext,
    canonical_digest,
)
from src.openship.capabilities.policy import read_effective_tool_policy
from src.openship.config import settings
from src.openship.execution.admission import admit_operation
from src.openship.execution.models import ToolOperation
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import RunLimits, RunModelConfig
from src.openship.providers.errors import ProviderError
from src.openship.providers.runner import authorize_current_profile
from src.openship.runs.budget import RunBudget
from src.openship.runs.dispatch import UnsupportedGraphVersion, resolve_graph
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim, claim_next
from src.openship.runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskContext,
    TaskDecisionCommand,
    TaskLimits,
    TaskPlan,
    TaskStep,
)
from src.openship.runs.tasks.decisions import apply_decision
from src.openship.runs.tasks.models import (
    TaskDecisionRecord,
    TaskRecord,
    TaskStepAttempt,
)
from src.openship.runs.tasks.state import read_plan, read_progress, save_plan
from src.openship.sandboxes.profile_schema import ProfileCatalog, catalog_digest
from src.openship.sandboxes.profiles import (
    install_runtime_catalog,
    reset_runtime_catalog,
)
from tests.conftest import TestSessionLocal
from tests.providers.conftest import (
    control,
    provider_stream,
    sdk_tool_chunk,
    serve_requests,
)
from tests.test_agent_tool_loop import _tool_profile
from tests.test_task_profile_selection import _all_profiles, _catalog
from tests.test_task_state import _identity

pytestmark = []

INTENT = "Chart the supplied observations and report their averages."
CAPABILITY_IDS = ("download_file", "python_execute")

OBSERVATIONS = "observations.csv"
CHART = "chart.png"
AVERAGES = "averages.json"
OBSERVATION_BYTES = b"timestamp,value\n2024-01-01,1.5\n2024-01-02,2.0\n"
CHART_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-chart"
AVERAGE_BYTES = b'{"averages": {"mean": 3.5}}'

#: The bytes each capability publishes, as the plan's declared outputs name them.
PUBLISHED_OUTPUTS = {
    "download_file": (
        (OBSERVATIONS, "cloud_observation", "text/plain", OBSERVATION_BYTES),
    ),
    "python_execute": (
        (CHART, "analysis_output", "image/png", CHART_BYTES),
        (AVERAGES, "analysis_output", "application/json", AVERAGE_BYTES),
    ),
}


def _artifact_volume(monkeypatch) -> LocalArtifactStore:
    """This lane's artifact volume, with the installation identity it carries."""
    root = Path(tempfile.mkdtemp()) / "artifact-volume"
    monkeypatch.setattr(settings, "artifact_root", root)
    monkeypatch.setattr(settings, "artifact_storage_id", uuid.uuid4())
    store = LocalArtifactStore(root, settings.artifact_storage_id)
    store.initialize()
    return store


def _publish(
    db, store: LocalArtifactStore, *, scope, operation, name, kind, media_type, data
) -> str:
    """Real staged bytes for one declared output, recorded on that operation."""
    project_id, conversation_id, run_id = scope
    artifact_id = uuid.uuid4()
    reference = ArtifactRef(
        id=artifact_id,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )
    staged = store.stage(
        artifact_id,
        [data],
        reference,
        project_id=project_id,
        conversation_id=conversation_id,
    )
    store.publish_file(staged, f"{artifact_id}/content")
    db.add(
        Artifact(
            id=artifact_id,
            project_id=project_id,
            conversation_id=conversation_id,
            originating_run_id=run_id,
            originating_operation_id=operation.id,
            kind=kind,
            media_type=media_type,
            display_name=name,
            size_bytes=len(data),
            sha256=reference.sha256,
            relative_storage_key=f"{artifact_id}/content",
            status="ready",
            source="tool",
        )
    )
    db.flush()
    return str(artifact_id)


def _refs() -> tuple[CapabilityRef, ...]:
    """The real pinned references for the two capabilities a task needs."""
    wanted = set(CAPABILITY_IDS)
    return tuple(
        CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        for manifest in list_capability_manifests()
        if manifest.id in wanted
    )


def _assessment_payload() -> dict:
    """One assessment envelope: the request is actionable and nothing is missing."""
    return {
        "assessment": {
            "classification": "task",
            "explanation": "The observations can be charted and averaged as supplied.",
            "evidence_refs": [],
            "missing_information": [],
            "failure": "none",
            "step_id": None,
            "unknowns": [],
            "risks": [],
            "recommended_recovery_action": None,
        }
    }


def _plan_payload() -> dict:
    """A plan draft naming capabilities by id; the server rebuilds each ref."""
    return {
        "assessment": _assessment_payload()["assessment"],
        "plan": {
            "intent": INTENT,
            "constraints": ["keep the recorded units"],
            "steps": [
                {
                    "id": "fetch",
                    "intent": "Download the supplied observations",
                    "depends_on": [],
                    "capability": "download_file",
                    "profile_id": None,
                    "expected_outputs": ["observations.csv"],
                    "completion_checks": ["observations.csv is staged"],
                },
                {
                    "id": "analyze",
                    "intent": "Chart the observations and report their averages",
                    "depends_on": ["fetch"],
                    "capability": "python_execute",
                    "profile_id": "standard",
                    "expected_outputs": ["chart.png", "averages.json"],
                    "completion_checks": ["the chart and averages both exist"],
                },
            ],
        },
    }


def _answer(payload: dict) -> dict:
    """One inference request answered with this envelope."""
    return {
        "chunks": provider_stream("openai", text=[json.dumps(payload)], finish="stop")
    }


def _prose(text: str) -> dict:
    """One request answered in prose: no tool call, so no step work."""
    return {"chunks": provider_stream("openai", text=[text], finish="stop")}


def _call(call_id: str, name: str, arguments: dict) -> dict:
    """One model request answered with a single native tool call."""
    return {
        "chunks": [
            sdk_tool_chunk(
                fragments=[
                    {
                        "index": 0,
                        "id": call_id,
                        "name": name,
                        "arguments": json.dumps(arguments),
                    }
                ],
                finish_reason="tool_calls",
            )
        ]
    }


def _journey() -> list[dict]:
    """Assessment, plan, then one tool call per plan step, in plan order."""
    return [
        _answer(_assessment_payload()),
        _answer(_plan_payload()),
        _call(
            "provider-fetch",
            "download_file",
            {"url": "https://observations.test/day.csv"},
        ),
        _call(
            "provider-analyze",
            "python_execute",
            {"profile_id": "standard", "source": "pass"},
        ),
    ]


class _CommittedGateway:
    """Real authority, real durable operation: the result is only what it says."""

    def __init__(
        self,
        *,
        principal_id,
        project_id,
        conversation_id,
        run_id,
        store: LocalArtifactStore,
        sessions=TestSessionLocal,
    ):
        self.calls: list[dict] = []
        self.store = store
        self.scope = (project_id, conversation_id, run_id)
        self.sessions = sessions
        self.principal_id = principal_id
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.crash_after_admission = False

    def _result_for(self, capability_id: str, arguments: dict) -> dict:
        if capability_id == "download_file":
            return {
                "url": arguments["url"],
                "stored_alias": "observations.csv",
                "size_bytes": 128,
                "media_type": "text/plain",
            }
        return {
            "exit_code": 0,
            "outputs": ["chart.png", "averages.json"],
            "stdout": "averages computed",
        }

    async def execute_tool(self, **request):
        self.calls.append(request)
        ref = request["ref"]
        claim = request["claim"]
        arguments = request["arguments"]
        authority = AuthorizationContext(
            principal_id=self.principal_id,
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            action="execute_tool",
            target_id=str(self.run_id),
        )
        with self.sessions() as db:
            operation = admit_operation(
                db,
                claim,
                authority,
                request["logical_call_id"],
                ref,
                arguments,
                request["inputs"],
            )
            if self.crash_after_admission:
                # The admit is committed and the worker then dies: the call may
                # have run, so nothing here may settle an outcome.
                db.commit()
                raise ProviderError(
                    "execution_interrupted",
                    safe_message="the step stopped before its result was committed",
                )
            result = self._result_for(ref.id, arguments)
            operation.output_artifact_ids = json.dumps(
                [
                    _publish(
                        db,
                        self.store,
                        scope=self.scope,
                        operation=operation,
                        name=name,
                        kind=kind,
                        media_type=media_type,
                        data=data,
                    )
                    for name, kind, media_type, data in PUBLISHED_OUTPUTS[ref.id]
                ]
            )
            operation.status = "succeeded"
            operation.result_json = json.dumps(result)
            operation.result_digest = canonical_digest(result)
            db.commit()
            return PublishedToolResult(
                operation_id=operation.id,
                status="succeeded",
                result=result,
            )


def _graph_node_names(execution) -> frozenset:
    """The fixed topology this installation builds for a v5 snapshot."""
    graph = resolve_graph("chat", 5, execution)(
        checkpointer=None,
        gateway_factory=None,
        invocation_factory=lambda _state: None,
    )
    return frozenset(graph.builder.nodes)


class Lane:
    """One seeded v5 chat root: pinned snapshot, live claim, durable gateway.

    The pinned snapshot is the one an admission would have written: its own
    task context, catalog and ceilings. A lane can hand its run back through
    the queue or re-read the lease it still holds, which is how a case states
    what a restarted worker would see.
    """

    def __init__(
        self,
        monkeypatch,
        *,
        mode="auto",
        behaviors=None,
        crash=False,
        sessions=None,
    ):
        profile = _tool_profile()
        declared = profile.model_dump(
            mode="json", exclude={"limits", "profile_revision"}
        )
        monkeypatch.setattr(settings, "model_profiles_json", json.dumps([declared]))
        monkeypatch.setattr(settings, "model_default_profile_id", profile.id)
        monkeypatch.setenv(profile.credential_ref, "synthetic-model-credential")
        monkeypatch.setattr(settings, "tool_enabled_ids", ",".join(CAPABILITY_IDS))
        monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
        monkeypatch.setattr(settings, "sandbox_deployment_id", "task-lane-deployment")
        self.store = _artifact_volume(monkeypatch)

        self.sessions = sessions or TestSessionLocal
        self.catalog = _catalog(_all_profiles())
        install_runtime_catalog(self.catalog)

        with self.sessions() as db:
            policy = read_effective_tool_policy(db)
            assert policy.state_available
            self.policy_revision = policy.revision
            user, project, conversation, run, claim = _identity(db)
            self.user_id = user.id
            self.project_id = project.id
            self.conversation_id = conversation.id
            self.run_id = run.id
            self.claim = claim
            self.execution = _v5_execution(
                (run.id, project.id, conversation.id), user.id, self.catalog, mode=mode
            )
            run.execution_configuration = self.execution.model_dump_json()
            run.tool_policy_revision = self.policy_revision
            db.commit()

        self.model_config = RunModelConfig(
            schema_version=1,
            primary=profile,
            limits=RunLimits(max_output_tokens=4_000, max_attempts=6),
            binding_origin="submission",
        )
        self.transport = serve_requests(monkeypatch, behaviors) if behaviors else None
        self.gateway = _CommittedGateway(
            sessions=self.sessions,
            store=self.store,
            principal_id=self.user_id,
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            run_id=self.run_id,
        )
        self.gateway.crash_after_admission = crash
        self.state = {
            "run_id": str(self.run_id),
            "conversation_id": str(self.conversation_id),
            "execution_configuration": self.execution.model_dump_json(),
            "model_configuration": self.model_config.model_dump_json(),
            "tool_policy_revision": self.policy_revision,
        }
        self.rebind()

    def rebind(self, claim=None):
        if claim is not None:
            self.claim = claim
        self.invocation = SimpleNamespace(
            claim=self.claim,
            control=control(),
            recorder=DatabaseAttemptRecorder(
                lambda: self.sessions(),
                self.claim.run_id,
                self.claim.fence,
                commit_each=True,
            ),
            budget=RunBudget(
                self.sessions,
                self.claim,
                RunLimits(max_attempts=12, max_output_tokens=4_000),
                self.execution,
            ),
            session_factory=self.sessions,
            authorize_profile=authorize_current_profile,
            context=_tool_context(self),
        )

    def invoke(self) -> dict:
        graph = resolve_graph("chat", 5, self.execution)(
            checkpointer=None,
            gateway_factory=lambda: self.gateway,
            invocation_factory=lambda _state: self.invocation,
        )
        return graph.invoke(
            self.state, config={"configurable": {"thread_id": str(self.run_id)}}
        )

    def resume_queue(self, worker_id: str) -> None:
        """Take this run back through the queue, the way the worker does.

        The SQLite database is shared across test modules, so older queued
        jobs from other lanes are parked first: the queue is global, and this
        lane's claim has to name its own run.
        """
        with self.sessions() as db:
            stale = [
                job.run_id
                for job in db.query(RunJob)
                .join(Run, Run.id == RunJob.run_id)
                .filter(Run.status == "queued", Run.id != self.run_id)
                .all()
            ]
            if stale:
                db.query(RunJob).filter(RunJob.run_id.in_(stale)).update(
                    {
                        RunJob.owner_id: "prior-lane",
                        RunJob.lease_until: datetime.now(UTC) + timedelta(minutes=5),
                    },
                    synchronize_session=False,
                )
            taken = claim_next(db, worker_id, lease_seconds=600)
        assert taken is not None, "the queue did not hand the confirmed run back"
        assert taken.run_id == self.run_id, "the queue returned another lane's run"
        self.rebind(claim=taken)

    def rehold(self, worker_id: str | None = None) -> None:
        """Re-read the lease this run still holds, as a restarted worker does."""
        with self.sessions() as db:
            run = db.query(Run).filter_by(id=self.run_id).one()
            job = db.query(RunJob).filter_by(run_id=self.run_id).one()
            fence, owner, lease = run.fence, job.owner_id, job.lease_until
        self.rebind(
            claim=Claim(
                run_id=self.run_id,
                fence=fence,
                lease_until=lease or (datetime.now(UTC) + timedelta(minutes=5)),
                worker_id=worker_id or owner or "state-worker",
                user_id=self.user_id,
            )
        )

    def confirm_pending(self, *, action: str = "confirm") -> tuple:
        """Consume the one open decision with its exact request and digest."""
        with self.sessions() as db:
            open_rows = db.query(TaskDecisionRecord).filter_by(
                run_id=self.run_id, state="open"
            )
            assert open_rows.count() == 1, "the run must offer exactly one open request"
            open_request = open_rows.one()
            identity = (
                open_request.request_id,
                open_request.plan_revision,
                open_request.expected_digest,
                open_request.kind,
            )
            apply_decision(
                db,
                principal_id=self.user_id,
                run_id=self.run_id,
                command=TaskDecisionCommand(
                    client_request_id=uuid.uuid4(),
                    request_id=identity[0],
                    plan_revision=identity[1],
                    expected_digest=identity[2],
                    action=action,
                ),
                now=datetime.now(UTC),
            )
            db.commit()
        return identity


def _tool_context(lane) -> ToolInvocationContext:
    """The pinned authority a task step dispatches under: this run's scope."""
    return ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=lane.user_id,
            project_id=lane.project_id,
            conversation_id=lane.conversation_id,
            action="execute_tool",
            target_id=str(lane.run_id),
        ),
        execution=lane.execution,
        credential_resolver=lambda _binding: "synthetic-tool-credential",
    )


def _v5_execution(
    scope: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    initiator_id: uuid.UUID,
    catalog: ProfileCatalog,
    *,
    mode: str = "auto",
) -> ExecutionConfigV5:
    """The snapshot an admission pins for this run: intent, tools, ceilings."""
    run_id, project_id, conversation_id = scope
    return ExecutionConfigV5(
        allowed_capabilities=_refs(),
        aws_read_catalog_digest="e" * 64,
        task_context=TaskContext(
            root_run_id=run_id,
            project_id=project_id,
            conversation_id=conversation_id,
            initiator_id=initiator_id,
            scope="conversation",
            mode=mode,
            graph_version=5,
            original_intent=INTENT,
        ),
        profile_catalog=catalog,
        profile_catalog_digest=catalog_digest(catalog),
        task_limits=TaskLimits(),
        loop_limits=LoopLimits(
            deadline_seconds=900,
            max_tool_calls=8,
            max_model_turns=8,
            max_generation_attempts=12,
            max_output_tokens_per_request=4_000,
            max_total_output_tokens=48_000,
        ),
    )


def _durable(run_id, sessions=TestSessionLocal) -> dict:
    """What the durable records say, read back with no graph state at all."""
    with sessions() as db:
        progress = read_progress(db, run_id)
        plan = read_plan(db, run_id)
        task = db.query(TaskRecord).filter_by(run_id=run_id).one_or_none()
        return {
            "progress": progress,
            "plan": plan,
            "operations": [
                SimpleNamespace(
                    id=row.id,
                    capability_id=row.capability_id,
                    logical_call_id=row.logical_call_id,
                    status=row.status,
                )
                for row in db.query(ToolOperation).filter_by(run_id=run_id).all()
            ],
            "attempts": [
                SimpleNamespace(
                    step_id=row.step_id, ordinal=row.ordinal, status=row.status
                )
                for row in db.query(TaskStepAttempt).filter_by(run_id=run_id).all()
            ],
            "decisions": [
                SimpleNamespace(
                    request_id=row.request_id,
                    kind=row.kind,
                    state=row.state,
                    plan_revision=row.plan_revision,
                    expected_digest=row.expected_digest,
                )
                for row in db.query(TaskDecisionRecord).filter_by(run_id=run_id).all()
            ],
            "task_status": task.status if task is not None else None,
        }


def _run_state(run_id, sessions=TestSessionLocal) -> dict:
    """Where the run record itself stands, read without any graph state."""
    with sessions() as db:
        run = db.query(Run).filter_by(id=run_id).one()
        return {"status": run.status, "fence": run.fence}


def _calls(lane) -> list[str]:
    """The capabilities this lane's gateway was actually asked to dispatch."""
    return [request["ref"].id for request in lane.gateway.calls]


def _dispatch_counts(operations) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in operations:
        counts[row.capability_id] = counts.get(row.capability_id, 0) + 1
    return counts


def _step_attempts(attempts) -> dict[str, str]:
    return {row.step_id: row.status for row in attempts}


@pytest.fixture(autouse=True)
def close_task_graph_lanes():
    yield
    reset_runtime_catalog()


def test_copilot_no_dispatch_before_exact_confirmation(monkeypatch):
    lane = Lane(monkeypatch, mode="copilot", behaviors=_journey())
    parked = lane.invoke()
    assert parked["parked"] == "plan_confirmation"

    pre_confirmation_dispatches = len(lane.gateway.calls)
    assert pre_confirmation_dispatches == 0
    before = _durable(lane.run_id)
    assert before["operations"] == []
    assert before["attempts"] == []
    assert before["progress"].call_bindings == ()
    assert before["progress"].status == "waiting"
    assert before["progress"].pending_decision is not None
    assert len(before["decisions"]) == 1
    assert _run_state(lane.run_id)["status"] == "waiting_plan"

    request_id, plan_revision, expected_digest, kind = lane.confirm_pending()
    assert (plan_revision, kind) == (1, "plan_confirmation")
    lane.resume_queue("copilot-worker")
    lane.invoke()

    assert _calls(lane) == list(CAPABILITY_IDS)
    after = _durable(lane.run_id)
    assert after["progress"].pending_decision is None
    assert len(after["decisions"]) == 1
    assert after["decisions"][0].state == "confirmed"
    assert after["decisions"][0].request_id == request_id
    assert after["decisions"][0].expected_digest == expected_digest
    assert len(after["operations"]) == 2
    assert [row.logical_call_id for row in after["operations"]] == [
        binding.logical_call_id for binding in after["progress"].call_bindings
    ]


def test_auto_announces_plan_then_downloads_executes_and_verifies(monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey())
    outcome = lane.invoke()

    capability_calls = _calls(lane)
    assert capability_calls == ["download_file", "python_execute"]
    assert outcome["assistant_response"].startswith("Plan revision 1:")
    assert outcome["cursor"]["verification_recorded"] == ["fetch", "analyze"]

    durable = _durable(lane.run_id)
    assert _dispatch_counts(durable["operations"]) == {
        "download_file": 1,
        "python_execute": 1,
    }
    assert _step_attempts(durable["attempts"]) == {
        "fetch": "succeeded",
        "analyze": "succeeded",
    }
    assert [row.logical_call_id for row in durable["operations"]] == [
        binding.logical_call_id for binding in durable["progress"].call_bindings
    ]
    # Task 12's verdicts are what make this completed: each step's declared
    # outputs are published by its own committed operation, ready, and their
    # stored bytes still prove the recorded identity, so every plan step is
    # verified and nothing else had to be believed.
    assert durable["progress"].verified_steps == ("fetch", "analyze")
    assert len(durable["progress"].verified_outputs) == 3
    assert durable["task_status"] == "completed"
    assert durable["progress"].status == "completed"
    assert durable["decisions"] == []


def test_one_confirmation_allows_multiple_steps(monkeypatch):
    lane = Lane(monkeypatch, mode="copilot", behaviors=_journey())
    assert lane.invoke()["parked"] == "plan_confirmation"
    assert len(lane.gateway.calls) == 0

    lane.confirm_pending()
    lane.resume_queue("copilot-worker")
    lane.invoke()

    capability_calls = _calls(lane)
    assert capability_calls == ["download_file", "python_execute"]
    durable = _durable(lane.run_id)
    # Both steps ran on one plan revision under a single confirmation: the run
    # never re-asked, replanned or parked again on its own.
    assert len(durable["decisions"]) == 1
    assert durable["plan"].revision == 1
    assert len(durable["attempts"]) == 2
    assert durable["progress"].verified_steps == ("fetch", "analyze")
    assert durable["progress"].status == "completed"
    assert lane.transport.requests == 4


def test_committed_download_replayed_without_refetch_after_restart(monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey())
    lane.invoke()
    before_crash = _durable(lane.run_id)
    assert _dispatch_counts(before_crash["operations"]) == {
        "download_file": 1,
        "python_execute": 1,
    }
    downloaded_before = {row.id for row in before_crash["operations"]}
    assert before_crash["progress"].pending_decision is None

    # A restarted worker re-reads the same lease and re-invokes the graph with
    # no usable checkpoint: everything it acts on is what the records already
    # say, so nothing already attempted is re-run and nothing re-decided.
    lane.rehold()
    outcome = lane.invoke()

    after_restart = _durable(lane.run_id)
    assert {row.id for row in after_restart["operations"]} == downloaded_before
    assert _dispatch_counts(after_restart["operations"]) == {
        "download_file": 1,
        "python_execute": 1,
    }
    assert _step_attempts(after_restart["attempts"]) == {
        "fetch": "succeeded",
        "analyze": "succeeded",
    }
    # The restarted invocation inspected nothing again: both verdicts are
    # already durable, so the switch ends the run from the records alone.
    assert outcome["cursor"].get("verification_inspected") is None
    assert outcome["cursor"]["next_action"] == "finish"
    assert after_restart["progress"].verified_steps == ("fetch", "analyze")
    assert after_restart["task_status"] == before_crash["task_status"]
    assert after_restart["task_status"] == "completed"
    assert after_restart["plan"].revision == before_crash["plan"].revision
    assert len(lane.gateway.calls) == 2
    assert lane.transport.requests == 4


def test_assistant_final_text_does_not_complete_missing_outputs(monkeypatch):
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[
            _answer(_assessment_payload()),
            _answer(_plan_payload()),
            _call(
                "provider-fetch",
                "download_file",
                {"url": "https://observations.test/day.csv"},
            ),
            _prose("The chart is finished."),
        ],
    )
    outcome = lane.invoke()

    assert _calls(lane) == ["download_file"]
    assert outcome["cursor"]["stopped"] == "no_tool_call"
    assert outcome["cursor"]["verification_recorded"] == ["fetch"]

    durable = _durable(lane.run_id)
    assert _step_attempts(durable["attempts"]) == {"fetch": "succeeded"}
    assert len(durable["operations"]) == 1
    # The download earned a verdict because its own operation published a ready
    # artifact whose stored bytes still prove the identity; the chart has no
    # such evidence, so the task stays partial, and naming a deliverable in
    # prose is neither completion nor the published message.
    assert durable["progress"].verified_steps == ("fetch",)
    assert durable["progress"].skipped_steps == ()
    assert durable["task_status"] == "verification_pending"
    assert outcome["assistant_response"].startswith("Plan revision 1:")
    assert "The chart is finished." not in outcome["assistant_response"]


def test_lost_uncommitted_approval_shows_same_plan_without_restart(monkeypatch):
    lane = Lane(monkeypatch, mode="copilot", behaviors=_journey()[:2])
    parked = lane.invoke()
    assert parked["parked"] == "plan_confirmation"
    assert len(lane.gateway.calls) == 0
    before = _durable(lane.run_id)
    assert before["plan"].revision == 1
    assert lane.transport.requests == 2
    assert _run_state(lane.run_id)["status"] == "waiting_plan"

    # The approval never landed: the transaction that would have recorded it
    # rolled back, so the run is still waiting on the same request. A restarted
    # worker re-reads the same lease and re-invokes the graph.
    lane.rehold()
    outcome = lane.invoke()

    assert outcome["parked"] == "plan_confirmation"
    assert lane.transport.requests == 2
    assert len(lane.gateway.calls) == 0

    after = _durable(lane.run_id)
    assert after["plan"].revision == before["plan"].revision
    assert len(after["decisions"]) == 1
    assert after["decisions"][0].request_id == before["decisions"][0].request_id
    assert (
        after["decisions"][0].expected_digest == before["decisions"][0].expected_digest
    )
    assert after["operations"] == []
    assert after["attempts"] == []
    assert after["progress"].call_bindings == ()
    assert after["progress"].status == "waiting"


def test_plan_growth_uses_fixed_nodes_and_persisted_step_progress(monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey())
    lane.invoke()
    node_names_before_growth = _graph_node_names(lane.execution)
    before = _durable(lane.run_id)
    assert before["plan"].revision == 1

    lane.rehold()
    with TestSessionLocal() as db:
        save_growth(db, lane.claim, lane.catalog)
        db.commit()
        grown = _durable(lane.run_id)

    # Plan steps and revisions are durable data, not generated nodes: the same
    # fixed topology serves the grown plan, and the steps already attempted
    # stay recorded under it.
    node_names_after_growth = _graph_node_names(lane.execution)
    assert node_names_after_growth == node_names_before_growth
    assert grown["plan"].revision == 2
    assert grown["progress"].step_ids == ("fetch", "analyze", "publish")
    assert {row.step_id for row in grown["attempts"]} == {"fetch", "analyze"}
    assert grown["progress"].call_bindings == before["progress"].call_bindings

    # A v5 run whose pinned snapshot is not v5 blocks instead of falling back.
    legacy = ExecutionConfig(
        graph_kind="chat",
        graph_version=4,
        allowed_capabilities=_refs(),
    )
    with pytest.raises(UnsupportedGraphVersion):
        resolve_graph("chat", 5, legacy)
    with pytest.raises(UnsupportedGraphVersion):
        resolve_graph("chat", 5, None)


def save_growth(db, claim, catalog) -> None:
    """The server's own grown plan: the published steps plus one more."""
    prior = read_plan(db, claim.run_id)
    if prior is None:
        raise AssertionError("growth needs the plan the run already published")
    current = read_progress(db, claim.run_id)
    steps = tuple(
        TaskStep(
            id=step.id,
            intent=step.intent,
            depends_on=step.depends_on,
            capability=step.capability,
            profile_id=step.profile_id,
            expected_outputs=step.expected_outputs,
            completion_checks=step.completion_checks,
        )
        for step in prior.steps
    ) + (
        TaskStep(
            id="publish",
            intent="Publish the chart and the averages",
            depends_on=("analyze",),
            expected_outputs=("summary.json",),
            completion_checks=("summary published",),
        ),
    )
    save_plan(
        db,
        claim,
        TaskPlan(
            revision=prior.revision + 1,
            intent=INTENT,
            constraints=prior.constraints,
            steps=steps,
            available_capabilities=_refs(),
            available_profiles=tuple(sorted(catalog.profiles)),
            status="validated",
        ),
        expected_revision=current.current_revision,
    )


def test_call_binding_survives_dispatch_without_result_checkpoint(monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey(), crash=True)
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    with TestSessionLocal() as db:
        operations = db.query(ToolOperation).filter_by(run_id=lane.run_id).all()
        assert len(operations) == 1
        unsettled = operations[0]
        progress = read_progress(db, lane.run_id)
    assert unsettled.status == "admitted"
    assert unsettled.logical_call_id == progress.call_bindings[-1].logical_call_id
    assert progress.attempts == ()
    assert len(progress.call_bindings) == 1

    # The binding is what makes the next reader honest: it asks nothing new,
    # dispatches nothing, and parks the durable recovery decision around the
    # identity it could not settle.
    lane.rehold()
    resumed = lane.invoke()
    assert resumed["parked"] == "recovery"
    assert _calls(lane) == ["download_file"]
    assert lane.transport.requests == 3
    durable = _durable(lane.run_id)
    assert durable["attempts"] == []
    assert durable["task_status"] == "waiting"
    assert len(durable["operations"]) == 1
    pending = durable["progress"].pending_decision
    assert pending is not None
    assert pending.kind == "recovery"
    assert pending.recovery_report is not None


def _refused_plan_payload() -> dict:
    """A plan draft naming a capability this run was never given."""
    payload = _plan_payload()
    payload["plan"]["steps"][0]["capability"] = "send_email"
    return payload


def test_assessment_and_plan_see_prior_conversation_not_system_prompt(monkeypatch):
    lane = Lane(monkeypatch, behaviors=[_answer(_assessment_payload()), _answer(_plan_payload())])
    lane.state["messages"] = [
        {"role": "system", "content": "TITLE GENERATION SECRET"},
        {"role": "user", "content": "Earlier requested a weather chart"},
        {"role": "assistant", "content": "Earlier plan downloaded the forecast"},
        {"role": "user", "content": lane.execution.task_context.original_intent},
    ]

    lane.invoke()

    for request in lane.transport.seen[:2]:
        body = json.dumps(request["messages"])
        assert "Earlier requested a weather chart" in body
        assert "Earlier plan downloaded the forecast" in body
        assert "TITLE GENERATION SECRET" not in body


def test_step_request_receives_bounded_recent_conversation(monkeypatch):
    lane = Lane(monkeypatch, behaviors=_journey())
    lane.state["messages"] = [
        {"role": "system", "content": "hidden system"},
        *[{"role": "user", "content": f"older-{i}-" + "é" * 3000} for i in range(25)],
        {"role": "assistant", "content": "most recent context"},
        {"role": "user", "content": lane.execution.task_context.original_intent},
    ]

    lane.invoke()

    body = json.dumps(lane.transport.seen[2]["messages"], ensure_ascii=False)
    assert "most recent context" in body
    assert "older-0-" not in body
    assert "hidden system" not in body
    assert len(body.encode("utf-8")) < 30000


def test_initial_clarification_parks_without_a_prior_plan(monkeypatch):
    assessment = _assessment_payload()
    assessment["assessment"].update(
        classification="clarification",
        explanation="The requested script is not identified.",
        missing_information=["Which script?"],
    )
    lane = Lane(monkeypatch, behaviors=[_answer(assessment)])

    outcome = lane.invoke()

    assert outcome["parked"] == "clarification"
    assert lane.gateway.calls == []


def test_a_refused_plan_answers_the_request_instead_of_completing_in_silence(monkeypatch):
    """A plan the server refuses must leave the person reading something usable.

    The refusals are real: the dev-stack QA run
    ``e54c2653-122a-4868-be96-1331899a96b1`` had both plan drafts refused and
    completed as an empty assistant message with no task, no plan and no visible
    reason. The graph's answer node is the surface that tells the user what
    happened; completing in silence is not a result.
    """
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[
            _answer(_assessment_payload()),
            _answer(_refused_plan_payload()),
            _answer(_refused_plan_payload()),
        ],
    )
    outcome = lane.invoke()

    assert outcome["cursor"]["stopped"] == "invalid_response"
    response = outcome["assistant_response"].strip()
    assert response
    assert "plan" in response.lower()
    assert lane.gateway.calls == []
    assert lane.transport.requests == 3

    durable = _durable(lane.run_id)
    assert durable["plan"] is None
    assert durable["operations"] == []
    assert durable["task_status"] is None
