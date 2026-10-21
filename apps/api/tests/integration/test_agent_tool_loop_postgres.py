"""PostgreSQL proves concurrent model attempts share one durable reservation."""

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
)
from src.openship.chat.models import Conversation
from src.openship.providers.contracts import RunLimits, pin_profile
from src.openship.providers.errors import ProviderError
from src.openship.runs.budget import RunBudget
from src.openship.runs.models import Event, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url
from tests.providers.conftest import qualified_profile

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; budget concurrency requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database = "phase4_agent_budget_lane"
    lane_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-agent-budget") / "alembic.ini"
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
    engine = create_engine(lane_url, pool_size=6, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        finally:
            admin.dispose()


def test_two_workers_cannot_overbook_last_generation_attempt(pg_sessions):
    suffix = uuid.uuid4().hex
    with pg_sessions() as db:
        user = User(
            username=f"budget_{suffix[:16]}",
            email=f"budget_{suffix}@example.test",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Budget concurrency")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Budget")
        db.add(conversation)
        db.flush()
        manifests = list_capability_manifests()
        refs = tuple(
            CapabilityRef(
                id=item.id,
                version=item.version,
                manifest_digest=item.manifest_digest,
            )
            for item in manifests
        )
        execution = ExecutionConfig(
            graph_kind="chat",
            graph_version=2,
            allowed_capabilities=refs,
            loop_limits=LoopLimits(
                max_model_turns=1,
                max_tool_calls=0,
                max_generation_attempts=1,
                max_output_tokens_per_request=4000,
                max_total_output_tokens=4000,
            ),
        )
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            client_request_id=str(uuid.uuid4()),
            status="running",
            kind="chat",
            graph_version=2,
            fence=1,
            execution_configuration=execution.model_dump_json(),
        )
        db.add(run)
        db.flush()
        lease_until = datetime.now(UTC) + timedelta(minutes=2)
        db.add(RunJob(run_id=run.id, fence=1, owner_id="budget-worker", lease_until=lease_until))
        db.commit()
        claim = Claim(run.id, 1, lease_until, "budget-worker", user.id)
        run_id = run.id

    capabilities = {
        "streaming": True,
        "usage_reporting": True,
        "context_tokens": 32768,
        "max_output_tokens": 4096,
        "token_counting": False,
        "tool_calls": True,
        "structured_output": False,
        "attachment_types": [],
        "parallel_tool_calls": False,
    }
    profile = pin_profile(qualified_profile(capabilities=capabilities))
    budget = RunBudget(pg_sessions, claim, RunLimits(max_attempts=3), execution)
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=10)
        try:
            budget.reserve_generation(
                profile,
                reason="initial",
                model_turn_ordinal=0,
                requested_output_tokens=4000,
                reserved_tokens=None,
                reserved_cost=None,
            )
            return "admitted"
        except ProviderError as refused:
            return refused.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: reserve(), range(2)))

    assert outcomes.count("admitted") == 1
    assert outcomes.count("budget_exceeded") == 1
    with pg_sessions() as db:
        assert db.query(ProviderAttempt).filter_by(run_id=run_id).count() == 1
        assert db.query(Event).filter_by(run_id=run_id, type="model.attempt.started").count() == 1
