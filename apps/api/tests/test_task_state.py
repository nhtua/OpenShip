"""Durable task state: fenced plans, committed operation outcomes and migration safety.

The shared SQLite schema is built by the autouse ``setup_db`` fixture. The
migration cases below build their own temporary database because they need
to inspect the pre-upgrade history.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.chat.models import Conversation
from src.openship.execution.models import ToolOperation
from src.openship.runs.models import Event, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.tasks.contracts import (
    TaskPlan,
    TaskStep,
)
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
from tests.conftest import TestSessionLocal

CAPABILITY = CapabilityRef(
    id="python_execute", version="1.0.0", manifest_digest="b" * 64
)

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")


def _identity(db):
    suffix = uuid.uuid4().hex[:12]
    user = User(
        username=f"taskstate_{suffix}",
        email=f"taskstate_{suffix}@example.test",
        password_hash="hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name=f"task state {suffix}")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id, project_id=project.id, title="task state"
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
            owner_id="state-worker",
            lease_until=lease_until,
        )
    )
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=lease_until,
        worker_id="state-worker",
        user_id=user.id,
    )
    return user, project, conversation, run, claim


def _plan(
    revision: int = 1,
    *,
    status: str = "validated",
    intent: str = "Produce the requested chart.",
) -> TaskPlan:
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
        status=status,
    )


def _operation(
    db,
    run: Run,
    user: User,
    *,
    status: str = "succeeded",
    result_digest: str | None = "c" * 64,
    arguments_digest: str = "d" * 64,
    principal_id: uuid.UUID | None = None,
) -> ToolOperation:
    operation = ToolOperation(
        run_id=run.id,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        principal_id=principal_id or user.id,
        logical_call_id=f"model:0:call:{uuid.uuid4().hex[:12]}",
        capability_id="python_execute",
        capability_version="1.0.0",
        manifest_digest=CAPABILITY.manifest_digest,
        arguments_json="{}",
        arguments_digest=arguments_digest,
        request_digest="e" * 64,
        input_artifact_refs="[]",
        configuration_revision="1",
        execution_bundle_digest="f" * 64,
        status=status,
        result_json='{"ok": true}' if status == "succeeded" else None,
        result_digest=result_digest,
        output_artifact_ids="[]",
    )
    db.add(operation)
    db.flush()
    return operation


def _artifact(
    db,
    run: Run,
    operation: ToolOperation,
    *,
    status: str = "ready",
    sha256: str = "1" * 64,
) -> Artifact:
    artifact = Artifact(
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        originating_run_id=run.id,
        originating_operation_id=operation.id,
        kind="analysis_output",
        media_type="image/png",
        display_name="chart.png",
        size_bytes=12,
        sha256=sha256,
        relative_storage_key=f"task-state/{uuid.uuid4().hex}",
        status=status,
        source="tool",
    )
    db.add(artifact)
    db.flush()
    operation.output_artifact_ids = json.dumps([str(artifact.id)])
    db.flush()
    return artifact


# ---------------------------------------------------------------------------
# State behavior
# ---------------------------------------------------------------------------


def test_plan_revision_is_immutable_and_fenced():
    db = TestSessionLocal()
    try:
        _, _, _, run, claim = _identity(db)

        save_plan(db, claim, _plan(revision=1), expected_revision=0)
        db.commit()
        previous = read_progress(db, run.id)
        assert previous.current_revision == 1
        assert previous.status == "planned"
        assert tuple(step.id for step in previous.steps) == ("inspect",)

        revised = save_plan(
            db,
            claim,
            _plan(revision=2, intent="Produce the revised chart."),
            expected_revision=previous.current_revision,
        )
        db.commit()
        updated = read_progress(db, run.id)
        assert updated.current_revision == previous.current_revision + 1
        assert revised.current_revision == updated.current_revision

        # The same revision cannot be replaced with different content.
        with pytest.raises(TaskStateError) as conflict:
            save_plan(
                db,
                claim,
                _plan(revision=2, intent="Replace the reviewed plan."),
                expected_revision=1,
            )
        assert conflict.value.code in {"plan_conflict", "stale_revision"}
        db.rollback()
        assert read_progress(db, run.id).current_revision == 2

        # A stale expected revision is rejected rather than rebased.
        with pytest.raises(TaskStateError) as stale:
            save_plan(db, claim, _plan(revision=3), expected_revision=1)
        assert stale.value.code == "stale_revision"
        db.rollback()

        # A stale fence cannot write either.
        stale_claim = Claim(
            run_id=claim.run_id,
            fence=0,
            lease_until=claim.lease_until,
            worker_id=claim.worker_id,
            user_id=claim.user_id,
        )
        with pytest.raises(TaskStateError) as fenced:
            save_plan(db, stale_claim, _plan(revision=3), expected_revision=2)
        assert fenced.value.code == "stale_claim"
        db.rollback()
    finally:
        db.close()


def test_committed_operation_is_only_source_of_attempt_outcome():
    db = TestSessionLocal()
    try:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()

        operation = _operation(db, run, user)
        artifact = _artifact(db, run, operation)
        db.commit()

        progress = record_attempt_result(
            db,
            claim,
            step_id="inspect",
            attempt=1,
            operation_id=operation.id,
        )
        db.commit()

        assert len(progress.steps[0].attempts) == 1
        attempt = progress.steps[0].attempts[0]
        assert attempt.operation_id == operation.id
        assert attempt.status == "succeeded"
        assert attempt.input_digest == operation.arguments_digest
        assert attempt.result_digest == operation.result_digest
        assert attempt.evidence_refs
        assert any(
            artifact.sha256 in reference for reference in attempt.evidence_refs
        )
        assert progress.status == "running"
        assert progress.verified_outputs == ()
        assert progress.counters.tool_calls == 1

        stored = read_progress(db, run.id)
        assert stored.steps[0].attempts[0].operation_id == operation.id
    finally:
        db.close()


def test_duplicate_result_does_not_advance_twice():
    db = TestSessionLocal()
    try:
        user, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        operation = _operation(db, run, user)
        _artifact(db, run, operation)
        db.commit()

        record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()
        first_events = (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "task.attempt_recorded")
            .count()
        )
        assert first_events == 1

        # The same committed operation published again is idempotent.
        replay = record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()
        assert len(replay.steps[0].attempts) == 1
        assert len(read_progress(db, run.id).steps[0].attempts) == 1
        replayed_events = (
            db.query(Event)
            .filter(Event.run_id == run.id, Event.type == "task.attempt_recorded")
            .count()
        )
        assert replayed_events == 1

        # The same operation cannot be re-labelled as a second attempt.
        with pytest.raises(TaskStateError) as conflict:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=2, operation_id=operation.id
            )
        assert conflict.value.code == "attempt_conflict"
        db.rollback()

        # A different operation cannot overwrite the recorded ordinal.
        other = _operation(db, run, user, result_digest="9" * 64)
        db.commit()
        with pytest.raises(TaskStateError) as conflicting_ordinal:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=1, operation_id=other.id
            )
        assert conflicting_ordinal.value.code == "attempt_conflict"
        db.rollback()
        assert len(read_progress(db, run.id).steps[0].attempts) == 1
    finally:
        db.close()


def test_operation_scope_outcome_and_artifact_ownership_are_validated():
    db = TestSessionLocal()
    try:
        user, _, _, run, claim = _identity(db)
        _, _, _, other_run, _ = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()

        uncommitted = _operation(db, run, user, status="running", result_digest=None)
        db.commit()
        with pytest.raises(TaskStateError) as pending:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=1, operation_id=uncommitted.id
            )
        assert pending.value.code == "operation_not_committed"
        db.rollback()

        foreign = _operation(db, other_run, user)
        db.commit()
        with pytest.raises(TaskStateError) as scope:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=1, operation_id=foreign.id
            )
        assert scope.value.code == "operation_scope"
        db.rollback()

        wrong_principal = _operation(db, run, user, principal_id=uuid.uuid4())
        db.commit()
        with pytest.raises(TaskStateError) as principal:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=1, operation_id=wrong_principal.id
            )
        assert principal.value.code == "operation_scope"
        db.rollback()

        no_result = _operation(db, run, user, result_digest=None)
        db.commit()
        with pytest.raises(TaskStateError) as missing:
            record_attempt_result(
                db, claim, step_id="inspect", attempt=1, operation_id=no_result.id
            )
        assert missing.value.code == "operation_incomplete"
        db.rollback()

        foreign_artifact = _operation(db, run, user)
        artifact = _artifact(db, run, foreign_artifact)
        artifact.conversation_id = other_run.conversation_id
        db.commit()
        with pytest.raises(TaskStateError) as ownership:
            record_attempt_result(
                db,
                claim,
                step_id="inspect",
                attempt=1,
                operation_id=foreign_artifact.id,
            )
        assert ownership.value.code == "artifact_scope"
        db.rollback()

        unknown_step = _operation(db, run, user)
        db.commit()
        with pytest.raises(TaskStateError) as step:
            record_attempt_result(
                db, claim, step_id="render", attempt=1, operation_id=unknown_step.id
            )
        assert step.value.code == "step_unknown"
        db.rollback()
    finally:
        db.close()


def test_plan_state_rolls_back_with_its_public_event():
    db = TestSessionLocal()
    try:
        _, _, _, run, claim = _identity(db)
        run_id = run.id
        save_plan(db, claim, _plan(), expected_revision=0)
        db.flush()
        assert db.query(Event).filter(Event.run_id == run_id).count() == 1
    finally:
        db.rollback()
        db.close()

    fresh = TestSessionLocal()
    try:
        assert fresh.query(TaskRecord).filter(TaskRecord.run_id == run_id).count() == 0
        assert fresh.query(Event).filter(Event.run_id == run_id).count() == 0
    finally:
        fresh.close()


def test_open_decision_and_decision_command_uniqueness():
    db = TestSessionLocal()
    try:
        _, _, _, run, claim = _identity(db)
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()
        task = (
            db.query(TaskRecord)
            .filter(TaskRecord.run_id == run.id)
            .one()
        )
        expires_at = datetime.now(UTC) + timedelta(hours=24)
        decision = TaskDecisionRecord(
            task_id=task.id,
            run_id=run.id,
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

        db.add(
            TaskDecisionRecord(
                task_id=task.id,
                run_id=run.id,
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

        db.add(
            TaskDecisionCommandRecord(
                task_id=task.id,
                run_id=run.id,
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

        db.add(
            TaskDecisionCommandRecord(
                task_id=task.id,
                run_id=run.id,
                decision_id=decision.id,
                client_request_id=uuid.uuid4(),
                request_id=decision.request_id,
                action="confirm",
                plan_revision=1,
                expected_digest="a" * 64,
                result_json="{}",
            )
        )
        # A different client_request_id is a different command.
        db.commit()
        db.query(TaskDecisionCommandRecord).filter(
            TaskDecisionCommandRecord.run_id == run.id
        ).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Migration behavior
# ---------------------------------------------------------------------------


def _alembic_ini(tmp_path: Path, db_path: Path) -> Path:
    api_root = Path(__file__).parents[1]
    path = tmp_path / "alembic.ini"
    path.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = sqlite:///{db_path}

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
    return path


def _run_alembic(ini: Path, *args: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[1]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _seed_legacy_run(engine) -> str:
    with engine.begin() as connection:
        user_id = "10000000-0000-0000-0000-000000000001"
        project_id = "20000000-0000-0000-0000-000000000001"
        conversation_id = "30000000-0000-0000-0000-000000000001"
        run_id = "40000000-0000-0000-0000-000000000001"
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'phase7a-user', 'phase7a@example.test', 'hash')"
            ),
            {"id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) "
                "VALUES (:id, :owner, 'phase7a')"
            ),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :user, :project, 'legacy')"
            ),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO runs (id, conversation_id, project_id, status) "
                "VALUES (:id, :conversation, :project, 'completed')"
            ),
            {"id": run_id, "conversation": conversation_id, "project": project_id},
        )
    return run_id


def test_upgrade_keeps_old_history_without_fabricating_tasks(tmp_path):
    db_path = tmp_path / "phase7a-history.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run_alembic(ini, "upgrade", "phase6b030").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    run_id = _seed_legacy_run(engine)

    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with engine.begin() as connection:
        legacy = connection.execute(
            text("SELECT status, graph_version FROM runs WHERE id=:id"),
            {"id": run_id},
        ).one()
        assert legacy.status == "completed"
        assert legacy.graph_version == 1
        task_count = connection.execute(
            text("SELECT COUNT(*) FROM task_records")
        ).scalar_one()
        assert task_count == 0
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "phase7a031"

        # The nonterminal waiting statuses fit the existing 20-character column.
        for status in ("waiting_plan", "waiting_input", "waiting_config"):
            connection.execute(
                text("UPDATE runs SET status=:status WHERE id=:id"),
                {"status": status, "id": run_id},
            )
    tables = set(inspect(engine).get_table_names())
    assert {
        "task_records",
        "task_plans",
        "task_step_attempts",
        "task_decisions",
        "task_decision_commands",
    } <= tables
    engine.dispose()


def test_downgrade_refuses_v5_rows(tmp_path):
    db_path = tmp_path / "phase7a-downgrade.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run_alembic(ini, "upgrade", "head").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        user_id = "10000000-0000-0000-0000-000000000002"
        project_id = "20000000-0000-0000-0000-000000000002"
        conversation_id = "30000000-0000-0000-0000-000000000002"
        run_id = "40000000-0000-0000-0000-000000000002"
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'phase7a-v5', 'phase7a-v5@example.test', 'hash')"
            ),
            {"id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) "
                "VALUES (:id, :owner, 'phase7a v5')"
            ),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :user, :project, 'v5')"
            ),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO runs (id, conversation_id, project_id, status, kind, graph_version) "
                "VALUES (:id, :conversation, :project, 'running', 'chat', 5)"
            ),
            {"id": run_id, "conversation": conversation_id, "project": project_id},
        )

    downgraded = _run_alembic(ini, "downgrade", "phase6b030")
    assert downgraded.returncode != 0
    assert "refusing to downgrade" in downgraded.stderr.lower()
    engine.dispose()


def test_downgrade_cleans_up_when_no_v5_rows_exist(tmp_path):
    db_path = tmp_path / "phase7a-empty-downgrade.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run_alembic(ini, "upgrade", "head").returncode == 0
    downgraded = _run_alembic(ini, "downgrade", "phase6b030")
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{db_path}")
    assert "task_records" not in set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "phase6b030"
    engine.dispose()
