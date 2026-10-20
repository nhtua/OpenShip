"""PostgreSQL transcript ordering and fencing under concurrent writers."""

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
from src.openship.chat.models import Conversation
from src.openship.providers.contracts import ChatMessage
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.transcript import append_transcript, read_transcript
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; transcript concurrency requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database = "phase4_tool_transcript_lane"
    lane_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-tool-transcript") / "alembic.ini"
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


def test_concurrent_appends_are_ordered_and_stale_fence_cannot_append(pg_sessions):
    db = pg_sessions()
    suffix = uuid.uuid4().hex
    user = User(username=f"transcript_{suffix}", email=f"transcript_{suffix}@example.test", password_hash="hash")
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Transcript concurrency")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Transcript")
    db.add(conversation)
    db.flush()
    run = Run(conversation_id=conversation.id, project_id=project.id, status="running", kind="chat", graph_version=2, fence=1)
    db.add(run)
    db.flush()
    lease_until = datetime.now(UTC) + timedelta(minutes=1)
    db.add(RunJob(run_id=run.id, fence=1, owner_id="transcript-worker", lease_until=lease_until))
    run_id, user_id = run.id, user.id
    db.commit()
    db.close()
    claim = Claim(run_id, 1, lease_until, "transcript-worker", user_id)
    barrier = threading.Barrier(2)

    def append(content):
        session = pg_sessions()
        try:
            barrier.wait(timeout=10)
            entry = append_transcript(session, claim, 0, ChatMessage(role="user", content=content))
            session.commit()
            return entry.ordinal
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ordinals = list(pool.map(append, ("first", "second")))
    assert sorted(ordinals) == [0, 1]

    stale = Claim(run_id, 0, lease_until, "transcript-worker", user_id)
    session = pg_sessions()
    try:
        with pytest.raises(HTTPException) as refused:
            append_transcript(session, stale, 0, ChatMessage(role="user", content="stale"))
        assert refused.value.detail["error"]["code"] == "lease_lost"
        session.rollback()
        assert [row.ordinal for row in read_transcript(session, run_id)] == [0, 1]
    finally:
        session.close()
