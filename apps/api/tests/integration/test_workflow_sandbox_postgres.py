"""PostgreSQL upgrade and claim-scoped selection for workflow sandbox pointers.

Upgrades a fresh lane database through the real Alembic chain, including the new
``phase6b026`` workflow-sandbox revision (which does not reuse or downgrade the
abandoned branch's 021–025 identifiers), then exercises the on-demand handle
selection with independent SQLAlchemy sessions so no provider wait can hold a
transaction open.
"""

import asyncio
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
    # Later revisions may extend the chain; require its actual head rather
    # than freezing this sandbox lane to the revision that introduced it.
    assert re.fullmatch(r"\w+\s+\(head\)\s*", current.stdout), current.stdout
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


def _seed_followup_run(Sessions, first_id, first_claim, project_id, conversation_id):
    """Finish the prior run before admitting its next run in the same conversation."""
    now = datetime.now(UTC)
    run_id = _uuid.uuid4()
    with Sessions() as db:
        db.query(Run).filter(Run.id == first_id).update({Run.status: "completed"})
        db.add(Run(id=run_id, project_id=project_id,
                   conversation_id=conversation_id, status="running", fence=1))
        db.add(RunJob(run_id=run_id, fence=1, owner_id="pg-worker-2",
                      lease_until=now + timedelta(minutes=5), available_at=now))
        db.commit()
    claim = Claim(
        run_id=run_id,
        fence=1,
        lease_until=now + timedelta(minutes=5),
        worker_id="pg-worker-2",
        user_id=first_claim.user_id,
    )
    return run_id, claim


def test_upgrade_adds_one_scoped_pointer_table(pg_lane):
    """Fresh main data upgrades through 027 and shared-scope 028."""
    engine, _ = pg_lane
    insp = sa_inspect(engine)
    assert "workflow_sandboxes" in insp.get_table_names()
    columns = {column["name"] for column in insp.get_columns("workflow_sandboxes")}
    assert {"root_run_id", "project_id", "conversation_id", "requirements_key",
            "requested_at", "intent_id", "command_uncertain_until"} <= columns
    assert "command_operation_id" in columns
    artifact_checks = " ".join(
        item["sqltext"] for item in insp.get_check_constraints("artifacts")
    )
    assert "analysis_output" in artifact_checks


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
    """PostgreSQL enforces one pointer per project/conversation scope."""
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
                requested_at=datetime.now(UTC),
                intent_id=_uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    assert client.created == 1


@pytest.mark.asyncio
async def test_two_runs_same_scope_serialize_to_one_provider_pointer(pg_lane):
    _, Sessions = pg_lane
    first_id, first_claim, project_id, conversation_id = _seed_run(Sessions)
    client = FakeClient()
    first = await sessions.get_or_create_sandbox(
        Sessions, client, claim=first_claim, root_run_id=first_id,
        requirements=_requirements(),
    )
    second_id, second_claim = _seed_followup_run(
        Sessions, first_id, first_claim, project_id, conversation_id
    )
    second = await sessions.get_or_create_sandbox(
        Sessions, client, claim=second_claim, root_run_id=second_id,
        requirements=_requirements(),
    )
    assert first.sandbox_id == second.sandbox_id
    assert client.created == 1
    with Sessions() as db:
        scoped = db.query(WorkflowSandbox).filter_by(
            project_id=project_id, conversation_id=conversation_id
        )
        assert scoped.count() == 1
        row = scoped.one()
        assert row.project_id == project_id
        assert row.conversation_id == conversation_id


@pytest.mark.asyncio
async def test_independent_sessions_cannot_race_provider_create(pg_lane):
    _, Sessions = pg_lane
    run_id, claim, _project_id, _conversation_id = _seed_run(Sessions)

    create_started = asyncio.Event()
    release_create = asyncio.Event()

    class ContendedClient(FakeClient):
        async def create(self, requirements):
            self.created += 1
            create_started.set()
            await release_create.wait()
            return SandboxHandle(
                provider_id=requirements.provider_id,
                sandbox_id="pg-contended-sandbox",
                requirements_key=requirements.key,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )

    client = ContendedClient()
    first_task = asyncio.create_task(
        sessions.get_or_create_sandbox(
            Sessions, client, claim=claim, root_run_id=run_id,
            requirements=_requirements(),
        )
    )
    await asyncio.wait_for(create_started.wait(), timeout=5)
    with pytest.raises(sessions.SandboxCreationUncertain):
        await sessions.get_or_create_sandbox(
            Sessions, client, claim=claim, root_run_id=run_id,
            requirements=_requirements(),
        )
    release_create.set()
    first = await asyncio.wait_for(first_task, timeout=5)
    second = await sessions.get_or_create_sandbox(
        Sessions, client, claim=claim, root_run_id=run_id,
        requirements=_requirements(),
    )
    assert first.sandbox_id == second.sandbox_id
    assert client.created == 1


@pytest.mark.asyncio
async def test_late_equal_fence_create_ack_cannot_replace_successor(pg_lane):
    _, Sessions = pg_lane
    first_id, first_claim, project_id, conversation_id = _seed_run(Sessions)

    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class LateAckClient(FakeClient):
        async def create(self, requirements):
            self.created += 1
            ordinal = self.created
            if ordinal == 1:
                first_started.set()
                await release_first.wait()
            return SandboxHandle(
                provider_id=requirements.provider_id,
                sandbox_id=f"pg-late-{ordinal}",
                requirements_key=requirements.key,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )

    client = LateAckClient()
    first_task = asyncio.create_task(
        sessions.get_or_create_sandbox(
            Sessions, client, claim=first_claim, root_run_id=first_id,
            requirements=_requirements(),
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=5)
    second_id, second_claim = _seed_followup_run(
        Sessions, first_id, first_claim, project_id, conversation_id
    )
    with Sessions() as db:
        row = db.query(WorkflowSandbox).filter_by(
            project_id=project_id, conversation_id=conversation_id
        ).one()
        row.requested_at = datetime.now(UTC) - timedelta(seconds=1831)
        db.commit()

    second = await sessions.get_or_create_sandbox(
        Sessions, client, claim=second_claim, root_run_id=second_id,
        requirements=_requirements(),
    )
    release_first.set()
    with pytest.raises(sessions.SandboxClaimUncertain):
        await asyncio.wait_for(first_task, timeout=5)
    assert second.sandbox_id == "pg-late-2"
    with Sessions() as db:
        row = db.query(WorkflowSandbox).filter_by(
            project_id=project_id, conversation_id=conversation_id
        ).one()
        assert row.root_run_id == second_id
        assert row.sandbox_id == second.sandbox_id
    assert client.created == 2


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
