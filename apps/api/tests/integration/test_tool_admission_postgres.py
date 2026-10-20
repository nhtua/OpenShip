"""PostgreSQL barriers for conversation admission and shared tool slots."""

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ToolCommandRequest,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.execution.admission import (
    acquire_execution_slot,
    admit_operation,
    release_execution_slot,
)
from src.openship.execution.models import ExecutionSlot, ToolOperation
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.service import submit_tool_run
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; this test requires a disposable PostgreSQL service")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    lane_name = "phase4_tool_admission_lane"
    lane_url = parsed.set(database=lane_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
            connection.execute(text(f'CREATE DATABASE "{lane_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-tool-admission") / "alembic.ini"
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
    Sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield Sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
        finally:
            admin.dispose()


def _new_owned_conversation(db, label):
    suffix = uuid.uuid4().hex
    user = User(username=f"phase4_{suffix}", email=f"phase4_{suffix}@example.test", password_hash="hash")
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name=label)
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title=label)
    db.add(conversation)
    db.flush()
    return user, project, conversation


def _command():
    manifest = list_capability_manifests()[0]
    return ToolCommandRequest(
        client_request_id=uuid.uuid4(),
        capability=CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        ),
        arguments={"query": "postgresql row locking", "max_results": 2},
    )


def test_concurrent_commands_serialize_one_active_run_and_one_event_set(pg_sessions, monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    setup = pg_sessions()
    user, _, conversation = _new_owned_conversation(setup, "Concurrent admission")
    conversation_id = conversation.id
    user_id = user.id
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)

    def submit():
        db = pg_sessions()
        try:
            barrier.wait(timeout=10)
            run = submit_tool_run(db, user_id, conversation_id, _command())
            db.commit()
            return ("accepted", run.id)
        except HTTPException as error:
            db.rollback()
            return ("refused", error.status_code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: submit(), range(2)))
    assert sum(result[0] == "accepted" for result in outcomes) == 1
    assert sum(result == ("refused", 409) for result in outcomes) == 1

    db = pg_sessions()
    try:
        run_ids = [row[0] for row in outcomes if row[0] == "accepted"]
        assert db.query(Run).filter(Run.conversation_id == conversation_id, Run.kind == "tool").count() == 1
        assert db.query(RunJob).filter(RunJob.run_id.in_(run_ids)).count() == 1
        assert db.query(Event).filter(Event.conversation_id == conversation_id, Event.type == "tool.submitted").count() == 1
        assert db.query(Outbox).filter(Outbox.conversation_id == conversation_id, Outbox.type == "tool.submitted").count() == 1
    finally:
        db.close()


def test_competing_workers_share_one_slot_and_expired_fence_cannot_release_it(pg_sessions, monkeypatch):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "tool_concurrency", 1)
    db = pg_sessions()
    manifest = list_capability_manifests()[0]
    ref = CapabilityRef(id=manifest.id, version=manifest.version, manifest_digest=manifest.manifest_digest)
    runs = []
    claims = []
    operations = []
    for index in range(2):
        user, project, conversation = _new_owned_conversation(db, f"Slot {index}")
        run = submit_tool_run(db, user.id, conversation.id, _command())
        db.flush()
        worker_id = f"worker-{index}"
        lease_until = datetime.now(UTC) + timedelta(minutes=1)
        run.status = "running"
        run.fence = 1
        job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
        job.fence = 1
        job.owner_id = worker_id
        job.lease_until = lease_until
        claim = Claim(run.id, 1, lease_until, worker_id, user.id)
        context = AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        )
        operation = admit_operation(
            db, claim, context, "manual:0", ref,
            {"query": "postgresql row locking", "max_results": 2}, (),
        )
        runs.append((run, job))
        claims.append(claim)
        operations.append(operation)
    db.commit()
    db.close()

    barrier = threading.Barrier(2)

    def acquire(index):
        session = pg_sessions()
        try:
            barrier.wait(timeout=10)
            slot = acquire_execution_slot(session, claims[index], operations[index].id)
            session.commit()
            return slot.id if slot else None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        slot_ids = list(pool.map(acquire, range(2)))
    assert sum(slot_id is not None for slot_id in slot_ids) == 1

    winner = next(index for index, slot_id in enumerate(slot_ids) if slot_id is not None)
    slot_id = slot_ids[winner]
    db = pg_sessions()
    try:
        assert db.query(ExecutionSlot).filter(ExecutionSlot.operation_id.isnot(None)).count() == 1
        assert db.query(ToolOperation).count() == 2
        run, job = runs[winner]
        job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
        run = db.query(Run).filter(Run.id == run.id).one()
        slot = db.query(ExecutionSlot).filter(ExecutionSlot.id == slot_id).one()
        old_claim = claims[winner]
        slot.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        run.fence = 2
        job.fence = 2
        job.owner_id = "replacement-worker"
        job.lease_until = datetime.now(UTC) + timedelta(minutes=1)
        replacement = Claim(run.id, 2, job.lease_until, job.owner_id, old_claim.user_id)
        db.commit()

        reclaimed = acquire_execution_slot(db, replacement, operations[winner].id)
        assert reclaimed is not None
        assert reclaimed.id == slot_id
        assert reclaimed.fence == 2
        assert not release_execution_slot(db, old_claim, slot_id)
        assert release_execution_slot(db, replacement, slot_id)
        db.commit()
    finally:
        db.close()
