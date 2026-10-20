"""PostgreSQL proof that durable tool rows rebuild snapshots after pruning."""

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

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.chat.models import Conversation
from src.openship.events.service import read_snapshot
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.runs.models import Event, Run
from src.openship.tools.search.contracts import SearchToolResult
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; tool snapshots require PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    lane_name = "phase4_tool_snapshot_lane"
    lane_url = parsed.set(database=lane_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
            connection.execute(text(f'CREATE DATABASE "{lane_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-tool-snapshot") / "alembic.ini"
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
    engine = create_engine(lane_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
        finally:
            admin.dispose()


def test_pruned_tool_events_rebuild_operation_and_artifact_projection(pg_sessions):
    db = pg_sessions()
    try:
        suffix = uuid.uuid4().hex
        user = User(
            username=f"tool_snapshot_{suffix[:12]}",
            email=f"tool_snapshot_{suffix}@example.test",
            password_hash="hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Tool snapshot")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Search")
        db.add(conversation)
        db.flush()
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            kind="tool",
            graph_version=1,
            status="completed",
            fence=1,
        )
        db.add(run)
        db.flush()

        manifest = next(item for item in list_capability_manifests() if item.id == "tavily_web_search")
        observed_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        artifact_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        artifact = Artifact(
            id=artifact_id,
            project_id=project.id,
            conversation_id=conversation.id,
            originating_run_id=run.id,
            originating_operation_id=operation_id,
            kind="search_result",
            media_type="application/json",
            display_name="search-results.json",
            size_bytes=128,
            sha256="a" * 64,
            relative_storage_key=f"projects/{project.id}/conversations/{conversation.id}/artifacts/{artifact_id}/content",
            status="ready",
            mutability="immutable",
            source="tavily_web_search",
            created_at=observed_at,
        )
        result = SearchToolResult(
            operation_id=str(operation_id),
            tool={"id": manifest.id, "version": manifest.version, "manifest_digest": manifest.manifest_digest},
            observed_at=observed_at,
            query="terraform state migration",
            items=({
                "title": "Terraform state",
                "url": "https://docs.example.test/state",
                "snippet": "State migration reference.",
                "rank": 1,
            },),
            artifact={
                "id": artifact_id,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "media_type": artifact.media_type,
            },
        )
        operation = ToolOperation(
            id=operation_id,
            run_id=run.id,
            project_id=project.id,
            conversation_id=conversation.id,
            principal_id=user.id,
            logical_call_id=f"manual-{suffix}",
            capability_id=manifest.id,
            capability_version=manifest.version,
            manifest_digest=manifest.manifest_digest,
            arguments_json='{"query":"terraform state migration","max_results":1}',
            arguments_digest="b" * 64,
            request_digest="c" * 64,
            configuration_revision="phase4a",
            execution_bundle_digest="d" * 64,
            status="succeeded",
            result_json=result.model_dump_json(),
            result_digest="e" * 64,
            output_artifact_ids=f'["{artifact_id}"]',
            created_at=observed_at,
            updated_at=observed_at,
            finished_at=observed_at,
        )
        attempt = ToolAttempt(
            operation_id=operation_id,
            ordinal=1,
            fence=1,
            outcome="succeeded",
            request_digest="c" * 64,
            response_digest="f" * 64,
            dispatch_started_at=observed_at,
            finished_at=observed_at,
        )
        event = Event(
            project_id=project.id,
            conversation_id=conversation.id,
            run_id=run.id,
            sequence=0,
            type="tool.completed",
            payload="{}",
        )
        conversation.next_event_sequence = 1
        db.add(operation)
        db.flush()
        db.add(artifact)
        db.add_all([attempt, event])
        db.commit()
        db.delete(event)
        db.commit()

        snapshot = read_snapshot(db, user.id, str(conversation.id))

        assert snapshot.events == []
        assert snapshot.runs[0].kind == "tool"
        assert snapshot.runs[0].operations[0].result["query"] == "terraform state migration"
        assert snapshot.runs[0].operations[0].attempts[0].outcome == "succeeded"
        assert snapshot.artifacts[0].id == artifact_id
    finally:
        db.close()
