"""PostgreSQL lane for durable task state, fences and downgrade protection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.chat.models import Conversation
from src.openship.execution.models import ToolOperation
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.tasks.contracts import TaskPlan, TaskStep
from src.openship.runs.tasks.models import (
    TaskDecisionCommandRecord,
    TaskDecisionRecord,
    TaskRecord,
)
from src.openship.runs.tasks.state import (
    TaskStateError,
    read_progress,
    record_attempt_result,
    save_plan,
)
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]

CAPABILITY = CapabilityRef(
    id="python_execute", version="1.0.0", manifest_digest="b" * 64
)
LANE_DATABASE = "phase7a_task_state_lane"


def _alembic_ini(path: Path, url: str) -> Path:
    api_root = Path(__file__).parents[2]
    ini = path / "alembic.ini"
    ini.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {url.replace('%', '%%')}
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
    return ini


def _run_alembic(ini: Path, *args: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def pg_lane(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip(
            "no TEST_POSTGRES_URL; task-state PostgreSQL proof is blocked on a "
            "disposable PostgreSQL service"
        )
    bounded = bounded_postgres_url(service_url)
    parsed = make_url(bounded)
    lane_url = parsed.set(database=LANE_DATABASE).render_as_string(
        hide_password=False
    )
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{LANE_DATABASE}"'))
    finally:
        admin.dispose()

    ini = _alembic_ini(tmp_path_factory.mktemp("phase7a-task-state"), lane_url)
    built = _run_alembic(ini, "upgrade", "head")
    assert built.returncode == 0, built.stderr
    engine = create_engine(lane_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions, ini, engine
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
        finally:
            admin.dispose()


def _identity(db):
    suffix = uuid.uuid4().hex
    user = User(
        username=f"taskstate_pg_{suffix}",
        email=f"taskstate_pg_{suffix}@example.test",
        password_hash="hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name=f"task state pg {suffix}")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="task state pg"
    )
    db.add(conversation)
    db.flush()
    lease_until = datetime.now(UTC) + timedelta(minutes=5)
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        kind="chat",
        graph_version=5,
        fence=1,
    )
    db.add(run)
    db.flush()
    db.add(
        RunJob(
            run_id=run.id,
            fence=1,
            owner_id="pg-state-worker",
            lease_until=lease_until,
        )
    )
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=lease_until,
        worker_id="pg-state-worker",
        user_id=user.id,
    )
    return user, run, claim


def _plan(revision: int = 1, *, intent: str = "Produce the requested chart.") -> TaskPlan:
    step = TaskStep(
        id="inspect",
        intent="Inspect the supplied source",
        capability=CAPABILITY,
        profile_id="logs-data",
        expected_outputs=("chart.png",),
        completion_checks=("chart exists",),
    )
    return TaskPlan(
        revision=revision,
        intent=intent,
        steps=(step,),
        available_capabilities=(CAPABILITY,),
        available_profiles=("logs-data",),
        status="validated",
    )


def _operation(db, run: Run, user: User, *, result_digest: str = "c" * 64) -> ToolOperation:
    operation = ToolOperation(
        run_id=run.id,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        principal_id=user.id,
        logical_call_id=f"model:0:call:{uuid.uuid4().hex}",
        capability_id="python_execute",
        capability_version="1.0.0",
        manifest_digest=CAPABILITY.manifest_digest,
        arguments_json="{}",
        arguments_digest="d" * 64,
        request_digest="e" * 64,
        input_artifact_refs="[]",
        configuration_revision="1",
        execution_bundle_digest="f" * 64,
        status="succeeded",
        result_json='{"ok": true}',
        result_digest=result_digest,
        output_artifact_ids="[]",
    )
    db.add(operation)
    db.flush()
    artifact = Artifact(
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        originating_run_id=run.id,
        originating_operation_id=operation.id,
        kind="analysis_output",
        media_type="image/png",
        display_name="chart.png",
        size_bytes=12,
        sha256="1" * 64,
        relative_storage_key=f"task-state-pg/{uuid.uuid4().hex}",
        status="ready",
        source="tool",
    )
    db.add(artifact)
    db.flush()
    operation.output_artifact_ids = json.dumps([str(artifact.id)])
    db.flush()
    return operation


def test_postgres_state_is_fenced_idempotent_and_atomic(pg_lane):
    sessions, _, _ = pg_lane
    db = sessions()
    try:
        user, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        operation = _operation(db, run, user)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    # A stale fence must not advance the same task.
    stale = Claim(
        run_id=run_id,
        fence=0,
        lease_until=claim.lease_until,
        worker_id=claim.worker_id,
        user_id=claim.user_id,
    )
    db = sessions()
    try:
        with pytest.raises(TaskStateError) as fenced:
            save_plan(db, stale, _plan(revision=2), expected_revision=1)
        assert fenced.value.code == "stale_claim"
        db.rollback()
    finally:
        db.close()

    barrier = threading.Barrier(2)
    results = []

    def publish():
        session = sessions()
        try:
            barrier.wait(timeout=15)
            progress = record_attempt_result(
                session,
                claim,
                step_id="inspect",
                attempt=1,
                operation_id=operation.id,
            )
            session.commit()
            results.append(progress)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: publish(), range(2)))

    db = sessions()
    try:
        stored = read_progress(db, run_id)
        assert len(stored.steps[0].attempts) == 1
        assert stored.steps[0].attempts[0].operation_id == operation.id
        assert len(results) == 2
    finally:
        db.close()

    # Rollback leaves neither the state nor its event visible.
    db = sessions()
    try:
        save_plan(db, claim, _plan(revision=2, intent="next"), expected_revision=1)
        db.flush()
        db.rollback()
    finally:
        db.close()
    db = sessions()
    try:
        assert read_progress(db, run_id).current_revision == 1
        events = db.execute(
            text(
                "SELECT COUNT(*) FROM events WHERE run_id=:run AND type='task.plan_saved'"
            ),
            {"run": run_id},
        ).scalar_one()
        assert events == 1
    finally:
        db.close()


def test_postgres_waiting_statuses_and_open_decision_uniqueness(pg_lane):
    sessions, _, _ = pg_lane
    db = sessions()
    try:
        _, run, claim = _identity(db)
        run_id = run.id
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        task = db.query(TaskRecord).filter(TaskRecord.run_id == run_id).one()
        task_id = task.id

        for status in ("waiting_plan", "waiting_input", "waiting_config"):
            db.execute(
                text("UPDATE runs SET status=:status WHERE id=:id"),
                {"status": status, "id": run_id},
            )
        db.commit()
        expires_at = datetime.now(UTC) + timedelta(hours=24)
        decision = TaskDecisionRecord(
            task_id=task_id,
            run_id=run_id,
            request_id=uuid.uuid4(),
            kind="plan_confirmation",
            plan_revision=1,
            expected_digest="a" * 64,
            state="open",
            request_json="{}",
            expires_at=expires_at,
        )
        db.add(decision)
        db.commit()

        # At most one open decision per run.
        db.add(
            TaskDecisionRecord(
                task_id=task_id,
                run_id=run_id,
                request_id=uuid.uuid4(),
                kind="clarification",
                plan_revision=1,
                expected_digest="a" * 64,
                state="open",
                request_json="{}",
                expires_at=expires_at,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Decision commands are idempotent per run/client request identity.
        client_request_id = uuid.uuid4()
        db.add(
            TaskDecisionCommandRecord(
                task_id=task_id,
                run_id=run_id,
                decision_id=decision.id,
                client_request_id=client_request_id,
                request_id=decision.request_id,
                action="confirm",
                plan_revision=1,
                expected_digest="a" * 64,
                result_json="{}",
            )
        )
        db.commit()

        db.add(
            TaskDecisionCommandRecord(
                task_id=task_id,
                run_id=run_id,
                decision_id=decision.id,
                client_request_id=client_request_id,
                request_id=decision.request_id,
                action="confirm",
                plan_revision=1,
                expected_digest="a" * 64,
                result_json="{}",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # A distinct client request id is a distinct command.
        db.add(
            TaskDecisionCommandRecord(
                task_id=task_id,
                run_id=run_id,
                decision_id=decision.id,
                client_request_id=uuid.uuid4(),
                request_id=decision.request_id,
                action="confirm",
                plan_revision=1,
                expected_digest="a" * 64,
                result_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()


def test_postgres_downgrade_refuses_v5_rows(pg_lane):
    sessions, ini, engine = pg_lane
    db = sessions()
    try:
        _, run, _ = _identity(db)
        run_id = run.id
        db.commit()
    finally:
        db.close()

    down = _run_alembic(ini, "downgrade", "phase6b030")
    assert down.returncode != 0
    assert "refusing to downgrade" in down.stderr.lower()
    assert "runs" in set(inspect(engine).get_table_names())
    assert "task_records" in set(inspect(engine).get_table_names())
    assert run_id is not None
