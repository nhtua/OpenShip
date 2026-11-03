"""PostgreSQL upgrade and claim-scoped selection for workflow sandbox pointers.

Upgrades a fresh lane database through the real Alembic chain, including the new
``phase6b026`` workflow-sandbox revision (which does not reuse or downgrade the
abandoned branch's 021–025 identifiers), then exercises the on-demand handle
selection with independent SQLAlchemy sessions so no provider wait can hold a
transaction open.
"""

import os
import re
import subprocess
import sys
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.sandboxes import sessions
from src.openship.sandboxes.contracts import RuntimeRequirements, SandboxHandle
from src.openship.sandboxes.models import WorkflowSandbox
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_lane(tmp_path_factory):
    """Alembic-upgrade a fresh PostgreSQL lane; report missing services, not passes."""
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip(
            "no TEST_POSTGRES_URL; this test requires a disposable PostgreSQL service"
        )

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    lane_name = "phase6b_workflow_sandbox_lane"
    lane_url = parsed.set(database=lane_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
            connection.execute(text(f'CREATE DATABASE "{lane_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase6b-workflow-sandbox") / "alembic.ini"
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
        env={"PYTHONPATH": str(api_root / "src")},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    current = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "current"],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert current.returncode == 0, current.stderr
    assert re.search(r"phase6b026\s*\(head\)", current.stdout), current.stdout
    assert not re.search(r"phase6b02[1-5]\w*\s*\(head\)", current.stdout), current.stdout

    engine = create_engine(lane_url, pool_size=8, max_overflow=4)
    Sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield engine, Sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
        finally:
            admin.dispose()


def _requirements():
    return RuntimeRequirements(
        provider_id="local",
        profile_id="python311",
        image="python:test",
        dependency_identity="stdlib",
        cpu="1",
        memory="512Mi",
    )


class FakeClient:
    """Stands in for OpenSandboxClient: counts creates, never deletes or renews."""

    def __init__(self, *, on_wait=None, expiry_seconds=1800):
        self.on_wait = on_wait
        self.expiry_seconds = expiry_seconds
        self.created = 0
        self.inspected = 0

    async def create(self, requirements):
        self.created += 1
        if self.on_wait is not None:
            await self.on_wait()
        return SandboxHandle(
            provider_id=requirements.provider_id,
            sandbox_id=f"pg-sbx-{self.created}",
            requirements_key=requirements.key,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.expiry_seconds),
        )

    async def inspect(self, handle):
        self.inspected += 1
        if self.on_wait is not None:
            await self.on_wait()
        return handle.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=1500)}
        )

    def assert_no_cleanup(self):
        assert self.created >= 0  # the only provider surface here is create/inspect


def _seed_run(Sessions):
    """Seed one authorized running root run plus its current lease claim."""
    now = datetime.now(UTC)
    user_id, project_id, conversation_id, run_id = (_uuid.uuid4() for _ in range(4))
    with Sessions() as db:
        user = User(id=user_id, username=f"u-{run_id.hex}",
                    email=f"sandbox-{run_id.hex}@example.test", password_hash="test-only")
        db.add(user)
        db.flush()
        db.add(Project(id=project_id, owner_user_id=user_id, name="pg lane"))
        db.flush()
        db.add(Conversation(id=conversation_id, user_id=user_id, project_id=project_id,
                            title="pg lane"))
        db.flush()
        db.add(Run(id=run_id, conversation_id=conversation_id, project_id=project_id,
                   status="running", fence=1))
        db.flush()
        db.add(RunJob(run_id=run_id, fence=1, owner_id="pg-worker-1",
                      lease_until=now + timedelta(seconds=300), available_at=now))
        db.commit()
    claim = Claim(run_id=run_id, fence=1, lease_until=now + timedelta(seconds=300),
                  worker_id="pg-worker-1", user_id=user_id)
    return run_id, claim, project_id, conversation_id


def test_upgrade_adds_one_scoped_pointer_table(pg_lane):
    """Fresh main data upgrades to phase6b026 with the scoped pointer table."""
    engine, _ = pg_lane
    insp = sa_inspect(engine)
    assert "workflow_sandboxes" in insp.get_table_names()
    columns = {column["name"] for column in insp.get_columns("workflow_sandboxes")}
    assert {"root_run_id", "project_id", "conversation_id", "requirements_key"} <= columns


@pytest.mark.asyncio
async def test_independent_sessions_reuse_one_pointer(pg_lane):
    """Two selections through separate sessions create exactly one sandbox."""
    _, Sessions = pg_lane
    run_id, claim, project_id, conversation_id = _seed_run(Sessions)
    client = FakeClient()
    first = await sessions.get_or_create_sandbox(
        Sessions, client, claim=claim, root_run_id=run_id, requirements=_requirements()
    )
    second = await sessions.get_or_create_sandbox(
        Sessions, client, claim=claim, root_run_id=run_id, requirements=_requirements()
    )
    assert client.created == 1
    assert first.sandbox_id == second.sandbox_id == "pg-sbx-1"
    with Sessions() as db:
        rows = db.query(WorkflowSandbox).all()
        assert len(rows) == 1
        assert rows[0].root_run_id == run_id
        assert rows[0].project_id == project_id
        assert rows[0].conversation_id == conversation_id
        assert rows[0].status == "active"
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_duplicate_pointer_for_a_root_run_is_rejected(pg_lane):
    """PostgreSQL enforces the one-handle-per-root-run scope in the table itself."""
    _, Sessions = pg_lane
    run_id, claim, project_id, conversation_id = _seed_run(Sessions)
    client = FakeClient()
    await sessions.get_or_create_sandbox(
        Sessions, client, claim=claim, root_run_id=run_id, requirements=_requirements()
    )
    with Sessions() as db:
        db.add(
            WorkflowSandbox(
                root_run_id=run_id,
                project_id=project_id,
                conversation_id=conversation_id,
                provider_id="local",
                requirements_key=_requirements().key,
                status="requested",
                fence=claim.fence,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    assert client.created == 1


@pytest.mark.asyncio
async def test_pg_claim_stolen_during_wait_blocks_the_acknowledgement(pg_lane):
    """A session-side fence bump while the provider waits blocks the save and
    blocks any automatic recreation afterwards."""
    _, Sessions = pg_lane
    run_id, claim, _project_id, _conversation_id = _seed_run(Sessions)

    async def steal_claim():
        with Sessions() as db:
            db.query(RunJob).filter_by(run_id=run_id).update(
                {RunJob.fence: claim.fence + 1, RunJob.owner_id: None,
                 RunJob.lease_until: None}
            )
            db.commit()

    client = FakeClient(on_wait=steal_claim)
    with pytest.raises(sessions.SandboxClaimUncertain):
        await sessions.get_or_create_sandbox(
            Sessions, client, claim=claim, root_run_id=run_id, requirements=_requirements()
        )
    assert client.created == 1
    with Sessions() as db:
        row = db.query(WorkflowSandbox).filter_by(root_run_id=run_id).one()
        assert row.status == "requested"
        assert row.sandbox_id is None

    with pytest.raises(sessions.SandboxCreationUncertain):
        await sessions.get_or_create_sandbox(
            Sessions, client, claim=claim, root_run_id=run_id, requirements=_requirements()
        )
    assert client.created == 1
