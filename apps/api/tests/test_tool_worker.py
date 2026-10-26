"""Durable manual tool execution, cancellation and recovery behavior."""

import asyncio
import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.auth.models import User
from src.openship.capabilities.catalog import (
    list_capability_manifests,
    list_legacy_search_manifests,
)
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    ToolCommandRequest,
)
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.events.service import Event
from src.openship.execution.models import ExecutionSlot, ToolAttempt, ToolOperation
from src.openship.runs.models import Outbox, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import (
    Claim,
    begin_execution_window,
    claim_next,
    reconcile_expired,
)
from src.openship.runs.service import request_cancel, submit_tool_run, submit_turn
from src.openship.tools.search.contracts import SearchObservation
from src.openship.workspace.models import Project


def _tool_run(db, *, operation_status="admitted", dispatched=False, expired=True):
    manifest = next(
        item for item in list_legacy_search_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    user = User(
        username=f"tool_recovery_{uuid.uuid4().hex[:12]}",
        email=f"tool_recovery_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Tool recovery")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="Untitled"
    )
    db.add(conversation)
    db.flush()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind="tool",
        graph_version=1,
        fence=7,
        execution_configuration=ExecutionConfig(
            graph_kind="tool",
            graph_version=1,
            allowed_capabilities=(ref,),
            credential_binding_refs=(manifest.credential_binding_ref,),
        ).model_dump_json(),
    )
    db.add(run)
    db.flush()
    lease_until = (
        datetime.now(UTC) - timedelta(seconds=1)
        if expired
        else datetime.now(UTC) + timedelta(minutes=1)
    )
    job = RunJob(
        run_id=run.id,
        fence=7,
        owner_id="expired-tool-worker",
        lease_until=lease_until,
    )
    db.add(job)
    operation = ToolOperation(
        run_id=run.id,
        project_id=project.id,
        conversation_id=conversation.id,
        principal_id=user.id,
        logical_call_id="manual:0",
        capability_id=ref.id,
        capability_version=ref.version,
        manifest_digest=ref.manifest_digest,
        arguments_json='{"max_results":1,"query":"recovery test"}',
        arguments_digest="a" * 64,
        request_digest="b" * 64,
        input_artifact_refs="[]",
        credential_binding_ref=manifest.credential_binding_ref,
        configuration_revision=ref.manifest_digest,
        execution_bundle_digest="c" * 64,
        status=operation_status,
    )
    db.add(operation)
    db.flush()
    attempt = None
    if dispatched:
        attempt = ToolAttempt(
            operation_id=operation.id,
            ordinal=1,
            fence=7,
            dispatch_started_at=datetime.now(UTC) - timedelta(seconds=3),
            request_digest="a" * 64,
            outcome="started",
        )
        operation.status = "running"
        db.add(attempt)
    db.commit()
    return user, project, conversation, run, job, operation, attempt


def test_worker_logs_artifact_storage_readiness_failure(caplog, monkeypatch):
    from types import SimpleNamespace

    from src.openship.runs import worker as worker_module
    from src.openship.runs.worker import Worker

    class UnavailableStore:
        def verify(self):
            raise ArtifactStorageError(
                "artifact storage identity marker is unavailable"
            )

    monkeypatch.setattr(worker_module, "configured_store", lambda: UnavailableStore())
    worker = Worker.__new__(Worker)
    worker.Session = SessionLocal
    project_id, conversation_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    context = worker._tool_context(
        claim=None,
        run=SimpleNamespace(id=uuid.uuid4(), project_id=project_id),
        conversation=SimpleNamespace(id=conversation_id, user_id=user_id),
        execution=ExecutionConfig(graph_kind="tool", graph_version=1),
    )

    assert context.artifact_store is None
    assert "Conversation artifact storage is unavailable" in caplog.text
    assert "identity marker is unavailable" in caplog.text


def _submit_search_run(db):
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    user = User(
        username=f"tool_runtime_{uuid.uuid4().hex[:12]}",
        email=f"tool_runtime_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Tool runtime")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="Untitled"
    )
    db.add(conversation)
    db.flush()
    run = submit_tool_run(
        db,
        user.id,
        conversation.id,
        ToolCommandRequest(
            client_request_id=uuid.uuid4(),
            capability=ref,
            arguments={"query": "terraform deployment plan", "max_results": 2},
        ),
    )
    db.commit()
    legacy_manifest = next(
        item for item in list_legacy_search_manifests() if item.id == "brave_web_search"
    )
    legacy_ref = CapabilityRef(
        id=legacy_manifest.id,
        version=legacy_manifest.version,
        manifest_digest=legacy_manifest.manifest_digest,
    )
    return user, project, conversation, run, legacy_ref


def test_expired_tool_run_without_dispatch_requeues_safely():
    db = SessionLocal()
    try:
        _user, _project, _conversation, run, job, operation, _attempt = _tool_run(db)

        reclaimed = reconcile_expired(db)

        db.refresh(run)
        db.refresh(job)
        db.refresh(operation)
        # Reconciliation is installation-wide. Other expired leases in the
        # shared test database may be reclaimed in the same pass, especially
        # after earlier tests' short leases age out during a long CI run.
        assert reclaimed >= 1
        assert run.status == "queued"
        assert job.owner_id is None
        assert job.lease_until is None
        assert operation.status == "admitted"
    finally:
        db.close()


def test_expired_dispatched_tool_is_interrupted_and_never_requeued():
    db = SessionLocal()
    try:
        _user, _project, conversation, run, job, operation, attempt = _tool_run(
            db, dispatched=True
        )

        reclaimed = reconcile_expired(db)

        db.refresh(run)
        db.refresh(job)
        db.refresh(operation)
        db.refresh(attempt)
        # Reconciliation is installation-wide, so another stale lease may be
        # reclaimed during the same pass in a shared test database.
        assert reclaimed >= 1
        assert run.status == "failed"
        assert run.error_code == "execution_interrupted"
        assert operation.status == "interrupted"
        assert operation.error_code == "execution_interrupted"
        assert attempt.outcome == "interrupted"
        assert attempt.error_code == "execution_interrupted"
        assert job.owner_id is None
        assert job.lease_until is None
        event = (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "tool.failed")
            .one()
        )
        assert json.loads(event.payload)["error_code"] == "execution_interrupted"
        assert (
            db.query(Outbox)
            .filter(
                Outbox.conversation_id == conversation.id,
                Outbox.event_id == event.id,
            )
            .count()
            == 1
        )
    finally:
        db.close()


@pytest.mark.parametrize(
    ("operation_status", "dispatched", "expected_status", "expected_code"),
    [
        ("admitted", False, "cancelled", "cancelled"),
        ("running", True, "interrupted", "execution_interrupted"),
    ],
)
def test_cancel_fences_run_and_tool_operation_with_one_outbox_transaction(
    operation_status, dispatched, expected_status, expected_code
):
    db = SessionLocal()
    try:
        user, _project, conversation, run, job, operation, attempt = _tool_run(
            db,
            operation_status=operation_status,
            dispatched=dispatched,
            expired=False,
        )
        old_fence = run.fence
        slot_id = None
        if dispatched:
            # Slot numbers are installation-global; another test may have
            # exercised capacity using this shared SQLite fixture first.
            db.query(ExecutionSlot).delete(synchronize_session=False)
            db.flush()
            slot_id = uuid.uuid4()
            db.add(
                ExecutionSlot(
                    id=slot_id,
                    slot_number=0,
                    operation_id=operation.id,
                    run_id=run.id,
                    fence=old_fence,
                    owner_id=job.owner_id,
                    lease_until=job.lease_until,
                )
            )
            db.commit()

        cancelled = request_cancel(db, user.id, str(run.id))

        db.refresh(job)
        db.refresh(operation)
        assert cancelled.status == "cancelled"
        assert cancelled.fence == old_fence + 1
        assert job.fence == cancelled.fence
        assert operation.status == expected_status
        assert operation.error_code == expected_code
        if attempt is not None:
            db.refresh(attempt)
            assert attempt.outcome == "interrupted"
            assert attempt.error_code == "execution_interrupted"
            slot = db.query(ExecutionSlot).filter(ExecutionSlot.id == slot_id).one()
            assert slot.operation_id is None
        run_event = (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "run_cancelled")
            .one()
        )
        tool_event = (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "tool.cancelled")
            .one()
        )
        assert (
            db.query(Outbox)
            .filter(
                Outbox.conversation_id == conversation.id,
                Outbox.event_id.in_((run_event.id, tool_event.id)),
                Outbox.destination == "event",
            )
            .count()
            == 2
        )
        control = (
            db.query(Outbox)
            .filter(
                Outbox.conversation_id == conversation.id,
                Outbox.event_id == run_event.id,
                Outbox.destination == "control",
            )
            .one()
        )
        assert control.type == "run.cancel"
        assert control.run_id == run.id
        assert db.query(Message).filter(Message.run_id == run.id).count() == 0
    finally:
        db.close()


def test_persisted_tool_deadline_is_reused_after_a_pre_dispatch_reclaim():
    db = SessionLocal()
    try:
        user, _project, _conversation, run, job, _operation, _attempt = _tool_run(
            db, expired=False
        )
        first_claim = Claim(
            run_id=run.id,
            fence=job.fence,
            lease_until=job.lease_until,
            worker_id=job.owner_id,
            user_id=user.id,
        )
        first = begin_execution_window(
            db,
            first_claim,
            deadline_seconds=120,
            graph_thread_id=f"{run.id}-attempt-7",
        )
        assert first is not None

        run.status = "running"
        run.fence = 8
        job.fence = 8
        job.owner_id = "replacement-tool-worker"
        job.lease_until = datetime.now(UTC) + timedelta(minutes=1)
        db.commit()
        second_claim = Claim(
            run_id=run.id,
            fence=8,
            lease_until=job.lease_until,
            worker_id=job.owner_id,
            user_id=user.id,
        )
        second = begin_execution_window(
            db,
            second_claim,
            deadline_seconds=900,
            graph_thread_id=f"{run.id}-attempt-8",
        )

        db.refresh(run)
        assert second == first
        assert run.graph_thread_id == f"{run.id}-attempt-8"
    finally:
        db.close()


def test_worker_runs_manual_search_without_model_and_preserves_first_chat_title(
    monkeypatch, tmp_path
):
    from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
    from src.openship.chat.routes import get_conversation_messages
    from src.openship.runs import dispatch as dispatch_module
    from src.openship.runs import worker as worker_module
    from src.openship.runs.worker import Worker

    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    user, _project, conversation, tool_run, ref = _submit_search_run(db)
    db.query(RunJob).filter(RunJob.run_id != tool_run.id).delete(
        synchronize_session=False
    )
    db.commit()
    claim = claim_next(db, worker_id="manual-tool-worker", lease_seconds=30)
    assert claim is not None and claim.run_id == tool_run.id

    store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
    store.initialize()
    secret = "worker-runtime-credential-sentinel"
    monkeypatch.setattr(worker_module, "credential_value", lambda binding: secret)
    monkeypatch.setattr(worker_module, "configured_store", lambda: store)
    calls = []

    async def search(arguments, invocation, _control):
        calls.append(arguments.query)
        assert invocation.credential_resolver("BRAVE_SEARCH_API_KEY") == secret
        return SearchObservation(
            tool=ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=1,
            items=(
                {
                    "title": "Terraform module guidance",
                    "url": "https://example.test/terraform",
                    "snippet": "A bounded search result.",
                    "rank": 1,
                },
            ),
            truncated=False,
            provider_metadata={},
        )

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=SessionLocal,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim)
        db.expire_all()
        stored_tool_run = db.get(Run, tool_run.id)
        operation = (
            db.query(ToolOperation).filter(ToolOperation.run_id == tool_run.id).one()
        )
        attempt = (
            db.query(ToolAttempt).filter(ToolAttempt.operation_id == operation.id).one()
        )
        assert stored_tool_run.status == "completed"
        assert stored_tool_run.provider_started_at is None
        assert stored_tool_run.execution_started_at is not None
        assert stored_tool_run.execution_deadline_at is not None
        assert operation.status == "succeeded"
        assert attempt.outcome == "succeeded"
        assert calls == ["terraform deployment plan"]
        assert db.query(Message).filter(Message.run_id == tool_run.id).count() == 0
        assert (
            db.query(ProviderAttempt)
            .filter(ProviderAttempt.run_id == tool_run.id)
            .count()
            == 0
        )
        tool_events = db.query(Event).filter(Event.run_id == tool_run.id).all()
        assert any(event.type == "tool.completed" for event in tool_events)
        assert any(event.type == "run.succeeded" for event in tool_events)
        assert all(secret not in event.payload for event in tool_events)
        assert db.query(Outbox).filter(
            Outbox.conversation_id == conversation.id
        ).count() >= len(tool_events)

        chat_run = submit_turn(
            db,
            user.id,
            str(conversation.id),
            "What should I do next?",
            str(uuid.uuid4()),
            provider_profile_id="primary-fixture",
        )
        db.query(RunJob).filter(RunJob.run_id != chat_run.id).delete(
            synchronize_session=False
        )
        db.commit()
        chat_claim = claim_next(db, worker_id="first-chat-worker", lease_seconds=30)
        assert chat_claim is not None and chat_claim.run_id == chat_run.id
        prompt_was_first = []
        system_prompts = []

        class FakeChatGraph:
            def invoke(self, state, config):
                prompt_was_first.append("## Title:" in state["messages"][0]["content"])
                system_prompts.append(state["messages"][0]["content"])
                return {
                    "assistant_response": "Review the plan.\n\n## Title: After manual search",
                    "usage": None,
                    "finish_reason": "stop",
                }

        def fake_chat_factory(_checkpointer, invocation_factory=None):
            return FakeChatGraph()

        monkeypatch.setitem(
            dispatch_module._GRAPH_FACTORIES, ("chat", 1), fake_chat_factory
        )
        assert worker.process_job(chat_claim)
        db.expire_all()
        assert prompt_was_first == [True]
        assert "choose one provider" in system_prompts[0]
        assert (
            "do not repeat an equivalent successful search" in system_prompts[0].lower()
        )
        assert db.get(Conversation, conversation.id).title == "After manual search"
        assert (
            db.query(Message)
            .filter(
                Message.run_id == chat_run.id,
                Message.role == "assistant",
            )
            .one()
            .content
            == "Review the plan."
        )
        history = asyncio.run(get_conversation_messages(conversation.id, db, user))
        assert [
            (message["role"], message["run_id"])
            for message in history
            if message["run_id"] == str(chat_run.id)
        ] == [
            ("user", str(chat_run.id)),
            ("assistant", str(chat_run.id)),
        ]
    finally:
        worker.engine.dispose()
        db.close()


def test_worker_cancellation_stops_active_tool_io_within_two_seconds(
    monkeypatch, tmp_path
):
    from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
    from src.openship.runs.worker import Worker

    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "worker_cancellation_poll_seconds", 0.05)
    db = SessionLocal()
    user, _project, conversation, run, _ref = _submit_search_run(db)
    db.query(RunJob).filter(RunJob.run_id != run.id).delete(synchronize_session=False)
    db.commit()
    claim = claim_next(db, worker_id="cancel-tool-worker", lease_seconds=30)
    assert claim is not None and claim.run_id == run.id

    store = LocalArtifactStore(tmp_path / "cancel-artifacts", uuid.uuid4())
    store.initialize()
    monkeypatch.setattr(
        "src.openship.runs.worker.credential_value", lambda _binding: "test-key"
    )
    monkeypatch.setattr("src.openship.runs.worker.configured_store", lambda: store)
    started = threading.Event()
    supervisor_observed_owner = threading.Event()

    async def waiting_search(_arguments, _invocation, control):
        started.set()
        while True:
            await asyncio.sleep(0.01)
            control.raise_if_stopped()

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", waiting_search)
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=SessionLocal,
        checkpointer=None,
    )
    original_ownership_lost = worker._ownership_lost

    def track_supervisor_ownership(db_session, run_id, fence):
        ownership_lost = original_ownership_lost(db_session, run_id, fence)
        if (
            threading.current_thread() is not threading.main_thread()
            and not ownership_lost
        ):
            supervisor_observed_owner.set()
        return ownership_lost

    monkeypatch.setattr(worker, "_ownership_lost", track_supervisor_ownership)
    results = []
    thread = threading.Thread(target=lambda: results.append(worker.process_job(claim)))
    thread.start()
    try:
        assert started.wait(timeout=2)
        assert supervisor_observed_owner.wait(timeout=2)
        cancellation_started = time.monotonic()
        cancelled = request_cancel(db, user.id, str(run.id))
        thread.join(timeout=2)
        elapsed = time.monotonic() - cancellation_started

        assert cancelled.status == "cancelled"
        assert not thread.is_alive()
        assert elapsed < 2
        assert results == [False]
        db.expire_all()
        stored_run = db.get(Run, run.id)
        operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
        assert stored_run.status == "cancelled"
        assert operation.status == "interrupted"
        output = (
            db.query(Artifact)
            .filter(Artifact.originating_operation_id == operation.id)
            .one()
        )
        assert output.status == "deleted"
        assert output.reserved_bytes == 0
        assert (
            db.query(ArtifactQuota)
            .filter(
                ArtifactQuota.conversation_id == conversation.id,
                ArtifactQuota.scope == "conversation",
                ArtifactQuota.reserved_bytes > 0,
            )
            .count()
            == 0
        )
        assert db.query(Message).filter(Message.run_id == run.id).count() == 0
    finally:
        if thread.is_alive():
            worker.running = False
            thread.join(timeout=2)
        worker.engine.dispose()
        db.close()


def test_pre_dispatch_worker_restart_reuses_deadline_and_executes_once(
    monkeypatch, tmp_path
):
    from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
    from src.openship.runs.worker import Worker

    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    _user, _project, _conversation, run, _ref = _submit_search_run(db)
    db.query(RunJob).filter(RunJob.run_id != run.id).delete(synchronize_session=False)
    db.commit()
    first_claim = claim_next(db, worker_id="crashed-tool-worker", lease_seconds=30)
    assert first_claim is not None and first_claim.run_id == run.id
    first_window = begin_execution_window(
        db,
        first_claim,
        deadline_seconds=120,
        graph_thread_id=f"{run.id}-{first_claim.checkpoint_ns}",
    )
    assert first_window is not None
    job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    assert reconcile_expired(db) == 1
    replacement_claim = claim_next(
        db, worker_id="replacement-tool-worker", lease_seconds=30
    )
    assert replacement_claim is not None and replacement_claim.run_id == run.id

    store = LocalArtifactStore(tmp_path / "restart-artifacts", uuid.uuid4())
    store.initialize()
    monkeypatch.setattr(
        "src.openship.runs.worker.credential_value", lambda _binding: "test-key"
    )
    monkeypatch.setattr("src.openship.runs.worker.configured_store", lambda: store)
    calls = []

    async def search(arguments, _invocation, _control):
        calls.append(arguments.query)
        return SearchObservation(
            tool=_ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=0,
            items=(),
            truncated=False,
            provider_metadata={},
        )

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", search)
    worker = Worker(
        worker_id=replacement_claim.worker_id,
        session_factory=SessionLocal,
        checkpointer=None,
    )
    try:
        assert worker.process_job(replacement_claim)
        db.expire_all()
        stored = db.get(Run, run.id)
        assert stored.status == "completed"
        assert stored.execution_started_at.replace(tzinfo=UTC) == first_window[0]
        assert stored.execution_deadline_at.replace(tzinfo=UTC) == first_window[1]
        assert stored.graph_thread_id == f"{run.id}-{replacement_claim.checkpoint_ns}"
        assert calls == ["terraform deployment plan"]
        operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
        assert operation.status == "succeeded"
    finally:
        worker.engine.dispose()
        db.close()
