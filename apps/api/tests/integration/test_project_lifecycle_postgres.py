"""PostgreSQL migration and lock-order guarantees for Project lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.models import ArtifactQuota
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.connectors.models import ProjectConnectorDefault
from src.openship.workspace.lifecycle import ArchiveStateError
from src.openship.workspace.models import Project
from src.openship.workspace.projects import (
    ProjectLifecycleError,
    delete_project,
    select_project_for_conversation,
)
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
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers = console
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


def _alembic(api_root: Path, ini: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def project_postgres(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; project locking requires a disposable PostgreSQL lane")

    bounded_url = bounded_postgres_url(service_url)
    parsed_url = make_url(bounded_url)
    database_name = f"phase5a1_projects_{uuid.uuid4().hex}"
    lane_url = parsed_url.set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()

    api_root = Path(__file__).parents[2]
    ini = _alembic_ini(
        api_root,
        tmp_path_factory.mktemp("phase5a1-projects") / "alembic.ini",
        lane_url,
    )
    engine = None
    try:
        built = _alembic(api_root, ini, "upgrade", "head")
        assert built.returncode == 0, built.stderr
        engine = create_engine(lane_url, pool_size=6, max_overflow=4)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        yield {"engine": engine, "sessions": sessions, "api_root": api_root, "ini": ini}
    finally:
        if engine is not None:
            engine.dispose()
        admin_engine = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            admin_engine.dispose()


def test_migration_preserves_existing_project_references_and_refuses_unsafe_downgrade(
    project_postgres,
):
    sessions = project_postgres["sessions"]
    suffix = uuid.uuid4().hex
    with sessions() as db:
        user = User(
            username=f"project-migration-{suffix[:16]}",
            email=f"{suffix}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Preserved project")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title="Preserved conversation",
        )
        quota = ArtifactQuota(scope="project", project_id=project.id)
        default = ProjectConnectorDefault(
            project_id=project.id,
            service="brave",
            updated_by_user_id=user.id,
        )
        db.add_all([conversation, quota, default])
        db.commit()
        project_id = project.id
        user_id = user.id
        conversation_id = conversation.id
        quota_id = quota.id
        default_id = default.id

    api_root = project_postgres["api_root"]
    ini = project_postgres["ini"]
    downgraded = _alembic(api_root, ini, "downgrade", "phase5a009b")
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with project_postgres["engine"].connect() as connection:
        head_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    with sessions() as db:
        assert db.get(Project, project_id).owner_user_id == user_id
        assert db.get(Conversation, conversation_id).project_id == project_id
        assert db.get(ArtifactQuota, quota_id).project_id == project_id
        assert db.get(ProjectConnectorDefault, default_id).project_id == project_id
        db.add(Project(owner_user_id=user_id, name="Second preserved project"))
        db.commit()

    refused = _alembic(api_root, ini, "downgrade", "phase5a009b")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()
    assert "multiple projects" in refused.stderr.lower()
    with project_postgres["engine"].connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == head_revision
        )


def test_concurrent_deletion_cannot_remove_both_last_projects(project_postgres):
    sessions = project_postgres["sessions"]
    suffix = uuid.uuid4().hex
    with sessions() as db:
        user = User(
            username=f"project-delete-{suffix[:16]}",
            email=f"{suffix}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        projects = [
            Project(owner_user_id=user.id, name="First"),
            Project(owner_user_id=user.id, name="Second"),
        ]
        db.add_all(projects)
        db.commit()
        user_id = user.id
        project_ids = [project.id for project in projects]

    gate = threading.Barrier(2)

    def remove(project_id):
        with sessions() as db:
            gate.wait(timeout=10)
            try:
                delete_project(db, owner_id=user_id, project_id=project_id)
                db.commit()
                return "archived"
            except ProjectLifecycleError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(remove, project_ids))

    assert outcomes.count("archived") == 1
    assert outcomes.count("last_project_required") == 1
    with sessions() as db:
        assert db.query(Project).filter(Project.owner_user_id == user_id).count() == 2
        assert db.query(Project).filter(
            Project.owner_user_id == user_id,
            Project.archived_at.is_(None),
        ).count() == 1


def test_concurrent_child_creation_and_project_deletion_never_leave_an_orphan(project_postgres):
    sessions = project_postgres["sessions"]
    suffix = uuid.uuid4().hex
    with sessions() as db:
        user = User(
            username=f"project-child-{suffix[:16]}",
            email=f"{suffix}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Concurrent target")
        db.add_all([project, Project(owner_user_id=user.id, name="Keep alive")])
        db.commit()
        user_id = user.id
        project_id = project.id

    gate = threading.Barrier(2)

    def create_child():
        with sessions() as db:
            gate.wait(timeout=10)
            try:
                select_project_for_conversation(
                    db,
                    owner_id=user_id,
                    project_id=project_id,
                )
            except ArchiveStateError as exc:
                db.rollback()
                return exc.code
            conversation = Conversation(
                user_id=user_id,
                project_id=project_id,
                title="Concurrent child",
            )
            db.add(conversation)
            db.commit()
            return "created"

    def remove_project():
        with sessions() as db:
            gate.wait(timeout=10)
            try:
                delete_project(db, owner_id=user_id, project_id=project_id)
                db.commit()
                return "archived"
            except ProjectLifecycleError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        child, deletion = tuple(pool.map(lambda action: action(), (create_child, remove_project)))

    with sessions() as db:
        project_exists = db.get(Project, project_id) is not None
        child_count = db.query(Conversation).filter(Conversation.project_id == project_id).count()
        assert project_exists
        assert deletion == "archived"
        assert db.get(Project, project_id).archived_at is not None
        if child == "created":
            assert child_count == 1
        else:
            assert child == "project_archived"
            assert child_count == 0
