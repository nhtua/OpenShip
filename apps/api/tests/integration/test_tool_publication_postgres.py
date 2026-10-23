"""PostgreSQL proof that artifact, result and events become visible together."""

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

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_legacy_search_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.events.service import Event
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.providers.control import InvocationControl
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.tools.search.contracts import SearchObservation
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; tool publication requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database_name = "phase4_tool_publication_lane"
    lane_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-tool-publication") / "alembic.ini"
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


@pytest.mark.asyncio
async def test_postgres_publishes_ready_artifact_result_and_events_in_one_commit(
    pg_sessions, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    manifest = next(
        item for item in list_legacy_search_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    execution = ExecutionConfig(
        graph_kind="tool",
        graph_version=1,
        allowed_capabilities=(ref,),
        credential_binding_refs=(manifest.credential_binding_ref,),
    )
    setup = pg_sessions()
    user = User(
        username=f"publication_{uuid.uuid4().hex[:12]}",
        email=f"publication_{uuid.uuid4().hex}@example.test",
        password_hash="hash",
    )
    setup.add(user)
    setup.flush()
    project = Project(owner_user_id=user.id, name="Publication lane")
    setup.add(project)
    setup.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Publication lane")
    setup.add(conversation)
    setup.flush()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind="tool",
        graph_version=1,
        fence=1,
        execution_configuration=execution.model_dump_json(),
    )
    setup.add(run)
    setup.flush()
    job = RunJob(
        run_id=run.id,
        fence=1,
        owner_id="publication-worker",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    setup.add(job)
    setup.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=job.lease_until,
        worker_id=job.owner_id,
        user_id=user.id,
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        ),
        execution=execution,
        credential_resolver=lambda binding: "postgres-test-sentinel"
        if binding == manifest.credential_binding_ref
        else None,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4()),
    )
    context.artifact_store.initialize()
    run_id = run.id
    conversation_id = conversation.id
    setup.close()

    async def fake_search(arguments, _context, _control):
        return SearchObservation(
            tool=ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=1,
            items=(
                {
                    "title": "PostgreSQL publication",
                    "url": "https://example.test/publication",
                    "snippet": "The result is retained.",
                    "rank": 1,
                },
            ),
            truncated=False,
            provider_metadata={},
        )

    import src.openship.execution.gateway as gateway_module

    monkeypatch.setitem(
        TOOL_HANDLER_OVERRIDES,
        "brave_web_search",
        fake_search,
    )
    original_publish = gateway_module.publish_tool_result
    uncommitted_state = []

    def inspect_before_commit(db, active_claim, attempt_id, observation, staged_outputs):
        result = original_publish(
            db, active_claim, attempt_id, observation, staged_outputs
        )
        reader = pg_sessions()
        try:
            operation = reader.query(ToolOperation).filter(
                ToolOperation.run_id == run_id
            ).one()
            artifact = reader.query(Artifact).filter(
                Artifact.originating_operation_id == operation.id
            ).one()
            completed_events = reader.query(Event).filter(
                Event.run_id == run_id,
                Event.type == "tool.completed",
            ).count()
            uncommitted_state.append(
                (operation.status, artifact.status, completed_events)
            )
        finally:
            reader.close()
        return result

    monkeypatch.setattr(gateway_module, "publish_tool_result", inspect_before_commit)
    result = await ToolGateway(pg_sessions).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:0",
        ref=ref,
        arguments={"query": "postgres publication test", "max_results": 2},
        control=InvocationControl(),
    )

    assert result.status == "succeeded"
    assert uncommitted_state == [("running", "staging", 0)]
    db = pg_sessions()
    try:
        operation = db.query(ToolOperation).filter(ToolOperation.run_id == run_id).one()
        attempt = db.query(ToolAttempt).filter(
            ToolAttempt.operation_id == operation.id
        ).one()
        artifact = db.query(Artifact).filter(
            Artifact.originating_operation_id == operation.id
        ).one()
        assert operation.status == "succeeded"
        assert attempt.outcome == "succeeded"
        assert artifact.status == "ready"
        assert db.query(Event).filter(
            Event.run_id == run_id, Event.type == "tool.completed"
        ).count() == 1
        assert db.query(Outbox).filter(
            Outbox.conversation_id == conversation_id,
            Outbox.type == "tool.completed",
        ).count() == 1
    finally:
        db.close()
