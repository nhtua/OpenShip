from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.contracts import (
    ConnectorRef,
    ExecutionConfigV2,
    ResourceBindingRef,
)
from src.openship.chat.models import Conversation
from src.openship.connectors.blockers import (
    configuration_digest,
    dismiss_connector_wait,
    expire_connector_blockers,
    park_connector_wait,
    resume_connector_wait,
)
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.readiness import ConnectorReadinessFailure
from src.openship.database.models import Base
from src.openship.database.session import SessionLocal
from src.openship.messaging.models import MessageTransportState
from src.openship.resources.models import (
    ConnectorBlocker,
    Environment,
    ResourceBinding,
    ResourceObject,
)
from src.openship.runs.models import Event, Outbox, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.service import request_cancel
from src.openship.workspace.models import Project


@pytest.fixture
def track_scope():
    scopes = []

    def track(scope):
        user, project, conversation, connector, _ref, _execution, run, _job, _claim = (
            scope
        )
        scopes.append((user.id, project.id, conversation.id, connector.id, run.id))

    yield track
    with SessionLocal() as db:
        for user_id, project_id, conversation_id, connector_id, run_id in reversed(
            scopes
        ):
            event_ids = [
                row[0]
                for row in db.query(Event.id).filter(Event.run_id == run_id).all()
            ]
            db.query(Outbox).filter(Outbox.event_id.in_(event_ids)).delete(
                synchronize_session=False
            )
            db.query(ConnectorBlocker).filter(ConnectorBlocker.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(Event).filter(Event.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(RunJob).filter(RunJob.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(Run).filter(Run.id == run_id).delete(synchronize_session=False)
            db.query(ConnectorRevision).filter(
                ConnectorRevision.connector_id == connector_id
            ).delete(synchronize_session=False)
            db.query(CredentialVersion).filter(
                CredentialVersion.connector_id == connector_id
            ).delete(synchronize_session=False)
            db.query(Connector).filter(Connector.id == connector_id).delete(
                synchronize_session=False
            )
            db.query(Conversation).filter(Conversation.id == conversation_id).delete(
                synchronize_session=False
            )
            db.query(Project).filter(Project.id == project_id).delete(
                synchronize_session=False
            )
            db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        db.commit()


def _scope(
    db, *, status="running", visibility="global", deadline=None, connector_kind="brave"
):
    owner_id = uuid.uuid4()
    user = User(
        id=owner_id,
        username=f"blocker-{owner_id.hex}",
        email=f"blocker-{owner_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(id=uuid.uuid4(), owner_user_id=owner_id, name="Blocker project")
    db.add_all([user, project])
    db.flush()
    conversation = Conversation(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=user.id,
        title="Blocker conversation",
    )
    db.add(conversation)
    db.flush()

    connector_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    connector = Connector(
        id=connector_id,
        kind=connector_kind,
        visibility=visibility,
        project_id=project.id if visibility == "project" else None,
        name="Search connector",
        current_revision=1,
        enabled=True,
        managed_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    credential = CredentialVersion(
        id=credential_id,
        connector_id=connector.id,
        visibility=visibility,
        project_id=project.id if visibility == "project" else None,
        kind="aws_bootstrap" if connector_kind == "aws" else "brave_api_key",
        schema_version=1,
        key_id="blocker-test-v1",
        nonce=b"n" * 12,
        ciphertext=b"c" * 16,
        state="active",
    )
    db.add_all([connector, credential])
    db.flush()
    configuration = (
        ConnectorConfiguration(
            kind="aws",
            region="us-east-1",
            expected_account_id="123456789012",
            basic_role_arn="arn:aws:iam::123456789012:role/ReadOnly",
        )
        if connector_kind == "aws"
        else None
    )
    db.add(
        ConnectorRevision(
            id=uuid.uuid4(),
            connector_id=connector.id,
            revision=1,
            schema_version=1,
            visibility=visibility,
            project_id=connector.project_id,
            configuration_json=(
                configuration.canonical_bytes().decode("utf-8")
                if configuration is not None
                else "{}"
            ),
            configuration_digest=(
                configuration.digest() if configuration is not None else "a" * 64
            ),
            credential_version_id=credential.id,
            created_by_user_id=user.id,
        )
    )
    db.flush()
    reference = ConnectorRef(
        id=connector.id,
        kind=connector_kind,
        configuration_revision=1,
        visibility=visibility,
        credential_version_id=credential.id,
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
        status=status,
        fence=1,
        graph_version=2,
        execution_configuration=execution.model_dump_json(),
        execution_started_at=datetime.now(UTC),
        execution_deadline_at=deadline or datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(run)
    db.flush()
    job = RunJob(
        run_id=run.id,
        fence=1,
        owner_id="worker-test",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=job.lease_until,
        worker_id="worker-test",
        user_id=user.id,
    )
    return user, project, conversation, connector, reference, execution, run, job, claim


def _park(db, scope):
    user, project, conversation, _connector, reference, execution, run, job, claim = (
        scope
    )
    failure = ConnectorReadinessFailure(reference, "credential_store_unavailable")
    assert park_connector_wait(
        db,
        claim=claim,
        run_id=run.id,
        conversation_id=conversation.id,
        project_id=project.id,
        execution=execution,
        failure=failure,
    )
    db.refresh(run)
    db.refresh(job)
    return user, project, conversation, reference, execution, run, job


def test_parking_releases_ownership_and_publishes_a_secret_free_blocker(track_scope):
    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        _user, project, _conversation, _reference, _execution, run, job = _park(
            db, scope
        )

        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        event = db.query(Event).filter_by(run_id=run.id, type="connector.blocked").one()
        payload = json.loads(event.payload)

        assert run.status == "waiting_connector"
        assert run.ended_at is None
        assert job.owner_id is None
        assert job.lease_until is None
        assert blocker.project_id == project.id
        assert blocker.connector_visibility == "global"
        assert blocker.connector_project_id is None
        assert blocker.configuration_digest == configuration_digest(_execution)
        assert payload["request_id"] == str(blocker.request_id)
        assert "ciphertext" not in event.payload
        assert db.query(Outbox).filter_by(event_id=event.id).count() == 1


def test_pre_dispatch_wait_refuses_runs_that_already_have_provider_intent(track_scope):
    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        (
            _user,
            project,
            conversation,
            _connector,
            reference,
            execution,
            run,
            _job,
            claim,
        ) = scope
        run.provider_started_at = datetime.now(UTC)
        db.commit()
        assert not park_connector_wait(
            db,
            claim=claim,
            run_id=run.id,
            conversation_id=conversation.id,
            project_id=project.id,
            execution=execution,
            failure=ConnectorReadinessFailure(reference, "connector_unavailable"),
        )
        assert run.status == "running"


def test_resume_is_atomic_idempotent_and_uses_the_same_pinned_global_connector(
    monkeypatch, track_scope
):
    monkeypatch.setattr(
        "src.openship.connectors.blockers.preflight_connector_references",
        lambda **_kwargs: None,
    )
    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        user, _project, _conversation, _reference, execution, run, _job = _park(
            db, scope
        )

        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        expected_digest = configuration_digest(execution)
        active_run = Run(
            conversation_id=run.conversation_id,
            project_id=run.project_id,
            client_request_id=str(uuid.uuid4()),
            status="queued",
            kind="chat",
            graph_version=1,
        )
        db.add(active_run)
        db.commit()
        args = {
            "user_id": user.id,
            "run_id": run.id,
            "blocker_id": blocker.id,
            "request_id": blocker.request_id,
            "expected_configuration_digest": expected_digest,
            "session_factory": SessionLocal,
        }

        with pytest.raises(HTTPException) as caught:
            resume_connector_wait(db, **args)
        assert caught.value.status_code == 409
        assert blocker.state == "waiting"
        db.delete(active_run)
        db.commit()

        resumed = resume_connector_wait(db, **args)
        assert resumed.status == "queued"
        assert resumed.execution_configuration == execution.model_dump_json()
        assert blocker.state == "resumed"
        assert blocker.consumed_at is not None
        assert blocker.closed_at is not None
        assert (
            db.query(Event).filter_by(run_id=run.id, type="connector.resumed").count()
            == 1
        )

        replay = resume_connector_wait(db, **args)
        assert replay.id == resumed.id
        assert (
            db.query(Event).filter_by(run_id=run.id, type="connector.resumed").count()
            == 1
        )


def test_resume_rejects_changed_request_or_configuration_digest(
    monkeypatch, track_scope
):
    monkeypatch.setattr(
        "src.openship.connectors.blockers.preflight_connector_references",
        lambda **_kwargs: None,
    )
    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        user, _project, _conversation, _reference, execution, run, _job = _park(
            db, scope
        )

        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        args = {
            "user_id": user.id,
            "run_id": run.id,
            "blocker_id": blocker.id,
            "request_id": uuid.uuid4(),
            "expected_configuration_digest": configuration_digest(execution),
            "session_factory": SessionLocal,
        }
        with pytest.raises(HTTPException) as caught:
            resume_connector_wait(db, **args)
        assert caught.value.status_code == 409
        assert blocker.state == "waiting"
        args["request_id"] = blocker.request_id
        args["expected_configuration_digest"] = "f" * 64
        with pytest.raises(HTTPException) as caught:
            resume_connector_wait(db, **args)
        assert caught.value.status_code == 409
        assert blocker.state == "waiting"


def test_resume_rejects_a_retired_target_binding(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.openship.connectors.blockers.preflight_connector_references",
        lambda **_kwargs: None,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'retired-target.db'}")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    isolated_sessions = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    try:
        with isolated_sessions() as db:
            db.add(
                MessageTransportState(
                    id=1,
                    installation_id=uuid.uuid4(),
                    transport_epoch=uuid.uuid4(),
                    active=False,
                )
            )
            db.commit()
        with isolated_sessions() as db:
            scope = _scope(db, visibility="project", connector_kind="aws")
            user, project, conversation, connector, reference, execution, run, _job, claim = scope
            environment = Environment(
                id=uuid.uuid4(),
                project_id=project.id,
                name="production",
                display_name="Production",
                created_by_user_id=user.id,
            )
            resource = ResourceObject(
                id=uuid.uuid4(),
                project_id=project.id,
                kind="aws_s3_bucket",
                uri=f"openship://{project.id}/resource/bucket",
                display_name="Bucket",
                created_by_user_id=user.id,
            )
            db.add_all([environment, resource])
            db.flush()
            binding = ResourceBinding(
                id=uuid.uuid4(),
                project_id=project.id,
                resource_object_id=resource.id,
                environment_id=environment.id,
                connector_id=connector.id,
                connector_revision=1,
                revision=1,
                account_id="123456789012",
                region="us-east-1",
                bucket_name="phase5-resume-target",
                bucket_arn="arn:aws:s3:::phase5-resume-target",
                expected_owner="123456789012",
                management_method="external",
                state="active",
                created_by_user_id=user.id,
            )
            db.add(binding)
            db.flush()
            execution = execution.model_copy(
                update={
                    "resource_binding_refs": (
                        ResourceBindingRef(
                            id=binding.id,
                            revision=binding.revision,
                            connector_id=binding.connector_id,
                            connector_revision=binding.connector_revision,
                        ),
                    )
                }
            )
            run.execution_configuration = execution.model_dump_json()
            db.commit()

            assert park_connector_wait(
                db,
                claim=claim,
                run_id=run.id,
                conversation_id=conversation.id,
                project_id=project.id,
                execution=execution,
                failure=ConnectorReadinessFailure(reference, "connector_unavailable"),
            )
            blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
            binding.state = "retired"
            binding.retired_at = datetime.now(UTC)
            db.commit()

            with pytest.raises(HTTPException) as caught:
                resume_connector_wait(
                    db,
                    user_id=user.id,
                    run_id=run.id,
                    blocker_id=blocker.id,
                    request_id=blocker.request_id,
                    expected_configuration_digest=configuration_digest(execution),
                    session_factory=isolated_sessions,
                )

            assert caught.value.status_code == 409
            assert (
                caught.value.detail["error"]["code"]
                == "connector_configuration_changed"
            )
            assert blocker.state == "waiting"
    finally:
        engine.dispose()


def test_expiry_and_dismissal_close_waits_without_requeueing_them(track_scope):
    with SessionLocal() as db:
        scope = _scope(db, deadline=datetime.now(UTC) + timedelta(days=2))
        track_scope(scope)
        user, _project, _conversation, _reference, _execution, run, job = _park(
            db, scope
        )
        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        count = expire_connector_blockers(
            db, now=blocker.expires_at + timedelta(seconds=1)
        )
        db.refresh(run)
        db.refresh(job)
        assert count == 1
        assert blocker.state == "expired"
        assert run.status == "failed"
        assert job.owner_id is None
        assert job.lease_until is None

    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        user, _project, _conversation, _reference, _execution, run, job = _park(
            db, scope
        )
        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        dismissed = dismiss_connector_wait(
            db, user_id=user.id, run_id=run.id, blocker_id=blocker.id
        )
        assert dismissed.status == "cancelled"
        assert blocker.state == "dismissed"
        assert job.owner_id is None


def test_wait_expiry_is_capped_by_the_original_execution_deadline(track_scope):
    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=5)
    with SessionLocal() as db:
        scope = _scope(db, deadline=deadline)
        track_scope(scope)
        _user, project, conversation, _connector, reference, execution, run, _job, claim = scope
        run.execution_deadline_at = deadline
        db.commit()

        assert park_connector_wait(
            db,
            claim=claim,
            run_id=run.id,
            conversation_id=conversation.id,
            project_id=project.id,
            execution=execution,
            failure=ConnectorReadinessFailure(reference, "connector_unavailable"),
            now=now,
        )

        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()
        assert blocker.expires_at.replace(tzinfo=UTC) == deadline


def test_cancel_command_closes_a_waiting_connector_blocker(track_scope):
    with SessionLocal() as db:
        scope = _scope(db)
        track_scope(scope)
        user, _project, _conversation, _reference, _execution, run, job = _park(
            db, scope
        )
        blocker = db.query(ConnectorBlocker).filter_by(run_id=run.id).one()

        cancelled = request_cancel(db, user.id, str(run.id))

        assert cancelled.status == "cancelled"
        assert blocker.state == "cancelled"
        assert blocker.closed_at is not None
        assert job.owner_id is None


def test_worker_parks_before_invoking_a_tool_when_local_preflight_fails(
    monkeypatch, track_scope
):
    from src.openship.execution.models import ToolOperation
    from src.openship.runs import worker as worker_module
    from src.openship.runs.queue import claim_next
    from src.openship.runs.worker import Worker

    with SessionLocal() as db:
        scope = _scope(db, status="queued")
        track_scope(scope)
        _user, _project, _conversation, _connector, reference, execution, run, job, _claim = scope
        run.kind = "tool"
        run.status = "queued"
        execution = execution.model_copy(update={"graph_kind": "tool"})
        run.execution_configuration = execution.model_dump_json()
        run.fence = 0
        job.fence = 0
        job.owner_id = None
        job.lease_until = None
        db.commit()
        claim = claim_next(db, worker_id="blocker-worker", lease_seconds=30)
        assert claim is not None and claim.run_id == run.id

    monkeypatch.setattr(
        worker_module,
        "preflight_connector_references",
        lambda **_kwargs: ConnectorReadinessFailure(
            reference, "credential_store_unavailable"
        ),
    )
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=SessionLocal,
        checkpointer=None,
    )
    try:
        assert worker.process_job(claim)
    finally:
        worker.engine.dispose()

    with SessionLocal() as db:
        stored_run = db.get(Run, run.id)
        stored_job = db.query(RunJob).filter_by(run_id=run.id).one()
        assert stored_run.status == "waiting_connector"
        assert stored_run.provider_started_at is None
        assert stored_job.owner_id is None
        assert stored_job.lease_until is None
        assert db.query(ToolOperation).filter(ToolOperation.run_id == run.id).count() == 0
        assert db.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).count() == 0
