"""Real PostgreSQL proof for a model-free worker invocation."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_legacy_search_manifests
from src.openship.capabilities.contracts import CapabilityRef, ToolCommandRequest
from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.runs.models import ProviderAttempt, Run
from src.openship.runs.queue import claim_next
from src.openship.runs.service import submit_tool_run
from src.openship.runs.worker import Worker
from src.openship.tools.search.contracts import SearchObservation
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; tool worker requires PostgreSQL")

    database_name = f"phase4_tool_worker_{uuid.uuid4().hex[:8]}"
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
    ini = tmp_path_factory.mktemp("phase4-tool-worker") / "alembic.ini"
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
    engine = create_engine(lane_url, pool_size=8, max_overflow=4)
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


def test_postgres_worker_completes_manual_search_without_model_or_chat_message(
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
    db = pg_sessions()
    user = User(
        username=f"tool_worker_{uuid.uuid4().hex[:12]}",
        email=f"tool_worker_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Tool worker")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Untitled")
    db.add(conversation)
    db.flush()
    run = submit_tool_run(
        db,
        user.id,
        conversation.id,
        ToolCommandRequest(
            client_request_id=uuid.uuid4(),
            capability=ref,
            arguments={"query": "postgres worker test", "max_results": 1},
        ),
    )
    db.commit()
    claim = claim_next(db, worker_id="postgres-tool-worker", lease_seconds=30)
    assert claim is not None and claim.run_id == run.id

    store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
    store.initialize()
    monkeypatch.setattr(
        "src.openship.runs.worker.credential_value",
        lambda _binding: "synthetic-search-credential",
    )
    monkeypatch.setattr("src.openship.runs.worker.configured_store", lambda: store)
    seen = []

    async def synthetic_search(arguments, _context, _control):
        seen.append(arguments.query)
        return SearchObservation(
            tool=ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=1,
            items=(
                {
                    "title": "PostgreSQL worker proof",
                    "url": "https://example.test/postgres",
                    "snippet": "Synthetic retained output.",
                    "rank": 1,
                },
            ),
            truncated=False,
            provider_metadata={},
        )

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", synthetic_search)
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=pg_sessions,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim)
        db.expire_all()
        stored_run = db.get(Run, run.id)
        operation = db.query(ToolOperation).filter(ToolOperation.run_id == run.id).one()
        attempt = db.query(ToolAttempt).filter(
            ToolAttempt.operation_id == operation.id
        ).one()
        artifact = db.query(Artifact).filter(
            Artifact.originating_operation_id == operation.id
        ).one()
        assert stored_run.status == "completed"
        assert stored_run.provider_started_at is None
        assert stored_run.execution_deadline_at is not None
        assert operation.status == "succeeded"
        assert attempt.outcome == "succeeded"
        assert artifact.status == "ready"
        assert seen == ["postgres worker test"]
        assert db.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).count() == 0
        assert db.query(Message).filter(Message.run_id == run.id).count() == 0
    finally:
        worker.engine.dispose()
        db.close()
