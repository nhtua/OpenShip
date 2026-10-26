"""Synthetic Botocore proof for AWS storage reads and atomic publication."""

from __future__ import annotations

import base64
import configparser
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
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
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
    ExecutionConfigV4,
    ToolCommandRequest,
    ToolInvocationContext,
    canonical_digest,
    decode_execution_config,
)
from src.openship.capabilities.definitions import (
    TOOL_HANDLER_OVERRIDES,
    ToolDefinitionEntry,
    get_tool_definition,
    implementation_digest_for,
    register_tool_definition,
    unregister_tool_definition,
)
from src.openship.capabilities.models import ToolPolicyOverride, ToolPolicyState
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
from src.openship.messaging.models import MessageTransportState
from src.openship.providers.control import InvocationControl
from src.openship.resources.models import (
    Environment,
    ResourceBinding,
    ResourceObject,
    ResourceObservation,
)
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import Claim, claim_next, reconcile_expired
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
    engine = None
    postgres_admin = None
    postgres_schema = None
    try:
        if request.param == "sqlite":
            url = f"sqlite:///{tmp_path / 'cloud-observation.db'}"
        else:
            raw_url = os.environ.get("TEST_POSTGRES_URL")
            if not raw_url:
                pytest.skip(
                    "no TEST_POSTGRES_URL; cloud observation PostgreSQL proof is opt-in"
                )
            base_url = make_url(raw_url)
            postgres_schema = f"cloud_observation_{uuid.uuid4().hex}"
            postgres_admin = create_engine(base_url, pool_pre_ping=True)
            with postgres_admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{postgres_schema}"'))
            query = dict(base_url.query)
            query["options"] = f"-csearch_path={postgres_schema}"
            url = base_url.set(query=query).render_as_string(hide_password=False)

            api_root = Path(__file__).parents[2]
            ini = tmp_path / "cloud-observation-alembic.ini"
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(api_root / "alembic.ini")
            parser.set("alembic", "script_location", str(api_root / "alembic"))
            parser.set("alembic", "sqlalchemy.url", url.replace("%", "%%"))
            with ini.open("w", encoding="utf-8") as config_file:
                parser.write(config_file)
            upgraded = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
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
        if engine.dialect.name == "sqlite":
            with sessions() as db:
                db.add_all(
                    [
                        ToolPolicyState(id=1, revision=1),
                        MessageTransportState(
                            id=1,
                            installation_id=uuid.uuid4(),
                            transport_epoch=uuid.uuid4(),
                            active=False,
                        ),
                    ]
                )
                db.commit()
        yield sessions
    finally:
        if engine is not None:
            engine.dispose()
        if postgres_admin is not None and postgres_schema is not None:
            with postgres_admin.begin() as connection:
                connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{postgres_schema}" CASCADE')
                )
            postgres_admin.dispose()


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


def _insert_aws_scope(db, *, include_legacy_binding=True):
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
    db.add(credential)
    db.flush()
    db.add(revision)
    db.flush()
    binding = None
    if include_legacy_binding:
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
    execution = ExecutionConfigV2(
        graph_kind="tool",
        graph_version=2,
        allowed_capabilities=(storage_ref, check_ref),
        connector_refs=(connector_ref,),
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
            if "x-amz-expected-bucket-owner" in request.headers:
                assert request.headers["x-amz-expected-bucket-owner"] == ACCOUNT_ID
            query = request.url.query.decode()
            if query in {"", "max-buckets=20"} and request.url.path in {"", "/"}:
                body = f"""<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Owner><ID>{ACCOUNT_ID}</ID><DisplayName>synthetic</DisplayName></Owner><Buckets><Bucket><Name>{BUCKET}</Name><CreationDate>2025-01-01T00:00:00.000Z</CreationDate></Bucket></Buckets></ListAllMyBucketsResult>""".encode()
            elif "location" in query:
                body = b'<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">us-east-1</LocationConstraint>'
            elif "versioning" in query:
                body = b'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>Enabled</Status><MfaDelete>Disabled</MfaDelete></VersioningConfiguration>'
            elif "policyStatus" in query:
                body = b'<PolicyStatus xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsPublic>true</IsPublic></PolicyStatus>'
            elif "publicAccessBlock" in query:
                body = b'<PublicAccessBlockConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><BlockPublicAcls>true</BlockPublicAcls><IgnorePublicAcls>true</IgnorePublicAcls><BlockPublicPolicy>true</BlockPublicPolicy><RestrictPublicBuckets>true</RestrictPublicBuckets></PublicAccessBlockConfiguration>'
            elif "acl" in query:
                body = f'<AccessControlPolicy xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Owner><ID>{ACCOUNT_ID}</ID><DisplayName>synthetic</DisplayName></Owner><AccessControlList><Grant><Grantee xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="CanonicalUser"><ID>{ACCOUNT_ID}</ID><DisplayName>synthetic</DisplayName></Grantee><Permission>FULL_CONTROL</Permission></Grant></AccessControlList></AccessControlPolicy>'.encode()
            else:
                raise AssertionError(f"unexpected S3 request path: {request.url.path}?{query}")
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                stream=_BodyStream(body),
            )
        assert request.url.host.startswith(("monitoring.", "cloudwatch."))
        payload = json.loads(request.content)
        assert request.headers["x-amz-target"].endswith("GetMetricData")
        metric_name = payload["MetricDataQueries"][0]["MetricStat"]["Metric"][
            "MetricName"
        ]
        if metric_name == "NumberOfObjects":
            query = payload["MetricDataQueries"][0]
            assert query["MetricStat"]["Metric"]["Namespace"] == "AWS/S3"
            assert query["MetricStat"]["Metric"]["Dimensions"] == [
                {"Name": "BucketName", "Value": BUCKET},
                {"Name": "StorageType", "Value": "AllStorageTypes"},
            ]
            assert query["MetricStat"]["Period"] == 86400
            assert query["MetricStat"]["Stat"] == "Average"
            measurement = (
                datetime.now(UTC).date() - timedelta(days=1)
            ).isoformat() + "T00:00:00Z"
            metric_response = {
                "MetricDataResults": [
                    {
                        "Id": query["Id"],
                        "Label": "NumberOfObjects",
                        "Timestamps": [measurement],
                        "Values": [42.0],
                        "StatusCode": "Complete",
                        "Messages": [],
                    }
                ]
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/x-amz-json-1.1",
                    "x-amzn-requestid": "synthetic-request",
                },
                stream=_BodyStream(json.dumps(metric_response).encode()),
            )
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
    with sessions() as db:
        db.add_all(
            [
                ToolPolicyState(id=1, revision=1),
                MessageTransportState(
                    id=1,
                    installation_id=uuid.uuid4(),
                    transport_epoch=uuid.uuid4(),
                    active=False,
                ),
            ]
        )
        db.commit()
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
            _insert_aws_scope(db, include_legacy_binding=False)
        )
        assert binding is None
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
                arguments={
                    "connector_id": str(_connector.id),
                    "bucket_name": BUCKET,
                    "expected_owner": ACCOUNT_ID,
                },
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
            db, include_legacy_binding=False
        )
        assert binding is None
        assert db.query(Environment).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceObject).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceBinding).filter_by(project_id=project.id).count() == 0
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
        arguments={
            "connector_id": str(connector_ref.id),
            "bucket_name": BUCKET,
            "expected_owner": ACCOUNT_ID,
        },
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
        assert observation.schema_version == 2
        assert observation.resource_object_id is None
        assert observation.resource_binding_id is None
        assert observation.connector_id == connector.id
        assert json.loads(observation.target_json) == {
            "bucket_name": BUCKET,
            "expected_owner": ACCOUNT_ID,
        }
        assert "cloud.observed" in events
        assert "cloud.observed" in outbox_types
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
        },
        control=InvocationControl(),
    )
    assert check_result.status == "succeeded"
    assert check_result.result["account_id"] == ACCOUNT_ID
    assert not check_result.artifacts

    assert len(STORAGE_TYPES_V1) == 22
    assert storage_manifest.outputs[0].name == "s3-bucket-storage.json"


@pytest.mark.asyncio
async def test_catalog_then_binding_free_aws_read_publishes_and_reopens_evidence(
    database_sessions, monkeypatch, tmp_path
):
    _configure_test_keys(monkeypatch, tmp_path)
    requests = []
    transport_factory = _synthetic_transport(requests)
    with database_sessions() as db:
        user, project, conversation, connector, credential, binding = _insert_aws_scope(
            db, include_legacy_binding=False
        )
        assert binding is None
        assert db.query(Environment).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceObject).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceBinding).filter_by(project_id=project.id).count() == 0
        db.merge(ToolPolicyOverride(capability_id="aws_read_catalog", enabled=True))
        db.merge(ToolPolicyOverride(capability_id="aws_read", enabled=True))
        catalog_manifest, read_manifest = (
            next(item for item in list_capability_manifests() if item.id == tool_id)
            for tool_id in ("aws_read_catalog", "aws_read")
        )
        catalog_ref = CapabilityRef(
            id=catalog_manifest.id,
            version=catalog_manifest.version,
            manifest_digest=catalog_manifest.manifest_digest,
        )
        read_ref = CapabilityRef(
            id=read_manifest.id,
            version=read_manifest.version,
            manifest_digest=read_manifest.manifest_digest,
        )
        connector_ref = ConnectorRef(
            id=connector.id,
            kind="aws",
            configuration_revision=1,
            visibility="project",
            credential_version_id=credential.id,
        )
        execution = ExecutionConfigV4(
            graph_kind="tool",
            graph_version=3,
            allowed_capabilities=(catalog_ref, read_ref),
            connector_refs=(connector_ref,),
            aws_read_catalog_digest=catalog_manifest.aws_read_catalog_digest,
        )
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            client_request_id=str(uuid.uuid4()),
            status="running",
            kind="tool",
            graph_version=3,
            fence=1,
            execution_configuration=execution.model_dump_json(),
        )
        db.add(run)
        db.flush()
        lease_until = datetime.now(UTC) + timedelta(minutes=1)
        db.add(
            RunJob(
                run_id=run.id,
                fence=1,
                owner_id="aws-read-publication-worker",
                lease_until=lease_until,
            )
        )
        db.commit()
        claim = Claim(
            run_id=run.id,
            fence=1,
            lease_until=lease_until,
            worker_id="aws-read-publication-worker",
            user_id=user.id,
        )
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
            session_factory=database_sessions,
        )
        artifact_root = tmp_path / "aws-read-evidence"
        storage_id = uuid.uuid4()
        store = LocalArtifactStore(artifact_root, storage_id)
        store.initialize()
        context.artifact_store = store
        operation_session = database_sessions
        run_id = run.id

    import src.openship.execution.gateway as gateway_module

    monkeypatch.setattr(
        gateway_module,
        "AwsRoleBroker",
        lambda factory: AwsRoleBroker(factory, transport_factory=transport_factory),
    )
    catalog_result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:0:catalog",
        ref=catalog_ref,
        arguments={"service": "s3", "operation": "list_buckets"},
        control=InvocationControl(),
    )
    assert catalog_result.status == "succeeded"
    assert catalog_result.result["operations"][0]["operation"] == "list_buckets"

    # The model can use the bucket name returned by its discovery call as the
    # target of a separate read without a resource-object or binding row.
    read_result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:1:read",
        ref=read_ref,
        arguments={
            "service": "s3",
            "operation": "list_buckets",
            "parameters": {},
        },
        control=InvocationControl(),
    )
    assert read_result.status == "succeeded"
    assert read_result.result["preview"]["Buckets"][0]["Name"] == BUCKET
    assert len(read_result.artifacts) == 1
    bucket_name = read_result.result["preview"]["Buckets"][0]["Name"]
    target_result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:2:target-read",
        ref=read_ref,
        arguments={
            "service": "s3",
            "operation": "get_bucket_location",
            "parameters": {"Bucket": bucket_name},
        },
        control=InvocationControl(),
    )
    assert target_result.status == "succeeded"
    assert target_result.result["preview"]["LocationConstraint"] == "us-east-1"
    assert len(target_result.artifacts) == 1

    report_results = {}
    for operation, parameters in (
        ("get_bucket_policy_status", {"Bucket": BUCKET}),
        ("get_public_access_block", {"Bucket": BUCKET}),
        ("get_bucket_acl", {"Bucket": BUCKET}),
        (
            "get_metric_data",
            {
                "MetricDataQueries": [
                    {
                        "Id": "m0",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/S3",
                                "MetricName": "NumberOfObjects",
                                "Dimensions": [
                                    {"Name": "BucketName", "Value": BUCKET},
                                    {
                                        "Name": "StorageType",
                                        "Value": "AllStorageTypes",
                                    },
                                ],
                            },
                            "Period": 86400,
                            "Stat": "Average",
                        },
                        "ReturnData": True,
                    }
                ],
                "StartTime": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
                "EndTime": datetime.now(UTC).isoformat(),
            },
        ),
    ):
        result = await ToolGateway(operation_session).execute_tool(
            claim=claim,
            context=context,
            logical_call_id=f"model:report:{operation}",
            ref=read_ref,
            arguments={
                "service": "cloudwatch" if operation == "get_metric_data" else "s3",
                "operation": operation,
                "region": "us-east-1",
                "parameters": parameters,
            },
            control=InvocationControl(),
        )
        assert result.status == "succeeded", result.error
        report_results[operation] = result.result["preview"]
    assert report_results["get_bucket_policy_status"]["PolicyStatus"]["IsPublic"] is True
    assert report_results["get_public_access_block"][
        "PublicAccessBlockConfiguration"
    ]["BlockPublicAcls"] is True
    assert report_results["get_bucket_acl"]["Grants"][0][
        "Permission"
    ] == "FULL_CONTROL"
    assert report_results["get_metric_data"]["MetricDataResults"][0]["Values"] == [
        42.0
    ]
    assert len(requests) == 18  # Each read assumes, verifies, and uses the role.

    with operation_session() as db:
        operation = db.query(ToolOperation).filter_by(
            id=target_result.operation_id, run_id=run_id, capability_id="aws_read"
        ).one()
        artifact = db.query(Artifact).filter_by(id=target_result.artifacts[0].id).one()
        observation = db.query(ResourceObservation).filter_by(
            operation_id=target_result.operation_id
        ).one()
        list_observation = db.query(ResourceObservation).filter_by(
            operation_id=read_result.operation_id
        ).one()
        event_types = [
            item.type
            for item in db.query(Event).filter_by(run_id=run_id).order_by(Event.sequence)
        ]
        assert operation.status == "succeeded"
        assert artifact.kind == "cloud_observation"
        assert artifact.status == "ready"
        assert observation.schema_version == 2
        assert observation.resource_binding_id is None
        assert observation.resource_object_id is None
        assert observation.connector_id == connector.id
        assert observation.service == "s3"
        assert observation.operation == "get_bucket_location"
        assert json.loads(observation.target_json) == {
            "parameters": {"Bucket": BUCKET}
        }
        assert list_observation.resource_object_id is None
        assert list_observation.resource_binding_id is None
        assert "cloud.observed" in event_types
        with LocalArtifactStore(artifact_root, storage_id).open_verified(artifact) as stream:
            evidence = json.load(stream)
        assert evidence["data"]["LocationConstraint"] == "us-east-1"


@pytest.mark.parametrize(
    "publication_change",
    [
        None,
        "connector_revoked",
        "policy_disabled_before_dispatch",
        "policy_disabled_after_read",
        "cancelled_after_read",
        "lease_lost_after_read",
    ],
    ids=[
        "success",
        "revoked-connector",
        "disabled-before-dispatch",
        "disabled-after-read",
        "cancelled-after-read",
        "lease-lost-after-read",
    ],
)
@pytest.mark.asyncio
async def test_synthetic_botocore_metadata_observation_publishes_atomically(
    database_sessions, monkeypatch, tmp_path, publication_change
):
    _configure_test_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(
        settings,
        "tool_enabled_ids",
        "aws_check_connection,aws_s3_bucket_storage,aws_s3_bucket_metadata",
    )
    requests = []
    transport_factory = _synthetic_transport(requests)
    with database_sessions() as db:
        user, project, conversation, connector, _credential, binding = _insert_aws_scope(
            db, include_legacy_binding=False
        )
        assert binding is None
        assert db.query(Environment).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceObject).filter_by(project_id=project.id).count() == 0
        assert db.query(ResourceBinding).filter_by(project_id=project.id).count() == 0
        manifest = next(
            item
            for item in list_capability_manifests()
            if item.id == "aws_s3_bucket_metadata"
        )
        metadata_ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        run = submit_tool_run(
            db,
            user.id,
            conversation.id,
            ToolCommandRequest(
                client_request_id=uuid.uuid4(),
                capability=metadata_ref,
                arguments={
                    "connector_id": str(connector.id),
                    "bucket_name": BUCKET,
                    "expected_owner": ACCOUNT_ID,
                },
            ),
        )
        db.commit()
        execution = decode_execution_config(run.execution_configuration)
        assert execution.resource_binding_refs == ()
        assert execution.connector_refs[0].id == connector.id
        claim = claim_next(db, worker_id="metadata-publication-worker")
        assert claim is not None
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
            session_factory=database_sessions,
        )
        artifact_root = tmp_path / "s3-metadata-artifacts"
        artifact_storage_id = uuid.uuid4()
        context.artifact_store = LocalArtifactStore(artifact_root, artifact_storage_id)
        context.artifact_store.initialize()
        run_id = run.id
        conversation_id = conversation.id
        operation_session = database_sessions

        stop_state = {"stopped": False}
        if publication_change in {
            "connector_revoked",
            "policy_disabled_after_read",
            "cancelled_after_read",
            "lease_lost_after_read",
        }:
            from src.openship.capabilities.definitions import get_tool_definition

            original_handler = get_tool_definition(manifest).handler

            async def change_scope_after_read(arguments, invocation, control):
                observation = await original_handler(arguments, invocation, control)
                with database_sessions() as mutation_db:
                    if publication_change == "connector_revoked":
                        row = mutation_db.query(Connector).filter_by(id=connector.id).one()
                        row.revoked_at = datetime.now(UTC)
                    elif publication_change == "policy_disabled_after_read":
                        from src.openship.capabilities.policy import set_tool_policy

                        monkeypatch.setattr(
                            settings, "connector_operator_user_id", user.id
                        )
                        set_tool_policy(
                            mutation_db,
                            actor=mutation_db.get(User, user.id),
                            capability_id=manifest.id,
                            enabled=False,
                            expected_revision=1,
                        )
                    elif publication_change == "lease_lost_after_read":
                        current_run = mutation_db.query(Run).filter_by(id=run_id).one()
                        current_job = mutation_db.query(RunJob).filter_by(
                            run_id=run_id
                        ).one()
                        replacement_fence = claim.fence + 1
                        current_run.fence = replacement_fence
                        current_job.fence = replacement_fence
                        current_job.owner_id = "replacement-metadata-worker"
                        current_job.lease_until = datetime.now(UTC) + timedelta(
                            minutes=1
                        )
                    else:
                        stop_state["stopped"] = True
                    mutation_db.commit()
                return observation

            monkeypatch.setitem(
                TOOL_HANDLER_OVERRIDES,
                "aws_s3_bucket_metadata",
                change_scope_after_read,
            )

    if publication_change == "policy_disabled_before_dispatch":
        from src.openship.capabilities.policy import set_tool_policy

        monkeypatch.setattr(settings, "connector_operator_user_id", user.id)
        gateway = ToolGateway(operation_session)
        _operation_id, admission_status = gateway._admit(
            claim,
            context,
            "manual:metadata",
            metadata_ref,
            {
                "connector_id": str(connector.id),
                "bucket_name": BUCKET,
                "expected_owner": ACCOUNT_ID,
            },
            (),
        )
        assert admission_status == "admitted"
        with database_sessions() as mutation_db:
            set_tool_policy(
                mutation_db,
                actor=mutation_db.get(User, user.id),
                capability_id=manifest.id,
                enabled=False,
                expected_revision=1,
            )
            mutation_db.commit()

    import src.openship.execution.gateway as gateway_module

    monkeypatch.setattr(
        gateway_module,
        "AwsRoleBroker",
        lambda factory: AwsRoleBroker(factory, transport_factory=transport_factory),
    )
    result = await ToolGateway(operation_session).execute_tool(
        claim=claim,
        context=context,
        logical_call_id="manual:metadata",
        ref=metadata_ref,
        arguments={
            "connector_id": str(connector.id),
            "bucket_name": BUCKET,
            "expected_owner": ACCOUNT_ID,
        },
        control=InvocationControl(stop=lambda: stop_state["stopped"]),
    )

    expected_requests = 0 if publication_change == "policy_disabled_before_dispatch" else 5
    assert len(requests) == expected_requests  # role, identity, location and versioning
    s3_requests = [request for request in requests if request.url.host.startswith("s3.")]
    if expected_requests:
        assert [request.url.query.decode() for request in s3_requests] == [
            "location",
            "versioning",
        ]
        assert all(
            request.headers["x-amz-expected-bucket-owner"] == ACCOUNT_ID
            for request in s3_requests
        )
    else:
        assert s3_requests == []

    if publication_change is None:
        assert result.status == "succeeded"
        assert result.result["versioning_status"] == "enabled"
        assert result.result["mfa_delete_status"] == "disabled"
        assert result.result["request_count"] == 5
        assert result.result["artifact"]["id"] == str(result.artifacts[0].id)
    elif publication_change == "lease_lost_after_read":
        assert result.status == "interrupted"
        assert result.error.code == "execution_interrupted"
        assert not result.artifacts
    elif publication_change == "cancelled_after_read":
        assert result.status == "cancelled"
        assert result.error.code == "cancelled"
        assert not result.artifacts
    else:
        assert result.status == "failed"
        assert result.error.code == (
            "connector_revoked"
            if publication_change == "connector_revoked"
            else "disabled"
        )
        assert not result.artifacts

    with operation_session() as db:
        operation = db.query(ToolOperation).filter_by(run_id=run_id).one()
        attempt = (
            db.query(ToolAttempt).filter_by(operation_id=operation.id).one_or_none()
        )
        observation = db.query(ResourceObservation).filter_by(operation_id=operation.id).one_or_none()
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
        if publication_change is None:
            artifact = db.query(Artifact).filter_by(id=result.artifacts[0].id).one()
            assert operation.status == "succeeded"
            assert attempt.outcome == "succeeded"
            assert artifact.kind == "cloud_observation"
            assert artifact.display_name == "s3-bucket-metadata.json"
            assert observation.schema_version == 2
            assert observation.source == "aws_s3_bucket_metadata"
            assert observation.resource_object_id is None
            assert observation.resource_binding_id is None
            assert observation.connector_id == connector.id
            assert json.loads(observation.target_json) == {
                "bucket_name": BUCKET,
                "expected_owner": ACCOUNT_ID,
            }
            assert observation.measurement_at is None
            assert observation.outcome == "measured"
            assert json.loads(observation.coverage_json) == {
                "versioning_status": "enabled",
                "mfa_delete_status": "disabled",
                "request_count": 5,
            }
            assert observation.evidence_artifact_id == artifact.id
            assert observation.evidence_sha256 == artifact.sha256
            assert "cloud.observed" in events
            assert "cloud.observed" in outbox_types
            restarted_store = LocalArtifactStore(artifact_root, artifact_storage_id)
            restarted_store.initialize()
            with restarted_store.open_verified(artifact) as evidence_file:
                evidence = json.load(evidence_file)
            assert evidence["versioning_status"] == "enabled"
            assert evidence["mfa_delete_status"] == "disabled"
            assert "raw_response" not in evidence
            assert "session_token" not in json.dumps(evidence)
        elif publication_change == "lease_lost_after_read":
            assert operation.status == "running"
            assert attempt is not None
            assert attempt.outcome == "started"
            assert observation is None
            assert "cloud.observed" not in events
            assert "cloud.observed" not in outbox_types
            artifact = db.query(Artifact).filter_by(
                originating_operation_id=operation.id
            ).one()
            assert artifact.status == "staging"
            assert operation.output_artifact_ids == "[]"
            assert not (artifact_root / artifact.relative_storage_key).exists()
        elif publication_change == "cancelled_after_read":
            assert operation.status == "cancelled"
            assert attempt is not None
            assert attempt.outcome == "cancelled"
            assert observation is None
            assert "cloud.observed" not in events
            assert "cloud.observed" not in outbox_types
            artifact = db.query(Artifact).filter_by(
                originating_operation_id=operation.id
            ).one()
            assert artifact.status == "deleted"
            assert artifact.reserved_bytes == 0
            assert not (artifact_root / artifact.relative_storage_key).exists()
        else:
            assert operation.status == "failed"
            if publication_change == "policy_disabled_before_dispatch":
                assert attempt is None
            else:
                assert attempt is not None
                assert attempt.outcome == "failed"
            assert observation is None
            assert "cloud.observed" not in events
            assert "cloud.observed" not in outbox_types
            artifact = db.query(Artifact).filter_by(
                originating_operation_id=operation.id
            ).one_or_none()
            if publication_change in {
                "policy_disabled_before_dispatch",
                "cancelled_after_read",
            }:
                assert artifact is None
            else:
                assert artifact is not None
                assert artifact.status == "deleted"


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
