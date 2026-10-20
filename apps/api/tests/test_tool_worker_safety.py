"""Queued but unimplemented run graphs must fail without model dispatch."""

import uuid

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ToolCommandRequest
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Event, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import claim_next
from src.openship.runs.service import submit_tool_run
from src.openship.runs.worker import Worker
from src.openship.workspace.models import Project


def test_unregistered_tool_graph_fails_without_model_or_synthetic_chat(monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex
        user = User(username=f"tool_worker_{suffix}", email=f"{suffix}@example.test", password_hash="hash")
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Tool worker")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Untitled")
        db.add(conversation)
        db.commit()

        manifest = list_capability_manifests()[0]
        command = ToolCommandRequest(
            client_request_id=uuid.uuid4(),
            capability=CapabilityRef(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
            ),
            arguments={"query": "no model should run", "max_results": 1},
        )
        run = submit_tool_run(db, user.id, conversation.id, command)
        db.commit()
        db.query(RunJob).filter(RunJob.run_id != run.id).delete(synchronize_session=False)
        db.commit()
        claim = claim_next(db, "tool-worker", lease_seconds=30)
        assert claim is not None

        worker = Worker(worker_id=claim.worker_id, session_factory=SessionLocal, checkpointer=None)
        try:
            assert worker.process_job(claim)
        finally:
            worker.engine.dispose()

        db.expire_all()
        stored = db.query(Run).filter(Run.id == run.id).one()
        assert stored.status == "failed"
        assert stored.error_code == "unsupported_graph_version"
        assert db.query(Message).filter(Message.run_id == run.id).count() == 0
        assert db.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).count() == 0
        terminal = db.query(Event).filter(Event.run_id == run.id, Event.type == "run.failed").one()
        assert "unsupported_graph_version" in terminal.payload
    finally:
        db.close()
