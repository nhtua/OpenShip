"""A project row lock must serialize concurrent quota reservations in PostgreSQL."""

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.artifacts.service import reserve_artifact
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def postgres_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; artifact quota concurrency requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database_name = "phase4_artifact_quota_lane"
    lane_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-artifact-quota") / "alembic.ini"
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
    engine = create_engine(lane_url, pool_size=6, max_overflow=4)
    Sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield Sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        finally:
            admin.dispose()


def test_simultaneous_reservations_cannot_exceed_shared_project_quota(postgres_sessions, monkeypatch):
    monkeypatch.setattr(settings, "artifact_project_quota_bytes", 5)
    monkeypatch.setattr(settings, "artifact_project_quota_count", 10)
    monkeypatch.setattr(settings, "artifact_conversation_quota_bytes", 100)
    setup = postgres_sessions()
    suffix = uuid.uuid4().hex
    user = User(username=f"quota_{suffix}", email=f"{suffix}@example.test", password_hash="hash")
    setup.add(user)
    setup.flush()
    project = Project(owner_user_id=user.id, name="Shared quota")
    setup.add(project)
    setup.flush()
    conversations = [
        Conversation(user_id=user.id, project_id=project.id, title=f"Quota {index}")
        for index in range(2)
    ]
    setup.add_all(conversations)
    setup.commit()
    project_id = project.id
    conversation_ids = [item.id for item in conversations]
    setup.close()

    barrier = threading.Barrier(2)

    def reserve(index):
        db = postgres_sessions()
        try:
            barrier.wait(timeout=10)
            artifact = reserve_artifact(
                db,
                conversation_ids[index],
                project_id,
                4,
                "upload",
                str(uuid.uuid4()),
                expected_sha256=(f"{index + 1:064x}"),
                display_name=f"upload-{index}.txt",
                media_type="text/plain",
            )
            db.commit()
            return ("reserved", artifact.id)
        except HTTPException as error:
            db.rollback()
            return ("refused", error.status_code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sum(result[0] == "reserved" for result in outcomes) == 1
    assert sum(result == ("refused", 413) for result in outcomes) == 1

    db = postgres_sessions()
    try:
        project_quota = db.query(ArtifactQuota).filter(
            ArtifactQuota.project_id == project_id,
            ArtifactQuota.scope == "project",
        ).one()
        assert project_quota.reserved_bytes == 4
        assert project_quota.reserved_count == 1
        assert db.query(Artifact).filter(Artifact.project_id == project_id).count() == 1
    finally:
        db.close()
