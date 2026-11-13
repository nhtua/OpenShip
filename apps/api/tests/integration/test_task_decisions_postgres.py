"""Real PostgreSQL migration coverage for task recovery decisions."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.task_recovery_migration_support import check_guards, seed_032

pytestmark = pytest.mark.postgres


@pytest.fixture
def recovery_migration_database(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail(
            "TEST_POSTGRES_URL is required for the selected PostgreSQL migration lane",
            pytrace=False,
        )

    bounded = bounded_postgres_url(service_url)
    database = f"phase7a_recovery_{uuid.uuid4().hex}"
    lane_url = (
        make_url(bounded).set(database=database).render_as_string(hide_password=False)
    )
    admin = create_engine(bounded).execution_options(isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        engine = create_engine(lane_url)
        yield engine, _alembic_ini(tmp_path, lane_url)
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()


def test_recovery_migration_upgrade_and_lossy_downgrade_guard(
    recovery_migration_database,
):
    engine, ini = recovery_migration_database
    check_guards(engine, ini, _run_alembic)


def test_recovery_downgrade_waits_for_postgres_writer_before_checking(
    recovery_migration_database,
):
    engine, ini = recovery_migration_database
    assert _run_alembic(ini, "upgrade", "phase7a032").returncode == 0
    seed_032(engine)
    assert _run_alembic(ini, "upgrade", "phase7a034").returncode == 0
    api_root = Path(__file__).parents[2]
    with engine.connect() as writer, engine.connect() as observer:
        writer_pid = writer.execute(text("SELECT pg_backend_pid() ")).scalar_one()
        writer.execute(
            text("UPDATE task_records SET skipped_steps_json = :value"),
            {"value": '["inspect"]'},
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(ini),
                "downgrade",
                "phase7a033",
            ],
            cwd=api_root,
            env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                blocked = observer.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :writer = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"writer": writer_pid},
                ).scalar_one()
                if blocked:
                    break
                assert process.poll() is None, (
                    "downgrade exited before blocking on writer"
                )
                time.sleep(0.05)
            else:
                pytest.fail("downgrade did not block on the concurrent writer")
            writer.commit()
            _, stderr = process.communicate(timeout=30)
            assert process.returncode != 0
            assert "cannot downgrade: recovery data would be lost" in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT skipped_steps_json FROM task_records")
            ).scalar_one()
            == '["inspect"]'
        )
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "phase7a034"
        )


def test_two_decisions_cannot_enqueue_twice(monkeypatch):
    """Two concurrent choice submissions serialize on the owned scope."""
    from alembic.config import Config

    from alembic import command as alembic_command
    from src.openship.config import settings
    from src.openship.messaging.models import MessageTransportState
    from src.openship.runs.models import Outbox, RunJob
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.runs.tasks.models import TaskDecisionCommandRecord
    from src.openship.runs.tasks.state import save_plan
    from src.openship.sandboxes.profiles import reset_runtime_catalog
    from tests.integration.test_phase7a_task_state_postgres import _identity, _plan
    from tests.test_task_decisions import _pinned_execution

    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail(
            "TEST_POSTGRES_URL is required for PostgreSQL decision concurrency",
            pytrace=False,
        )
    bounded = bounded_postgres_url(service_url)
    database = f"phase7a_decision_{uuid.uuid4().hex}"
    lane_url = (
        make_url(bounded).set(database=database).render_as_string(hide_password=False)
    )
    admin = create_engine(bounded).execution_options(isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parents[2] / "alembic")
        )
        config.set_main_option("sqlalchemy.url", lane_url.replace("%", "%%"))
        alembic_command.upgrade(config, "head")
        engine = create_engine(lane_url, pool_size=4)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
        monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
        with sessions() as db:
            if db.get(MessageTransportState, 1) is None:
                db.add(
                    MessageTransportState(
                        id=1, installation_id=uuid.uuid4(), transport_epoch=uuid.uuid4()
                    )
                )
                db.commit()
            user, run, claim = _identity(db)
            from src.openship.chat.models import Conversation
            from src.openship.workspace.models import Project

            project = db.get(Project, run.project_id)
            conversation = db.get(Conversation, run.conversation_id)
            _pinned_execution(run, user, project, conversation)
            db.flush()
            save_plan(db, claim, _plan(), expected_revision=0)
            now = datetime.now(UTC)
            request = park_task_wait(
                db, claim, kind="plan_confirmation", prompt="Approve?", now=now
            ).pending_decision
            db.commit()
            run_id, user_id = run.id, user.id
            generation = (
                db.query(RunJob).filter_by(run_id=run_id).one().dispatch_generation
            )
        choice = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="confirm",
        )
        first_locked, second_started, release = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        pids = {}

        def first():
            with sessions() as db:
                pids["first"] = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
                result = apply_decision(
                    db, principal_id=user_id, run_id=run_id, command=choice, now=now
                )
                first_locked.set()
                assert release.wait(20)
                db.commit()
                return result

        def second():
            assert first_locked.wait(20)
            with sessions() as db:
                pids["second"] = db.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                second_started.set()
                result = apply_decision(
                    db,
                    principal_id=user_id,
                    run_id=run_id,
                    command=choice.model_copy(
                        update={"client_request_id": uuid.uuid4()}
                    ),
                    now=now,
                )
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(first), pool.submit(second)
            try:
                assert first_locked.wait(20) and second_started.wait(20)
                with engine.connect() as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        blocked = observer.execute(
                            text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                            {"first": pids["first"], "second": pids["second"]},
                        ).scalar_one()
                        if blocked:
                            break
                        time.sleep(0.05)
                    else:
                        pytest.fail(
                            "second decision did not wait on the first transaction"
                        )
            finally:
                release.set()
            assert a.result(timeout=30) == b.result(timeout=30)
        with sessions() as db:
            assert (
                db.query(Outbox).filter_by(run_id=run_id, destination="command").count()
                == 1
            )
            assert (
                db.query(TaskDecisionCommandRecord).filter_by(run_id=run_id).count()
                == 2
            )
            assert (
                db.query(RunJob).filter_by(run_id=run_id).one().dispatch_generation
                == generation + 1
            )
    finally:
        reset_runtime_catalog()
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()


def test_cancel_serializes_before_concurrent_confirmation(
    recovery_migration_database, monkeypatch
):
    """A confirmation blocked behind cancellation cannot consume or enqueue."""
    from fastapi import HTTPException

    from src.openship.chat.models import Conversation
    from src.openship.config import settings
    from src.openship.runs.models import Outbox, Run
    from src.openship.runs.service import request_cancel
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
    from src.openship.runs.tasks.models import TaskDecisionRecord
    from src.openship.runs.tasks.state import save_plan
    from src.openship.sandboxes.profiles import reset_runtime_catalog
    from src.openship.workspace.models import Project
    from tests.integration.test_phase7a_task_state_postgres import _identity, _plan
    from tests.test_task_decisions import _pinned_execution

    engine, ini = recovery_migration_database
    assert _run_alembic(ini, "upgrade", "head").returncode == 0
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    try:
        with sessions() as db:
            user, run, claim = _identity(db)
            _pinned_execution(
                run,
                user,
                db.get(Project, run.project_id),
                db.get(Conversation, run.conversation_id),
            )
            db.flush()
            save_plan(db, claim, _plan(), expected_revision=0)
            now = datetime.now(UTC)
            decision = park_task_wait(
                db, claim, kind="plan_confirmation", prompt="Approve?", now=now
            ).pending_decision
            db.commit()
            user_id, run_id, conversation_id = user.id, run.id, run.conversation_id
        choice = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=decision.request_id,
            plan_revision=decision.plan_revision,
            expected_digest=decision.expected_digest,
            action="confirm",
        )
        locked, started, release = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        pids = {}

        def cancel():
            with sessions() as db:
                pids["cancel"] = db.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                db.query(Conversation).filter_by(
                    id=conversation_id
                ).with_for_update().one()
                locked.set()
                assert release.wait(20)
                request_cancel(db, user_id, str(run_id))

        def confirm():
            assert locked.wait(20)
            with sessions() as db:
                pids["confirm"] = db.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                started.set()
                with pytest.raises(HTTPException) as error:
                    apply_decision(
                        db, principal_id=user_id, run_id=run_id, command=choice, now=now
                    )
                db.rollback()
                return error.value.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(cancel), pool.submit(confirm)
            try:
                assert locked.wait(20) and started.wait(20)
                with engine.connect() as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if observer.execute(
                            text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                            {"first": pids["cancel"], "second": pids["confirm"]},
                        ).scalar_one():
                            break
                        time.sleep(0.05)
                    else:
                        pytest.fail("confirmation did not block behind cancellation")
            finally:
                release.set()
            a.result(timeout=30)
            assert b.result(timeout=30) == 409
        with sessions() as db:
            assert db.get(Run, run_id).status == "cancelled"
            assert (
                db.query(TaskDecisionRecord).filter_by(run_id=run_id).one().state
                == "cancelled"
            )
            assert (
                db.query(Outbox).filter_by(run_id=run_id, destination="command").count()
                == 0
            )
    finally:
        reset_runtime_catalog()
