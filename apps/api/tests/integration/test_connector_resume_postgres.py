"""PostgreSQL race coverage for exact Connector wait resume commands."""

from __future__ import annotations

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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.openship.auth.models import User
from src.openship.capabilities.contracts import (
    ConnectorRef,
    ExecutionConfigV2,
    canonical_digest,
)
from src.openship.chat.models import Conversation
from src.openship.connectors.blockers import resume_connector_wait
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.database.models import Base
from src.openship.events.service import Event
from src.openship.resources.models import ConnectorBlocker
from src.openship.runs.models import Run, RunJob
from src.openship.runs.service import request_cancel
from src.openship.workspace.models import Project


@pytest.fixture(scope="module")
def postgres_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; Connector resume PostgreSQL proof is opt-in")
    parsed_url = make_url(service_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.skip("TEST_POSTGRES_URL must point to PostgreSQL")

    schema = f"openship_resume_{uuid.uuid4().hex}"
    bounded_url = parsed_url.update_query_dict({"connect_timeout": "5"})
    admin_engine = create_engine(bounded_url, poolclass=NullPool)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except SQLAlchemyError:
        admin_engine.dispose()
        pytest.skip("configured PostgreSQL service is unavailable")
    engine = None
    try:
        options = parsed_url.query.get("options", "")
        scoped_options = (
            f"{options} -csearch_path={schema} -c lock_timeout=5000 "
            "-c statement_timeout=30000"
        ).strip()
        scoped_url = bounded_url.update_query_dict(
            {"options": scoped_options}
        ).render_as_string(hide_password=False)
        api_root = Path(__file__).parents[2]
        ini = tmp_path_factory.mktemp("connector-resume") / "alembic.ini"
        ini.write_text(
            f"[alembic]\nscript_location = {api_root / 'alembic'}\n"
            f"sqlalchemy.url = {scoped_url.replace('%', '%%')}\n",
            encoding="utf-8",
        )
        ini.chmod(0o600)
        try:
            upgraded = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(ini),
                    "upgrade",
                    "head",
                ],
                cwd=api_root,
                env={
                    "PYTHONPATH": str(api_root / "src"),
                    "ALEMBIC_CONFIG": str(ini),
                },
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        finally:
            ini.unlink(missing_ok=True)
        assert upgraded.returncode == 0, "Connector resume schema migration failed"

        engine = create_engine(scoped_url, poolclass=NullPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        yield sessions
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except SQLAlchemyError:
            pytest.fail(
                "temporary Connector resume schema could not be removed", pytrace=False
            )
        finally:
            admin_engine.dispose()


def _seed_waiting_run(sessions):
    now = datetime.now(UTC)
    with sessions() as db:
        user = User(
            id=uuid.uuid4(),
            username=f"resume-{uuid.uuid4().hex}",
            email=f"resume-{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(
            id=uuid.uuid4(), owner_user_id=user.id, name="Resume race project"
        )
        db.add(project)
        db.flush()
        conversation = Conversation(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=user.id,
            title="Resume race conversation",
        )
        connector = Connector(
            id=uuid.uuid4(),
            kind="brave",
            visibility="global",
            project_id=None,
            name="Synthetic global search",
            current_revision=1,
            enabled=True,
            managed_by_user_id=user.id,
            created_by_user_id=user.id,
        )
        credential = CredentialVersion(
            id=uuid.uuid4(),
            connector_id=connector.id,
            visibility="global",
            project_id=None,
            kind="brave_api_key",
            schema_version=1,
            key_id="resume-race-v1",
            nonce=b"n" * 12,
            ciphertext=b"c" * 16,
            state="active",
        )
        db.add_all([conversation, connector, credential])
        db.flush()
        reference = ConnectorRef(
            id=connector.id,
            kind="brave",
            configuration_revision=1,
            visibility="global",
            credential_version_id=credential.id,
        )
        db.add(
            ConnectorRevision(
                id=uuid.uuid4(),
                connector_id=connector.id,
                revision=1,
                schema_version=1,
                visibility="global",
                project_id=None,
                configuration_json="{}",
                configuration_digest="a" * 64,
                credential_version_id=credential.id,
                created_by_user_id=user.id,
            )
        )
        execution = ExecutionConfigV2(
            graph_kind="chat",
            graph_version=2,
            connector_refs=(reference,),
        )
        run = Run(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            project_id=project.id,
            client_request_id=str(uuid.uuid4()),
            status="waiting_connector",
            fence=1,
            graph_version=2,
            execution_configuration=execution.model_dump_json(),
            execution_started_at=now,
            execution_deadline_at=now + timedelta(hours=1),
        )
        db.add(run)
        db.flush()
        job = RunJob(
            id=uuid.uuid4(),
            run_id=run.id,
            fence=1,
            owner_id=None,
            lease_until=None,
        )
        blocker = ConnectorBlocker(
            id=uuid.uuid4(),
            project_id=project.id,
            conversation_id=conversation.id,
            run_id=run.id,
            connector_kind="brave",
            connector_visibility="global",
            connector_project_id=None,
            connector_id=connector.id,
            connector_revision=1,
            credential_version_id=credential.id,
            configuration_digest=canonical_digest(execution.model_dump(mode="json")),
            request_id=uuid.uuid4(),
            reason="credential_store_unavailable",
            state="waiting",
            expires_at=now + timedelta(hours=1),
        )
        db.add_all([job, blocker])
        db.commit()
        return user.id, run.id, blocker.id, blocker.request_id, blocker.configuration_digest


@pytest.mark.postgres
def test_resume_racing_cancel_leaves_one_closed_blocker(postgres_sessions, monkeypatch):
    from src.openship.connectors import blockers as blocker_service

    monkeypatch.setattr(
        blocker_service, "preflight_connector_references", lambda **_kwargs: None
    )
    user_id, run_id, blocker_id, request_id, digest = _seed_waiting_run(postgres_sessions)
    start = threading.Barrier(3)

    def resume():
        with postgres_sessions() as db:
            start.wait(timeout=10)
            try:
                run = resume_connector_wait(
                    db,
                    user_id=user_id,
                    run_id=run_id,
                    blocker_id=blocker_id,
                    request_id=request_id,
                    expected_configuration_digest=digest,
                    session_factory=postgres_sessions,
                )
                return ("resumed", run.status)
            except HTTPException as error:
                return ("closed", error.status_code)

    def cancel():
        with postgres_sessions() as db:
            start.wait(timeout=10)
            run = request_cancel(db, user_id, str(run_id))
            return run.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        resume_future = pool.submit(resume)
        cancel_future = pool.submit(cancel)
        start.wait(timeout=10)
        resume_result = resume_future.result(timeout=20)
        cancel_status = cancel_future.result(timeout=20)

    assert cancel_status == "cancelled"
    assert resume_result[0] in {"resumed", "closed"}
    with postgres_sessions() as db:
        run = db.get(Run, run_id)
        job = db.query(RunJob).filter_by(run_id=run_id).one()
        blocker = db.get(ConnectorBlocker, blocker_id)
        assert run.status == "cancelled"
        assert blocker.state == "cancelled"
        assert job.owner_id is None
        assert job.lease_until is None
        assert job.fence == run.fence
        assert db.query(Event).filter_by(run_id=run_id, type="connector.resumed").count() <= 1
