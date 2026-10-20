"""PostgreSQL recovery and cancellation fences for model-free tool runs."""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.models import ArtifactQuota
from src.openship.artifacts.service import reserve_artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ExecutionConfig
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.events.service import Event
from src.openship.execution.models import ExecutionSlot, ToolAttempt, ToolOperation
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import (
    begin_execution_window,
    claim_next,
    reconcile_expired,
)
from src.openship.runs.service import request_cancel
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; tool recovery requires PostgreSQL")

    database_name = f"phase4_tool_recovery_{uuid.uuid4().hex[:8]}"
    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    lane_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-tool-recovery") / "alembic.ini"
    ini.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {lane_url.replace('%', '%%')}

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    )
    built = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    engine = create_engine(lane_url, pool_size=6, max_overflow=4)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        finally:
            admin.dispose()


def _tool_run(db, *, status="running", expired=True, dispatched=False):
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
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
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Untitled")
    db.add(conversation)
    db.flush()
    execution = ExecutionConfig(
        graph_kind="tool",
        graph_version=1,
        allowed_capabilities=(ref,),
        credential_binding_refs=(manifest.credential_binding_ref,),
    )
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status=status,
        kind="tool",
        graph_version=1,
        fence=7,
        execution_configuration=execution.model_dump_json(),
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
        owner_id="old-tool-worker",
        lease_until=lease_until,
    )
    db.add(job)
    db.flush()
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
        status="running" if dispatched else "admitted",
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
            request_digest="d" * 64,
            outcome="started",
        )
        db.add(attempt)
        db.add(
            ExecutionSlot(
                slot_number=0,
                operation_id=operation.id,
                run_id=run.id,
                fence=7,
                owner_id=job.owner_id,
                lease_until=job.lease_until,
            )
        )
    db.flush()
    return user, project, conversation, run, job, operation, attempt, ref


def test_postgres_pre_dispatch_reclaim_preserves_the_execution_deadline(pg_sessions):
    db = pg_sessions()
    try:
        _user, _project, _conversation, run, _job, operation, _attempt, _ref = _tool_run(
            db, status="queued", expired=True
        )
        db.commit()
        first_claim = claim_next(db, "first-tool-worker", lease_seconds=30)
        assert first_claim is not None
        first_window = begin_execution_window(
            db,
            first_claim,
            deadline_seconds=120,
            graph_thread_id=f"{run.id}-attempt-8",
        )
        assert first_window is not None
        db.query(RunJob).filter(RunJob.run_id == run.id).update(
            {RunJob.lease_until: datetime.now(UTC) - timedelta(seconds=1)},
            synchronize_session=False,
        )
        db.commit()

        assert reconcile_expired(db) == 1
        replacement = claim_next(db, "replacement-tool-worker", lease_seconds=30)
        assert replacement is not None and replacement.run_id == run.id
        second_window = begin_execution_window(
            db,
            replacement,
            deadline_seconds=900,
            graph_thread_id=f"{run.id}-attempt-{replacement.fence}",
        )

        db.refresh(run)
        db.refresh(operation)
        assert second_window == first_window
        assert run.graph_thread_id == f"{run.id}-attempt-{replacement.fence}"
        assert operation.status == "admitted"
    finally:
        db.close()


def test_postgres_dispatched_expiry_fails_once_and_publishes_recovery_events(pg_sessions):
    db = pg_sessions()
    try:
        _user, _project, conversation, run, job, operation, attempt, _ref = _tool_run(
            db, dispatched=True
        )
        db.commit()

        assert reconcile_expired(db) == 1

        db.refresh(run)
        db.refresh(job)
        db.refresh(operation)
        db.refresh(attempt)
        assert run.status == "failed"
        assert run.error_code == "execution_interrupted"
        assert operation.status == "interrupted"
        assert attempt.outcome == "interrupted"
        assert job.owner_id is None and job.lease_until is None
        tool_event = db.query(Event).filter(
            Event.run_id == run.id, Event.type == "tool.failed"
        ).one()
        run_event = db.query(Event).filter(
            Event.run_id == run.id, Event.type == "run.failed"
        ).one()
        assert json.loads(tool_event.payload)["error_code"] == "execution_interrupted"
        assert db.query(Outbox).filter(
            Outbox.conversation_id == conversation.id,
            Outbox.event_id.in_((tool_event.id, run_event.id)),
        ).count() == 2
    finally:
        db.close()


def test_postgres_cancel_moves_both_fences_and_releases_unpublished_output(pg_sessions):
    db = pg_sessions()
    try:
        user, project, conversation, run, job, operation, attempt, _ref = _tool_run(
            db, expired=False, dispatched=True
        )
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            settings.artifact_search_reservation_bytes,
            "search_result",
            str(uuid.uuid4()),
            display_name="search-results.json",
            media_type="application/json",
            source="tool",
            originating_run_id=run.id,
            originating_operation_id=operation.id,
        )
        old_fence = run.fence
        db.commit()

        cancelled = request_cancel(db, user.id, str(run.id))

        db.refresh(job)
        db.refresh(operation)
        db.refresh(attempt)
        db.refresh(artifact)
        assert cancelled.fence == old_fence + 1
        assert job.fence == cancelled.fence
        assert job.owner_id is None and job.lease_until is None
        assert operation.status == "interrupted"
        assert attempt.outcome == "interrupted"
        assert artifact.status == "deleted" and artifact.reserved_bytes == 0
        assert db.query(ArtifactQuota).filter(
            ArtifactQuota.conversation_id == conversation.id,
            ArtifactQuota.scope == "conversation",
            ArtifactQuota.reserved_bytes > 0,
        ).count() == 0
        event_types = {
            event.type
            for event in db.query(Event).filter(Event.run_id == run.id).all()
        }
        assert {"tool.cancelled", "run_cancelled"} <= event_types
        assert db.query(Outbox).filter(
            Outbox.conversation_id == conversation.id
        ).count() >= 2
    finally:
        db.close()
