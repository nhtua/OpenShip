"""The additive 4C migration preserves legacy conversations and protects bindings."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.workspace.models import ConversationWorkspace, Project
from src.openship.workspace.service import ensure_workspace
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


def _alembic_ini(api_root: Path, path: Path, url: str) -> Path:
    path.write_text(
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
handlers =
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


def _alembic(api_root: Path, ini: Path, *args: str):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={
            "PYTHONPATH": str(api_root / "src"),
            "ALEMBIC_CONFIG": str(ini),
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_upgrade_is_additive_lazy_and_downgrade_protects_workspace_bindings(tmp_path):
    api_root = Path(__file__).parents[2]
    db_path = tmp_path / "workspace-migration.db"
    artifact_root = tmp_path / "must-not-be-created"
    ini = _alembic_ini(api_root, tmp_path / "alembic.ini", f"sqlite:///{db_path}")
    before = _alembic(api_root, ini, "upgrade", "phase4b007")
    assert before.returncode == 0, before.stderr

    engine = create_engine(f"sqlite:///{db_path}")
    user_id = "10000000-0000-0000-0000-000000000001"
    project_id = "20000000-0000-0000-0000-000000000001"
    conversation_id = "30000000-0000-0000-0000-000000000001"
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, username, email, password_hash) VALUES (:id, 'legacy', 'legacy@example.test', 'hash')"),
            {"id": user_id},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'legacy')"),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text("INSERT INTO conversations (id, user_id, project_id, title) VALUES (:id, :user, :project, 'legacy')"),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )

    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert "conversation_workspaces" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM conversations WHERE id=:id"),
            {"id": conversation_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM conversation_workspaces")
        ).scalar_one() == 0
    assert not artifact_root.exists()

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversation_workspaces "
                "(id, conversation_id, project_id, schema_version, relative_root, storage_id, status) "
                "VALUES ('50000000-0000-0000-0000-000000000001', :conversation, :project, 1, :root, :storage, 'available')"
            ),
            {
                "conversation": conversation_id,
                "project": project_id,
                "root": f"projects/{project_id}/conversations/{conversation_id}",
                "storage": "40000000-0000-0000-0000-000000000001",
            },
        )
    refused = _alembic(api_root, ini, "downgrade", "phase4b007")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()
    assert "workspace" in refused.stderr.lower()
    engine.dispose()


@pytest.fixture(scope="module")
def pg_session_factory(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; workspace association concurrency requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database = f"phase4c_workspace_{uuid.uuid4().hex[:8]}"
    lane_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = _alembic_ini(
        api_root,
        tmp_path_factory.mktemp("phase4c-workspace") / "alembic.ini",
        lane_url,
    )
    built = _alembic(api_root, ini, "upgrade", "head")
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


def test_concurrent_ensure_returns_one_stable_association(pg_session_factory, tmp_path):
    suffix = uuid.uuid4().hex
    with pg_session_factory() as db:
        user = User(
            username=f"workspace_{suffix[:12]}",
            email=f"workspace_{suffix}@example.test",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Workspace concurrency")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title="Concurrent ensure",
        )
        db.add(conversation)
        db.commit()
        user_id = user.id
        project_id = project.id
        conversation_id = conversation.id

    storage_id = uuid.uuid4()
    store = LocalArtifactStore(tmp_path / "pg-artifacts", storage_id)
    store.initialize()
    gate = threading.Barrier(2)

    def ensure():
        gate.wait(timeout=5)
        with pg_session_factory() as db:
            summary = ensure_workspace(db, user_id, conversation_id, store)
            db.commit()
            return summary.id, summary.relative_root, summary.storage_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: ensure(), range(2)))

    assert results[0] == results[1]
    assert results[0][1] == f"projects/{project_id}/conversations/{conversation_id}"
    assert results[0][2] == storage_id
    with pg_session_factory() as db:
        assert db.query(ConversationWorkspace).filter_by(conversation_id=conversation_id).count() == 1
    assert not (store.root / results[0][1]).exists()
