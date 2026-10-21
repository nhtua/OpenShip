"""PostgreSQL verifies durable workspace purge and quota transitions."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.artifacts.service import mark_artifact_ready, reserve_artifact
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.runs.models import Run
from src.openship.workspace.maintenance import plan_workspace_purge, purge_workspace
from src.openship.workspace.models import ConversationWorkspace, Project
from src.openship.workspace.service import ensure_workspace
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


@pytest.fixture(scope="module")
def postgres_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; workspace purge requires PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database = f"phase4_workspace_purge_{uuid.uuid4().hex[:8]}"
    lane_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("phase4-workspace-purge") / "alembic.ini"
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
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        finally:
            admin.dispose()


def test_active_pin_refuses_workspace_purge_then_committed_purge_releases_quota(
    postgres_sessions, tmp_path
):
    db = postgres_sessions()
    try:
        suffix = uuid.uuid4().hex
        user = User(
            username=f"purge_{suffix[:12]}",
            email=f"purge_{suffix}@example.test",
            password_hash="fixture-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="PostgreSQL purge")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Purge")
        db.add(conversation)
        db.flush()
        store = LocalArtifactStore(tmp_path / "pg-artifacts", uuid.uuid4())
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        content = b"retained snapshot"
        digest = hashlib.sha256(content).hexdigest()
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            len(content),
            "upload",
            str(uuid.uuid4()),
            expected_sha256=digest,
            display_name="snapshot.txt",
            media_type="text/plain",
        )
        expected = ArtifactRef(
            id=artifact.id,
            sha256=digest,
            size_bytes=len(content),
            media_type="text/plain",
        )
        staged = store.stage(
            artifact.id,
            [content],
            expected,
            project_id=project.id,
            conversation_id=conversation.id,
        )
        store.publish_file(staged, artifact.relative_storage_key)
        mark_artifact_ready(db, artifact.id, expected)
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="running",
            execution_configuration=(
                '{"selected_artifacts":[{"id":"'
                + str(artifact.id)
                + '","sha256":"'
                + digest
                + '"}]}'
            ),
        )
        db.add(run)
        db.commit()

        with pytest.raises(HTTPException) as pinned:
            plan_workspace_purge(db, workspace.id)
        assert pinned.value.detail["error"]["code"] == "artifact_is_pinned"
        assert db.get(Artifact, artifact.id).status == "ready"

        run.status = "completed"
        db.commit()
        plan = plan_workspace_purge(db, workspace.id)
        report = purge_workspace(db, store, plan)
        db.expire_all()

        assert report.deleted == 1
        assert report.failed == 0
        assert db.get(Artifact, artifact.id).status == "deleted"
        assert db.get(Artifact, artifact.id).sha256 == digest
        assert db.get(ConversationWorkspace, workspace.id).status == "deleted"
        quotas = db.query(ArtifactQuota).filter_by(project_id=project.id).all()
        assert all(row.committed_bytes == 0 and row.committed_count == 0 for row in quotas)
        assert not (store.root / artifact.relative_storage_key).exists()
    finally:
        db.close()
