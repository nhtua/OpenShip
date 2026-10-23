"""Synthetic Botocore proof for AWS storage reads and atomic publication."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from httpx import AsyncByteStream
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityManifestV2,
    CapabilityRef,
    ConnectorRef,
    ExecutionConfigV2,
    ResourceBindingRef,
    ToolCommandRequest,
    ToolInvocationContext,
    canonical_digest,
)
from src.openship.capabilities.definitions import (
    ToolDefinitionEntry,
    get_tool_definition,
    implementation_digest_for,
    register_tool_definition,
    unregister_tool_definition,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.aws.broker import AwsRoleBroker
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.credentials.dependencies import get_credential_store
from src.openship.connectors.credentials.store import CredentialContext
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.database.models import Base
from src.openship.events.service import Event
from src.openship.execution.aws_transport import AwsRequestBudget
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.providers.control import InvocationControl
from src.openship.resources.models import (
    Environment,
    ResourceBinding,
    ResourceObject,
    ResourceObservation,
)
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import Claim, reconcile_expired
from src.openship.runs.service import submit_tool_run
from src.openship.tools.aws.checks import validate_caller_identity
from src.openship.tools.aws.contracts import (
    AwsCheckArguments,
    AwsCheckResult,
)
from src.openship.tools.aws.s3_storage import STORAGE_TYPES_V1
from src.openship.workspace.models import Project

ACCOUNT_ID = "123456789012"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/ReadOnly"
BUCKET = "synthetic-phase5-storage-bucket"
BOOTSTRAP_ACCESS = "synthetic-bootstrap-access-key"  # rayloc:ignore
BOOTSTRAP_SECRET = "synthetic-bootstrap-secret-key"  # rayloc:ignore
ROLE_ACCESS = "synthetic-role-access-key"  # rayloc:ignore
ROLE_SECRET = "synthetic-role-secret-key"  # rayloc:ignore
ROLE_TOKEN = "synthetic-role-session-token"  # rayloc:ignore


class _BodyStream(AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        return None


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]
)
def database_sessions(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'cloud-observation.db'}"
    else:
        url = os.environ.get("TEST_POSTGRES_URL")
        if not url:
            pytest.skip(
                "no TEST_POSTGRES_URL; cloud observation PostgreSQL proof is opt-in"
            )
        api_root = Path(__file__).parents[2]
        ini = tmp_path / "cloud-observation-alembic.ini"
        ini.write_text(
            f"[alembic]\nscript_location = {api_root / 'alembic'}\nsqlalchemy.url = {url.replace('%', '%%')}\n",
            encoding="utf-8",
        )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
            cwd=api_root,
            env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(url, pool_size=6, max_overflow=4)
    if engine.dialect.name == "sqlite":
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()


def _configure_test_keys(monkeypatch, tmp_path):
    encryption_file = tmp_path / "cloud-observation-encryption.json"
    hmac_file = tmp_path / "cloud-observation-hmac.json"
    for path, key in ((encryption_file, b"e" * 32), (hmac_file, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "cloud-observation-test-v1",
                    "keys": {
                        "cloud-observation-test-v1": base64.b64encode(key).decode(
                            "ascii"
                        )
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_file)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_file)
    return encryption_file, hmac_file


def _insert_aws_scope(db):
    user = User(
        username=f"cloud-observation-{uuid.uuid4().hex[:12]}",
        email=f"cloud-observation-{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Cloud observation synthetic test")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        project_id=project.id,
        title="Cloud observation synthetic test",
    )
    db.add(conversation)
    db.flush()

    connector = Connector(
        id=uuid.uuid4(),
        kind="aws",
        visibility="project",
        project_id=project.id,
        name="Synthetic S3 reader",
        current_revision=1,
        enabled=True,
        managed_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    db.add(connector)
    db.flush()
    credential_id = uuid.uuid4()
    credential_context = CredentialContext(
        installation_id=settings.connector_installation_id,
        visibility="project",
        project_id=project.id,
        connector_id=connector.id,
        credential_version_id=credential_id,
        schema_version=1,
        kind="aws_bootstrap",
    )
    envelope = get_credential_store().encrypt(
        credential_context,
        {
            "access_key_id": BOOTSTRAP_ACCESS,
            "secret_access_key": BOOTSTRAP_SECRET,
            "session_token": None,
            "expires_at": None,
        },
    )
    credential = CredentialVersion(
        id=credential_id,
        connector_id=connector.id,
        visibility="project",
        project_id=project.id,
        kind="aws_bootstrap",
        schema_version=1,
        key_id=envelope.key_id,
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext,
        state="active",
    )
    configuration = ConnectorConfiguration(
        kind="aws",
        region="us-east-1",
        expected_account_id=ACCOUNT_ID,
        basic_role_arn=ROLE_ARN,
    )
    revision = ConnectorRevision(
        id=uuid.uuid4(),
        connector_id=connector.id,
        revision=1,
        schema_version=1,
        visibility="project",
        project_id=project.id,
        configuration_json=configuration.canonical_bytes().decode("utf-8"),
        configuration_digest=configuration.digest(),
        credential_version_id=credential.id,
        created_by_user_id=user.id,
    )
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
        uri=f"openship://{project.id}/resource/synthetic-bucket",
        display_name="Synthetic storage bucket",
        created_by_user_id=user.id,
    )
    binding = ResourceBinding(
        id=uuid.uuid4(),
        project_id=project.id,
        resource_object_id=resource.id,
        environment_id=environment.id,
        connector_id=connector.id,
        connector_revision=1,
        revision=1,
        account_id=ACCOUNT_ID,
        region="us-east-1",
        bucket_name=BUCKET,
        bucket_arn=f"arn:aws:s3:::{BUCKET}",
        expected_owner=ACCOUNT_ID,
        management_method="external",
        state="active",
        created_by_user_id=user.id,
    )
    db.add(credential)
    db.flush()
    db.add(revision)
    db.flush()
    db.add_all([environment, resource])
    db.flush()
    db.add(binding)
    db.flush()
    return user, project, conversation, connector, credential, binding


def _running_aws_tool(
    db, *, user, project, conversation, connector, credential, binding
):
    storage_manifest, check_manifest = (
        next(item for item in list_capability_manifests() if item.id == tool_id)
        for tool_id in ("aws_s3_bucket_storage", "aws_check_connection")
    )
    storage_ref = CapabilityRef(
        id=storage_manifest.id,
        version=storage_manifest.version,
        manifest_digest=storage_manifest.manifest_digest,
    )
    check_ref = CapabilityRef(
        id=check_manifest.id,
        version=check_manifest.version,
        manifest_digest=check_manifest.manifest_digest,
    )
    connector_ref = ConnectorRef(
        id=connector.id,
        kind="aws",
        configuration_revision=1,
        visibility="project",
        credential_version_id=credential.id,
    )
    binding_ref = ResourceBindingRef(
        id=binding.id,
        revision=1,
        connector_id=connector.id,
        connector_revision=1,
    )
    execution = ExecutionConfigV2(
        graph_kind="tool",
        graph_version=2,
        allowed_capabilities=(storage_ref, check_ref),
        connector_refs=(connector_ref,),
        resource_binding_refs=(binding_ref,),
        selected_resource_binding_refs=(binding_ref,),
    )
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        client_request_id=str(uuid.uuid4()),
        status="running",
        kind="tool",
        graph_version=2,
        fence=1,
        execution_configuration=execution.model_dump_json(),
    )
    db.add(run)
    db.flush()
    job = RunJob(
        run_id=run.id,
        fence=1,
        owner_id="cloud-observation-worker",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=1,
        lease_until=job.lease_until,
        worker_id=job.owner_id,
        user_id=user.id,
    )
    store = None
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(run.id),
        ),
        execution=execution,
        credential_resolver=lambda _name: None,
        session_factory=lambda: db.__class__(
            bind=db.get_bind(), expire_on_commit=False
        ),
    )
    return run, claim, context, store, storage_ref, check_ref, connector_ref


def _synthetic_transport(requests, *, on_request=None):
    session_state = {"name": None}

    async def respond(request):
        requests.append(request)
        if on_request is not None:
            on_request(request)
        if request.url.host.startswith("sts."):
            parameters = parse_qs(request.content.decode("ascii"))
            action = parameters.get("Action", [None])[0]
            if action == "AssumeRole":
                session_name = parameters["RoleSessionName"][0]
                session_state["name"] = session_name
                expiration = (datetime.now(UTC) + timedelta(seconds=900)).isoformat()
                body = f"""<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
                  <AssumeRoleResult>
                    <Credentials>
                      <AccessKeyId>{ROLE_ACCESS}</AccessKeyId>
                      <SecretAccessKey>{ROLE_SECRET}</SecretAccessKey>
                      <SessionToken>{ROLE_TOKEN}</SessionToken>
                      <Expiration>{expiration}</Expiration>
                    </Credentials>
                    <AssumedRoleUser>
                      <AssumedRoleId>synthetic-role-id:{session_name}</AssumedRoleId>
                      <Arn>arn:aws:sts::{ACCOUNT_ID}:assumed-role/ReadOnly/{session_name}</Arn>
                    </AssumedRoleUser>
                  </AssumeRoleResult>
                  <ResponseMetadata><RequestId>synthetic-request</RequestId></ResponseMetadata>
                </AssumeRoleResponse>""".encode()
                return httpx.Response(
                    200,
                    headers={"content-type": "text/xml"},
                    stream=_BodyStream(body),
                )
            assert action == "GetCallerIdentity"
            session_name = session_state["name"] or "openship-synthetic"
            body = f"""<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult><UserId>synthetic-role-id:{session_name}</UserId><Account>{ACCOUNT_ID}</Account><Arn>arn:aws:sts::{ACCOUNT_ID}:assumed-role/ReadOnly/{session_name}</Arn></GetCallerIdentityResult><ResponseMetadata><RequestId>synthetic-request</RequestId></ResponseMetadata></GetCallerIdentityResponse>""".encode()
            return httpx.Response(
                200,
                headers={"content-type": "text/xml"},
                stream=_BodyStream(body),
            )
        if request.url.host.startswith("s3."):
            assert request.method == "GET"
            assert "location" in request.url.query.decode()
            assert request.headers["x-amz-expected-bucket-owner"] == ACCOUNT_ID
            body = b'<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">us-east-1</LocationConstraint>'
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                stream=_BodyStream(body),
            )
        assert request.url.host.startswith("monitoring.")
        payload = json.loads(request.content)
        assert request.headers["x-amz-target"].endswith("GetMetricData")
        assert len(payload["MetricDataQueries"]) == 22
        assert payload["MaxDatapoints"] == 1024
        if "NextToken" in payload:
            assert payload["NextToken"] == "synthetic-page-2"
            page_queries = payload["MetricDataQueries"][11:]
            next_token = None
        else:
            page_queries = payload["MetricDataQueries"][:11]
            next_token = "synthetic-page-2"
        measurement = (
            datetime.now(UTC).date() - timedelta(days=1)
        ).isoformat() + "T00:00:00Z"
        results = [
            {
                "Id": query["Id"],
                "Label": "BucketSizeBytes",
                "Timestamps": [measurement],
                "Values": [float(int(query["Id"][1:]) + 1)],
                "StatusCode": "Complete",
                "Messages": [],
            }
            for query in page_queries
        ]
        metric_response = {"MetricDataResults": results}
        if next_token:
            metric_response["NextToken"] = next_token
        return httpx.Response(
            200,
            headers={
                "content-type": "application/x-amz-json-1.1",
                "x-amzn-requestid": "synthetic-request",
            },
            stream=_BodyStream(json.dumps(metric_response).encode()),
        )

    return lambda _endpoint: httpx.MockTransport(respond)


@pytest.fixture
def sqlite_worker_sessions(tmp_path):
    """Use a file-backed database so a child worker can reopen durable state."""
    engine = create_engine(f"sqlite:///{tmp_path / 'aws-worker-restart.db'}")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("crash_at", "expected_exit"),
    [
        ("before_intent", 71),
        ("after_read", 72),
        ("before_publication", 73),
    ],
)
@pytest.mark.asyncio
async def test_aws_worker_restart_never_replays_a_dispatched_request(
    crash_at, expected_exit, sqlite_worker_sessions, monkeypatch, tmp_path
):
    encryption_file, hmac_file = _configure_test_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "aws_check_connection,aws_s3_bucket_storage"
    )
    with sqlite_worker_sessions() as db:
        database_url = db.get_bind().url.render_as_string(hide_password=False)
    storage_id = uuid.uuid4()
    artifact_root = tmp_path / "worker-restart-artifacts"
    LocalArtifactStore(artifact_root, storage_id).initialize()
    monkeypatch.setattr(settings, "database_url", database_url)
    monkeypatch.setattr(settings, "artifact_root", artifact_root)
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)

    with sqlite_worker_sessions() as db:
        user, _project, conversation, _connector, _credential, binding = (
            _insert_aws_scope(db)
        )
        user_id = user.id
        manifest = next(
            item
            for item in list_capability_manifests()
            if item.id == "aws_s3_bucket_storage"
        )
        run = submit_tool_run(
            db,
            user.id,
            conversation.id,
            ToolCommandRequest(
                client_request_id=uuid.uuid4(),
                capability=CapabilityRef(
                    id=manifest.id,
                    version=manifest.version,
                    manifest_digest=manifest.manifest_digest,
                ),
                arguments={"resource_binding_id": str(binding.id)},
            ),
        )
        run_id = run.id
        db.commit()

    api_root = Path(__file__).parents[2]
    request_log = tmp_path / f"aws-requests-{crash_at}.jsonl"
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(api_root),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url,
        "TOOL_ENABLED_IDS": "aws_check_connection,aws_s3_bucket_storage",
        "CONNECTOR_INSTALLATION_ID": str(settings.connector_installation_id),
        "CONNECTOR_ENCRYPTION_KEY_FILE": str(encryption_file),
        "CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE": str(hmac_file),
        "ARTIFACT_ROOT": str(artifact_root),
        "ARTIFACT_STORAGE_ID": str(storage_id),
        "WORKER_CANCELLATION_POLL_SECONDS": "0.05",
        "WORKER_HEARTBEAT_INTERVAL": "2",
        "WORKER_LEASE_SECONDS": "30",
    }
    worker_script = api_root / "tests/full_stack/synthetic_aws_worker.py"

    def start_worker(crash_point):
        worker_env = {
            **base_env,
            "AWS_SYNTHETIC_REQUEST_LOG": str(request_log),
            "AWS_SYNTHETIC_CRASH_AT": crash_point or "",
        }
        return subprocess.run(
            [sys.executable, str(worker_script)],
            cwd=tmp_path,
            env=worker_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    crashed = start_worker(crash_at)
    assert crashed.returncode == expected_exit, crashed.stderr[-2000:]
    request_rows = (
        [json.loads(line) for line in request_log.read_text().splitlines()]
        if request_log.exists()
        else []
    )

    from src.openship.runs.routes import get_run_status

    with sqlite_worker_sessions() as db:
        api_user = db.query(User).filter_by(id=user_id).one()
        status = await get_run_status(str(run_id), db, api_user)
    assert status.status == "running"
    expected_operation_status = (
        "admitted" if crash_at == "before_intent" else "running"
    )
    assert status.operations[0].status == expected_operation_status

    with sqlite_worker_sessions() as db:
        operation = db.query(ToolOperation).filter_by(run_id=run_id).one()
        job = db.query(RunJob).filter_by(run_id=run_id).one()
        job.lease_until = datetime(2000, 1, 1, tzinfo=UTC)
        db.commit()
        assert reconcile_expired(db) == 1
        db.refresh(operation)
        run = db.query(Run).filter_by(id=run_id).one()
        if crash_at == "before_intent":
            assert request_rows == []
            assert run.status == "queued"
            assert operation.status == "admitted"
            assert db.query(ToolAttempt).filter_by(operation_id=operation.id).count() == 0
        else:
            assert len(request_rows) == 6
            assert run.status == "failed"
            assert run.error_code == "execution_interrupted"
            assert operation.status == "interrupted"
            attempt = db.query(ToolAttempt).filter_by(operation_id=operation.id).one()
            assert attempt.outcome == "interrupted"
            assert attempt.error_code == "execution_interrupted"
            artifact = db.query(Artifact).filter_by(
                originating_operation_id=operation.id
            ).one()
            assert artifact.status == "deleted"

    restarted = start_worker(None)
    assert restarted.returncode == 0, restarted.stderr[-2000:]
    request_rows_after_restart = [
        json.loads(line) for line in request_log.read_text().splitlines()
    ] if request_log.exists() else []
    if crash_at == "before_intent":
        assert len(request_rows_after_restart) == 6
        with sqlite_worker_sessions() as db:
            run = db.query(Run).filter_by(id=run_id).one()
            operation = db.query(ToolOperation).filter_by(run_id=run_id).one()
            attempt = db.query(ToolAttempt).filter_by(operation_id=operation.id).one()
            assert run.status == "completed"
            assert operation.status == "succeeded"
            assert attempt.outcome == "succeeded"
    else:
        assert len(request_rows_after_restart) == 6


@pytest.mark.asyncio
async def test_synthetic_botocore_storage_observation_publishes_atomically(
    database_sessions, monkeypatch, tmp_path
):
    _configure_test_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "aws_check_connection,aws_s3_bucket_storage"
    )
    requests = []
    transport_factory = _synthetic_transport(requests)
    with database_sessions() as db:
        user, project, conversation, connector, credential, binding = _insert_aws_scope(
            db
        )
        run, claim, context, _store, storage_ref, check_ref, connector_ref = (
            _running_aws_tool(
                db,
                user=user,
                project=project,
                conversation=conversation,
                connector=connector,
                credential=credential,
                binding=binding,
            )
        )
        context.session_factory = database_sessions
        context.artifact_store = LocalArtifactStore(
            tmp_path / "cloud-observation-artifacts", uuid.uuid4()
        )
        context.artifact_store.initialize()
        run_id = run.id
        conversation_id = conversation.id
        operation_session = database_sessions

    import src.openship.execution.gateway as gateway_module

    monkeypatch.setattr(
        gateway_module,
        "AwsRoleBroker",
        lambda factory: AwsRoleBroker(factory, transport_factory=transport_factory),
    )
    storage_manifest = next(
        item
        for item in list_capability_manifests()
        if item.id == "aws_s3_bucket_storage"
    )
    storage_result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:storage",
        ref=storage_ref,
        arguments={"resource_binding_id": str(binding.id)},
        control=InvocationControl(),
    )

    assert storage_result.status == "succeeded"
    assert storage_result.result["outcome"] == "measured"
    assert storage_result.result["reported_storage_subtotal_bytes"] == 253.0
    assert storage_result.result["coverage"]["reported_types"] == list(STORAGE_TYPES_V1)
    assert storage_result.result["artifact"]["id"] == str(
        storage_result.artifacts[0].id
    )
    assert len(requests) == 6  # role assumption, two identities, location and two pages

    with operation_session() as db:
        operation = db.query(ToolOperation).filter_by(run_id=run_id).one()
        attempt = db.query(ToolAttempt).filter_by(operation_id=operation.id).one()
        artifact = db.query(Artifact).filter_by(id=storage_result.artifacts[0].id).one()
        observation = (
            db.query(ResourceObservation).filter_by(operation_id=operation.id).one()
        )
        events = [
            item.type
            for item in db.query(Event)
            .filter(Event.run_id == run_id)
            .order_by(Event.sequence)
            .all()
        ]
        outbox_types = [
            item.type
            for item in db.query(Outbox)
            .filter(Outbox.conversation_id == conversation_id)
            .all()
        ]
        assert operation.status == "succeeded"
        assert attempt.outcome == "succeeded"
        assert artifact.kind == "cloud_observation"
        assert artifact.display_name == "s3-bucket-storage.json"
        assert observation.outcome == "measured"
        stored_coverage = json.loads(observation.coverage_json)
        assert stored_coverage["stale"] is False
        assert stored_coverage["reported_storage_subtotal_bytes"] == 253.0
        assert observation.evidence_artifact_id == artifact.id
        assert observation.evidence_sha256 == artifact.sha256
        assert "resource.observed" in events
        assert "resource.observed" in outbox_types
        store = context.artifact_store
        with store.open_verified(artifact) as evidence_file:
            evidence = json.load(evidence_file)
        assert (
            evidence["metrics"]["storage_types"][0]["storage_type"]
            == STORAGE_TYPES_V1[0]
        )
        assert len(evidence["metrics"]["storage_types"]) == 22

    check_manifest = next(
        item
        for item in list_capability_manifests()
        if item.id == "aws_check_connection"
    )
    check_definition = get_tool_definition(check_manifest)
    assert check_definition.model_selectable is False
    check_result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:check",
        ref=check_ref,
        arguments={
            "connector_id": str(connector_ref.id),
            "resource_binding_id": str(binding.id),
        },
        control=InvocationControl(),
    )
    assert check_result.status == "succeeded"
    assert check_result.result["bucket_verified"] is True
    assert check_result.result["account_id"] == ACCOUNT_ID
    assert not check_result.artifacts

    assert len(STORAGE_TYPES_V1) == 22
    assert storage_manifest.outputs[0].name == "s3-bucket-storage.json"


@pytest.mark.asyncio
async def test_test_only_handler_reuses_authenticated_aws_context(
    database_sessions, monkeypatch, tmp_path
):
    _configure_test_keys(monkeypatch, tmp_path)
    requests = []
    transport_factory = _synthetic_transport(requests)
    with database_sessions() as db:
        user, project, conversation, connector, credential, _binding = (
            _insert_aws_scope(db)
        )
        db.commit()
        project_id = project.id
        run_id = uuid.uuid4()
        execution_ref = ConnectorRef(
            id=connector.id,
            kind="aws",
            configuration_revision=1,
            visibility="project",
            credential_version_id=credential.id,
        )
        principal_id = user.id
        conversation_id = conversation.id

    description = "Test-only AWS identity handler for shared runner qualification."
    implementation_files = (
        "tests/integration/test_cloud_observation_publication_postgres.py",
    )
    implementation_digest = implementation_digest_for(implementation_files)
    fields = {
        "schema_version": 2,
        "id": "test_aws_identity_extension",
        "version": "1.0.0",
        "implementation_digest": implementation_digest,
        "description": description,
        "input_schema": AwsCheckArguments.model_json_schema(),
        "output_schema": AwsCheckResult.model_json_schema(),
        "effect": "read_only_external",
        "enablement_requirements": ("operator_enabled", "connector_configured"),
        "limits": {"timeout_seconds": 30, "max_model_result_bytes": 16 * 1024},
        "outputs": (),
        "connector_kinds": ("aws",),
        "operations": ("get_caller_identity",),
        "target_kinds": (),
        "execution_kind": "maintained",
    }
    manifest = CapabilityManifestV2(
        **fields,
        manifest_digest=canonical_digest(fields),
    )

    async def second_handler(arguments, context, control):
        pinned = next(
            item
            for item in context.execution.connector_refs
            if item.id == arguments.connector_id
        )
        access = await context.aws_access_factory(pinned)
        identity = await access.run(access.client("sts").get_caller_identity)
        valid = validate_caller_identity(
            identity,
            expected_account_id=access.configuration.expected_account_id,
            expected_role_arn=access.configuration.basic_role_arn,
        )
        control.raise_if_stopped()
        return AwsCheckResult(
            operation_id=str(context.operation_id),
            status="ready",
            account_id=valid.account_id,
            role_arn=valid.role_arn,
            region=access.configuration.region,
            checked_at=datetime.now(UTC),
        )

    entry = ToolDefinitionEntry(
        manifest=manifest,
        description=description,
        argument_model=AwsCheckArguments,
        result_model=AwsCheckResult,
        handler=second_handler,
        implementation_files=implementation_files,
        model_selectable=False,
    )
    register_tool_definition(entry)
    try:
        broker = AwsRoleBroker(
            database_sessions,
            transport_factory=transport_factory,
        )
        control = InvocationControl()
        context = ToolInvocationContext(
            authorization=AuthorizationContext(
                principal_id=principal_id,
                project_id=project_id,
                conversation_id=conversation_id,
                action="execute_tool",
                target_id=str(run_id),
            ),
            execution=ExecutionConfigV2(
                graph_kind="tool",
                graph_version=2,
                allowed_capabilities=(
                    CapabilityRef(
                        id=manifest.id,
                        version=manifest.version,
                        manifest_digest=manifest.manifest_digest,
                    ),
                ),
                connector_refs=(execution_ref,),
            ),
            credential_resolver=lambda _name: None,
            session_factory=database_sessions,
            operation_id=run_id,
        )
        budget = AwsRequestBudget()

        async def acquire(reference):
            access = await broker.acquire(
                project_id=project_id,
                reference=reference,
                run_id=run_id,
                control=control,
                request_budget=budget,
            )
            context._aws_accesses.append(access)
            return access

        context.aws_access_factory = acquire
        registered = get_tool_definition(manifest)
        result = await registered.handler(
            AwsCheckArguments(connector_id=connector.id), context, control
        )
        assert result.account_id == ACCOUNT_ID
        assert len(requests) == 3  # AssumeRole and broker/tool identity checks
        assert registered.model_selectable is False
    finally:
        await context.close_runtime_accesses()
        unregister_tool_definition(manifest.id)
