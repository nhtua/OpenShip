"""PostgreSQL row ordering and admitted-worker behavior for archive lifecycle."""

from __future__ import annotations

import hashlib
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

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import mark_artifact_ready, reserve_artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import (
    list_capability_manifests,
    list_legacy_search_manifests,
)
from src.openship.capabilities.contracts import CapabilityRef, ToolCommandRequest
from src.openship.chat.lifecycle import (
    archive_conversation,
    restore_conversation,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.selection import ConnectorSelectionError
from src.openship.runs import service as run_service
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import claim_next
from src.openship.runs.service import request_cancel, submit_tool_run, submit_turn
from src.openship.tools.search.contracts import SearchObservation
from src.openship.workspace.models import Project
from src.openship.workspace.projects import (
    ProjectLifecycleError,
    archive_project,
    restore_project,
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


@pytest.fixture(scope="module")
def archive_postgres(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; archive ordering requires disposable PostgreSQL")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    database_name = f"openship_archive_{uuid.uuid4().hex[:20]}"
    lane_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()

    api_root = Path(__file__).parents[2]
    ini = _alembic_ini(
        api_root,
        tmp_path_factory.mktemp("archive-lifecycle") / "alembic.ini",
        lane_url,
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
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield {"engine": engine, "sessions": sessions, "url": lane_url}
    finally:
        engine.dispose()
        admin_engine = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            admin_engine.dispose()


def _new_scope(sessions, label: str):
    suffix = uuid.uuid4().hex
    with sessions() as db:
        user = User(
            username=f"archive-{suffix[:18]}",
            email=f"{suffix}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name=f"{label} primary")
        backup = Project(owner_user_id=user.id, name=f"{label} backup")
        db.add_all([project, backup])
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title=f"{label} conversation",
        )
        second_conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title=f"{label} second conversation",
        )
        db.add_all([conversation, second_conversation])
        db.commit()
        return user.id, project.id, backup.id, conversation.id, second_conversation.id


def _submit_chat(sessions, user_id, conversation_id, request_id):
    with sessions() as db:
        try:
            return submit_turn(
                db,
                user_id,
                str(conversation_id),
                "archive ordering probe",
                request_id,
                provider_profile_id="primary-fixture",
            )
        except Exception:
            db.rollback()
            raise


def test_archive_serializes_with_admission(archive_postgres, monkeypatch):
    sessions = archive_postgres["sessions"]
    user_id, project_id, _backup_id, conversation_id, _ = _new_scope(
        sessions, "archive first"
    )
    archived_lock_held = threading.Event()
    release_archive = threading.Event()

    def archive_and_hold():
        with sessions() as db:
            archive_project(db, owner_id=user_id, project_id=project_id)
            archived_lock_held.set()
            assert release_archive.wait(timeout=5), "archive lock was not released in time"
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        archive_future = pool.submit(archive_and_hold)
        assert archived_lock_held.wait(timeout=5), "archive did not acquire its locks"
        submit_future = pool.submit(
            _submit_chat, sessions, user_id, conversation_id, str(uuid.uuid4())
        )
        assert not submit_future.done(), "admission did not wait for the archive row lock"
        release_archive.set()
        archive_future.result(timeout=5)
        with pytest.raises(HTTPException) as refused:
            submit_future.result(timeout=5)
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "project_archived"

    with sessions() as db:
        restore_project(db, owner_id=user_id, project_id=project_id)
        db.commit()

    scope_locked = threading.Event()
    release_admission = threading.Event()
    archive_done = threading.Event()
    original_resolve = run_service._resolve_selection

    def resolve_while_holding_scope(*args, **kwargs):
        scope_locked.set()
        assert release_admission.wait(timeout=5), "admission scope lock was not released in time"
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(run_service, "_resolve_selection", resolve_while_holding_scope)

    def archive_after_admission():
        with sessions() as db:
            archive_project(db, owner_id=user_id, project_id=project_id)
            db.commit()
            archive_done.set()

    request_id = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        submit_future = pool.submit(
            _submit_chat, sessions, user_id, conversation_id, request_id
        )
        assert scope_locked.wait(timeout=5), "admission did not lock its scope"
        archive_future = pool.submit(archive_after_admission)
        assert not archive_done.wait(timeout=0.2), "archive passed an uncommitted admission"
        release_admission.set()
        admitted = submit_future.result(timeout=5)
        archive_future.result(timeout=5)
    assert admitted.client_request_id == request_id
    with sessions() as db:
        assert db.query(Run).filter(Run.id == admitted.id).one().status == "queued"
        assert db.get(Project, project_id).archived_at is not None


def test_concurrent_project_archives_leave_one_active(archive_postgres):
    sessions = archive_postgres["sessions"]
    user_id, project_id, backup_id, _conversation_id, _ = _new_scope(
        sessions, "concurrent projects"
    )
    start = threading.Barrier(2)

    def archive_one(project_id_to_archive):
        with sessions() as db:
            start.wait(timeout=5)
            try:
                archive_project(
                    db,
                    owner_id=user_id,
                    project_id=project_id_to_archive,
                )
                db.commit()
                return "archived"
            except ProjectLifecycleError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(archive_one, (project_id, backup_id)))
    assert sorted(results) == ["archived", "last_project_required"]
    with sessions() as db:
        active_count = db.query(Project).filter(
            Project.owner_user_id == user_id,
            Project.archived_at.is_(None),
        ).count()
    assert active_count >= 1


def test_restore_serializes_with_parent_archive(archive_postgres):
    sessions = archive_postgres["sessions"]
    user_id, project_id, _backup_id, conversation_id, _ = _new_scope(
        sessions, "restore race"
    )
    with sessions() as db:
        archive_conversation(
            db,
            owner_id=user_id,
            conversation_id=conversation_id,
        )
        db.commit()

    restore_lock_held = threading.Event()
    release_restore = threading.Event()
    archive_finished = threading.Event()

    def restore_and_hold():
        with sessions() as db:
            conversation, _project = restore_conversation(
                db,
                owner_id=user_id,
                conversation_id=conversation_id,
            )
            restore_lock_held.set()
            assert release_restore.wait(timeout=5), "restore lock was not released in time"
            db.commit()
            return conversation.id

    def archive_parent():
        with sessions() as db:
            archive_project(db, owner_id=user_id, project_id=project_id)
            db.commit()
            archive_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        restore_future = pool.submit(restore_and_hold)
        assert restore_lock_held.wait(timeout=5), "restore did not acquire its locks"
        archive_future = pool.submit(archive_parent)
        assert not archive_finished.wait(timeout=0.2), "parent archive passed the restore lock"
        release_restore.set()
        assert restore_future.result(timeout=5) == conversation_id
        archive_future.result(timeout=5)

    with sessions() as db:
        conversation = db.get(Conversation, conversation_id)
        project = db.get(Project, project_id)
        assert conversation.archived_at is None
        assert project.archived_at is not None


def _search_command():
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    return ToolCommandRequest(
        client_request_id=uuid.uuid4(),
        capability=CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        ),
        arguments={"query": "archive retained output", "max_results": 1},
    )


def test_admitted_worker_publishes_after_archive(
    archive_postgres, monkeypatch, tmp_path
):
    from src.openship.capabilities.definitions import TOOL_HANDLER_OVERRIDES
    from src.openship.runs.worker import Worker

    sessions = archive_postgres["sessions"]
    user_id, project_id, _backup_id, conversation_id, second_conversation_id = _new_scope(
        sessions, "worker after archive"
    )
    monkeypatch.setattr(settings, "database_url", archive_postgres["url"])
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    monkeypatch.setattr(settings, "artifact_root", Path(tmp_path / "artifacts"))
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    store = LocalArtifactStore(settings.artifact_root, storage_id)
    store.initialize()

    def no_managed_connector(*_args, **_kwargs):
        raise ConnectorSelectionError("connector_required")

    monkeypatch.setattr(run_service, "resolve_connector", no_managed_connector)
    monkeypatch.setattr(run_service, "_connector_rows_for_project", lambda *_args, **_kwargs: 0)
    with sessions() as db:
        existing_bytes = b"retained resource evidence"
        existing_digest = hashlib.sha256(existing_bytes).hexdigest()
        existing = reserve_artifact(
            db,
            conversation_id,
            project_id,
            len(existing_bytes),
            "upload",
            str(uuid.uuid4()),
            expected_sha256=existing_digest,
            display_name="existing.txt",
            media_type="text/plain",
            source="upload",
        )
        existing_ref = ArtifactRef(
            id=existing.id,
            sha256=existing_digest,
            size_bytes=len(existing_bytes),
            media_type="text/plain",
        )
        staged = store.stage(
            existing.id,
            (existing_bytes,),
            existing_ref,
            project_id=project_id,
            conversation_id=conversation_id,
        )
        store.publish_file(staged, existing.relative_storage_key)
        mark_artifact_ready(db, existing.id, existing_ref)
        search_run = submit_tool_run(db, user_id, conversation_id, _search_command())
        db.commit()
        # Other cases share this module-scoped disposable database; keep their
        # accepted but unclaimed jobs from taking this worker's next lease.
        db.query(Run).filter(
            Run.id != search_run.id,
            Run.status == "queued",
        ).update({Run.status: "completed"}, synchronize_session=False)
        db.commit()
        claim = claim_next(db, worker_id="archive-worker", lease_seconds=30)
        assert claim is not None and claim.run_id == search_run.id
        cancellation_run = submit_tool_run(
            db, user_id, second_conversation_id, _search_command()
        )
        db.commit()
        db.query(Run).filter(Run.id == cancellation_run.id).update(
            {Run.status: "running", Run.fence: 1},
            synchronize_session=False,
        )
        db.query(RunJob).filter(RunJob.run_id == cancellation_run.id).update(
            {
                RunJob.fence: 1,
                RunJob.owner_id: "active-cancelled-worker",
                RunJob.lease_until: datetime.now(UTC) + timedelta(minutes=1),
            },
            synchronize_session=False,
        )
        db.commit()
        archive_project(db, owner_id=user_id, project_id=project_id)
        db.commit()
        cancelled = request_cancel(db, user_id, str(cancellation_run.id))
        assert cancelled.status == "cancelled"

    search_calls = []
    manifest = next(
        item for item in list_legacy_search_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )

    async def synthetic_search(arguments, _invocation, _control):
        search_calls.append(arguments.query)
        return SearchObservation(
            tool=ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=1,
            items=(
                {
                    "title": "A retained result",
                    "url": "https://example.test/archive",
                    "snippet": "Synthetic output produced after archive.",
                    "rank": 1,
                },
            ),
            truncated=False,
            provider_metadata={},
        )

    monkeypatch.setitem(TOOL_HANDLER_OVERRIDES, "brave_web_search", synthetic_search)
    monkeypatch.setattr(
        "src.openship.runs.worker.credential_value",
        lambda _binding: "synthetic-search-secret",
    )
    monkeypatch.setattr("src.openship.runs.worker.configured_store", lambda: store)
    assert search_calls == []

    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=sessions,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim)
    finally:
        worker.engine.dispose()

    with sessions() as db:
        completed = db.get(Run, search_run.id)
        assert completed.status == "completed"
        assert search_calls == ["archive retained output"]
        artifacts = db.query(Artifact).filter(
            Artifact.conversation_id == conversation_id,
            Artifact.status == "ready",
        ).order_by(Artifact.created_at, Artifact.id).all()
        assert len(artifacts) >= 2
        retained = next(item for item in artifacts if item.id == existing.id)
        with store.open_verified(retained) as content_file:
            assert content_file.read() == existing_bytes
        assert all(item.originating_run_id != search_run.id or item.status == "ready" for item in artifacts)
        assert db.get(Project, project_id).archived_at is not None
