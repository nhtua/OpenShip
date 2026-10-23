from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from httpx import AsyncByteStream
from pydantic import ValidationError

from src.openship.auth.models import User
from src.openship.capabilities.contracts import ConnectorRef
from src.openship.config import settings
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.credentials.broker import ConnectorCredentialError
from src.openship.connectors.credentials.dependencies import get_credential_store
from src.openship.connectors.credentials.store import CredentialContext
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.database.session import SessionLocal
from src.openship.execution.aws_transport import AwsSdkError
from src.openship.workspace.models import Project

BOOTSTRAP_ACCESS = "synthetic-bootstrap-access-key"  # rayloc:ignore
BOOTSTRAP_SECRET = "synthetic-bootstrap-secret-key"  # rayloc:ignore
ROLE_ACCESS = "synthetic-role-access-key"  # rayloc:ignore
ROLE_SECRET = "synthetic-role-secret-key"  # rayloc:ignore
ROLE_TOKEN = "synthetic-role-session-token"  # rayloc:ignore
ACCOUNT_ID = "123456789012"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/ReadOnly"


class _BodyStream(AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        return None


class _Control:
    def __init__(self):
        self.stopped = False

    def request_timeout_seconds(self, limit):
        return limit

    def raise_if_stopped(self):
        if self.stopped:
            from src.openship.providers.errors import ProviderError

            raise ProviderError("cancelled")


def _keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    encryption_path = tmp_path / "aws-broker-encryption.json"
    hmac_path = tmp_path / "aws-broker-hmac.json"
    for path, key in ((encryption_path, b"e" * 32), (hmac_path, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "aws-broker-test-v1",
                    "keys": {"aws-broker-test-v1": base64.b64encode(key).decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)


def _identity(db, label: str):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"aws-broker-{label}-{user_id.hex[:10]}",
        email=f"aws-broker-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(id=uuid.uuid4(), owner_user_id=user_id, name=f"AWS {label}")
    db.add_all([user, project])
    db.flush()
    return user, project


def _create_connector(db, user, project, *, source_expiration=None, session_token=None):
    connector = Connector(
        id=uuid.uuid4(),
        kind="aws",
        visibility="project",
        project_id=project.id,
        name="Synthetic read role",
        current_revision=1,
        enabled=True,
        managed_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    db.add(connector)
    db.flush()
    credential_id = uuid.uuid4()
    context = CredentialContext(
        installation_id=settings.connector_installation_id,
        visibility="project",
        project_id=project.id,
        connector_id=connector.id,
        credential_version_id=credential_id,
        schema_version=1,
        kind="aws_bootstrap",
    )
    payload = {
        "access_key_id": BOOTSTRAP_ACCESS,
        "secret_access_key": BOOTSTRAP_SECRET,
        "session_token": session_token,
        "expires_at": (
            source_expiration.astimezone(UTC).isoformat() if source_expiration else None
        ),
    }
    envelope = get_credential_store().encrypt(context, payload)
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
        expires_at=source_expiration,
        state="active",
    )
    db.add(credential)
    db.flush()
    configuration = ConnectorConfiguration(
        kind="aws",
        region="us-east-1",
        expected_account_id=ACCOUNT_ID,
        basic_role_arn=ROLE_ARN,
    )
    db.add(
        ConnectorRevision(
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
    )
    db.flush()
    return ConnectorRef(
        id=connector.id,
        kind="aws",
        configuration_revision=1,
        visibility="project",
        credential_version_id=credential.id,
    )


def _aws_types():
    import importlib

    try:
        module = importlib.import_module("src.openship.connectors.aws.broker")
    except ModuleNotFoundError:
        pytest.fail("the AWS role broker has not been implemented")
    return module.AwsRoleBroker, module.AwsRequestBudget, module.AwsBrokerError


def test_temporary_bootstrap_source_requires_expiration_and_hides_secrets():
    from src.openship.connectors.aws.contracts import AwsBootstrapAuth

    with pytest.raises(ValidationError):
        AwsBootstrapAuth.model_validate(
            {
                "access_key_id": BOOTSTRAP_ACCESS,
                "secret_access_key": BOOTSTRAP_SECRET,
                "session_token": "synthetic-session-token",  # rayloc:ignore
            }
        )

    auth = AwsBootstrapAuth(
        access_key_id=BOOTSTRAP_ACCESS,
        secret_access_key=BOOTSTRAP_SECRET,
        session_token="synthetic-session-token",  # rayloc:ignore
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert BOOTSTRAP_ACCESS not in repr(auth)
    assert BOOTSTRAP_SECRET not in repr(auth)
    assert "synthetic-session-token" not in repr(auth)


@pytest.mark.asyncio
async def test_temporary_source_token_signs_only_the_assume_role_request(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []
    source_token = "synthetic-source-session-token"  # rayloc:ignore

    async def respond(request):
        requests.append(request)
        form = parse_qs(request.content.decode())
        if form.get("Action") == ["AssumeRole"]:
            assert request.headers["x-amz-security-token"] == source_token
            return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))
        assert form.get("Action") == ["GetCallerIdentity"]
        assert request.headers["x-amz-security-token"] == ROLE_TOKEN
        assert source_token not in request.headers["x-amz-security-token"]
        return httpx.Response(200, stream=_BodyStream(_identity_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "temporary-source")
        reference = _create_connector(
            db,
            owner,
            project,
            session_token=source_token,
            source_expiration=datetime.now(UTC) + timedelta(hours=1),
        )
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    access = await broker.acquire(
        project_id=project_id,
        reference=reference,
        run_id=run_id,
        control=_Control(),
        request_budget=AwsRequestBudget(),
    )
    await access.aclose()

    assert len(requests) == 2


def _sts_error(code: str, message: str = "provider-body-sentinel") -> bytes:
    return f"""<ErrorResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
      <Error><Type>Sender</Type><Code>{code}</Code><Message>{message}</Message></Error>
      <RequestId>synthetic-request</RequestId>
    </ErrorResponse>""".encode()


def _assume_role_response(run_id: uuid.UUID, *, expiration=None) -> bytes:
    expires = expiration or datetime.now(UTC) + timedelta(seconds=900)
    session_name = f"openship-{run_id.hex}"
    return f"""<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
      <AssumeRoleResult>
        <Credentials>
          <AccessKeyId>{ROLE_ACCESS}</AccessKeyId>
          <SecretAccessKey>{ROLE_SECRET}</SecretAccessKey>
          <SessionToken>{ROLE_TOKEN}</SessionToken>
          <Expiration>{expires.isoformat()}</Expiration>
        </Credentials>
        <AssumedRoleUser>
          <AssumedRoleId>synthetic-role-id:{session_name}</AssumedRoleId>
          <Arn>arn:aws:sts::{ACCOUNT_ID}:assumed-role/ReadOnly/{session_name}</Arn>
        </AssumedRoleUser>
      </AssumeRoleResult>
      <ResponseMetadata><RequestId>synthetic-request</RequestId></ResponseMetadata>
    </AssumeRoleResponse>""".encode()


def _identity_response(run_id: uuid.UUID, *, account=ACCOUNT_ID, role="ReadOnly") -> bytes:
    session_name = f"openship-{run_id.hex}"
    arn = f"arn:aws:sts::{account}:assumed-role/{role}/{session_name}"
    return f"""<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
      <GetCallerIdentityResult>
        <UserId>synthetic-role-id:{session_name}</UserId>
        <Account>{account}</Account>
        <Arn>{arn}</Arn>
      </GetCallerIdentityResult>
      <ResponseMetadata><RequestId>synthetic-request</RequestId></ResponseMetadata>
    </GetCallerIdentityResponse>""".encode()


def _bucket_location_response() -> bytes:
    return b'<GetBucketLocationResponse xmlns="http://s3.amazonaws.com/doc/2006-03-01/"/>'


@pytest.mark.asyncio
async def test_assumes_only_pinned_role_verifies_identity_and_never_shares_bootstrap_auth(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []

    async def respond(request):
        requests.append(request)
        if request.url.host != "sts.us-east-1.amazonaws.com":
            return httpx.Response(200, stream=_BodyStream(_bucket_location_response()))
        form = parse_qs(request.content.decode())
        action = form.get("Action", [""])[0]
        if action == "AssumeRole":
            assert form["RoleArn"] == [ROLE_ARN]
            assert form["DurationSeconds"] == ["900"]
            assert form["RoleSessionName"] == [f"openship-{run_id.hex}"]
            assert "synthetic-bootstrap-access-key" in request.headers["authorization"]
            return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))
        assert action == "GetCallerIdentity"
        assert "synthetic-role-access-key" in request.headers["authorization"]
        assert "synthetic-bootstrap-access-key" not in request.headers["authorization"]
        return httpx.Response(200, stream=_BodyStream(_identity_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "basic")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    budget = AwsRequestBudget()
    access = await broker.acquire(
        project_id=project_id,
        reference=reference,
        run_id=run_id,
        control=_Control(),
        request_budget=budget,
    )
    try:
        s3 = access.client("s3")
        result = await access.run(s3.get_bucket_location, Bucket="synthetic-bucket")
        with pytest.raises(AwsSdkError) as caught:
            await access.run(s3.delete_bucket, Bucket="synthetic-bucket")
        assert caught.value.code == "aws_operation_not_supported"
        with pytest.raises(AwsSdkError) as caught:
            await access.run(access.client("sts").assume_role, RoleArn=ROLE_ARN)
        assert caught.value.code == "aws_operation_not_supported"
    finally:
        await access.aclose()

    assert result["LocationConstraint"] is None
    assert [request.url.host for request in requests] == [
        "sts.us-east-1.amazonaws.com",
        "sts.us-east-1.amazonaws.com",
        "s3.us-east-1.amazonaws.com",
    ]
    assert ROLE_SECRET not in repr(access)
    assert BOOTSTRAP_SECRET not in repr(access)
    assert budget.used == 3


@pytest.mark.asyncio
async def test_denied_assume_role_fails_without_trying_another_identity(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, AwsBrokerError = _aws_types()
    requests = []
    run_id = uuid.uuid4()

    async def respond(request):
        requests.append(request)
        return httpx.Response(403, stream=_BodyStream(_sts_error("AccessDenied")))

    with SessionLocal() as db:
        owner, project = _identity(db, "denied")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    with pytest.raises(AwsBrokerError) as caught:
        await broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=AwsRequestBudget(),
        )

    assert caught.value.code == "aws_trust_denied"
    assert "provider-body-sentinel" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_role_identity_verification_uses_the_same_request_budget(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "request-budget")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    budget = AwsRequestBudget(limit=1)
    with pytest.raises(AwsBrokerError) as caught:
        await broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=budget,
        )

    assert caught.value.code == "aws_request_limit_exceeded"
    assert budget.used == 1
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_expired_temporary_source_fails_before_any_sts_request(monkeypatch, tmp_path):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, stream=_BodyStream(_assume_role_response(uuid.uuid4())))

    with SessionLocal() as db:
        owner, project = _identity(db, "expired-source")
        reference = _create_connector(
            db,
            owner,
            project,
            session_token="synthetic-source-session-token",  # rayloc:ignore
            source_expiration=datetime.now(UTC) - timedelta(seconds=1),
        )
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    with pytest.raises(ConnectorCredentialError) as caught:
        await broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=uuid.uuid4(),
            control=_Control(),
            request_budget=AwsRequestBudget(),
        )

    assert caught.value.code == "connector_expired"
    assert requests == []


@pytest.mark.asyncio
async def test_concurrent_acquisition_shares_one_verified_role_session(monkeypatch, tmp_path):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []

    async def respond(request):
        requests.append(request)
        if not request.url.host.startswith("sts."):
            return httpx.Response(200, stream=_BodyStream(_bucket_location_response()))
        form = parse_qs(request.content.decode())
        action = form.get("Action", [""])[0]
        if action == "AssumeRole":
            await asyncio.sleep(0.05)
            return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))
        assert action == "GetCallerIdentity"
        return httpx.Response(200, stream=_BodyStream(_identity_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "concurrent")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    first_budget = AwsRequestBudget()
    second_budget = AwsRequestBudget()
    first = asyncio.create_task(
        broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=first_budget,
        )
    )
    await asyncio.sleep(0.01)
    second = asyncio.create_task(
        broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=second_budget,
        )
    )
    first_access, second_access = await asyncio.gather(first, second)
    try:
        first_result, second_result = await asyncio.gather(
            first_access.run(
                first_access.client("s3").get_bucket_location,
                Bucket="synthetic-bucket",
            ),
            second_access.run(
                second_access.client("s3").get_bucket_location,
                Bucket="synthetic-bucket",
            ),
        )
    finally:
        await asyncio.gather(first_access.aclose(), second_access.aclose())

    assert first_result["LocationConstraint"] is None
    assert second_result["LocationConstraint"] is None
    assert sum(
        1
        for request in requests
        if parse_qs(request.content.decode()).get("Action") == ["AssumeRole"]
    ) == 1
    assert len(requests) == 4
    assert first_budget.used + second_budget.used == 4


@pytest.mark.asyncio
async def test_revocation_is_checked_again_before_a_cached_role_dispatch(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []

    async def respond(request):
        requests.append(request)
        form = parse_qs(request.content.decode())
        if form.get("Action") == ["AssumeRole"]:
            return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))
        return httpx.Response(200, stream=_BodyStream(_identity_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "revoked")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    access = await broker.acquire(
        project_id=project_id,
        reference=reference,
        run_id=run_id,
        control=_Control(),
        request_budget=AwsRequestBudget(),
    )
    try:
        with SessionLocal() as db:
            connector = db.query(Connector).filter(Connector.id == reference.id).one()
            connector.revoked_at = datetime.now(UTC)
            db.commit()

        with pytest.raises(ConnectorCredentialError) as caught:
            await access.run(
                access.client("s3").get_bucket_location,
                Bucket="synthetic-bucket",
            )
    finally:
        await access.aclose()

    assert caught.value.code == "connector_revoked"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_revocation_between_assume_and_identity_stops_further_sts_calls(
    monkeypatch, tmp_path
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []
    connector_id = None

    async def respond(request):
        requests.append(request)
        with SessionLocal() as db:
            connector = db.query(Connector).filter(Connector.id == connector_id).one()
            connector.revoked_at = datetime.now(UTC)
            db.commit()
        return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))

    with SessionLocal() as db:
        owner, project = _identity(db, "revoked-during-assume")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        connector_id = reference.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    with pytest.raises(ConnectorCredentialError) as caught:
        await broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=AwsRequestBudget(),
        )

    assert caught.value.code == "connector_revoked"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account", "role", "expected_code"),
    [
        (ACCOUNT_ID, "UnexpectedRole", "aws_identity_mismatch"),
        ("999999999999", "ReadOnly", "aws_account_mismatch"),
    ],
)
async def test_role_identity_mismatch_stops_before_any_resource_read(
    monkeypatch, tmp_path, account, role, expected_code
):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, AwsBrokerError = _aws_types()
    run_id = uuid.uuid4()
    requests = []

    async def respond(request):
        requests.append(request)
        if parse_qs(request.content.decode()).get("Action") == ["AssumeRole"]:
            return httpx.Response(200, stream=_BodyStream(_assume_role_response(run_id)))
        return httpx.Response(
            200,
            stream=_BodyStream(_identity_response(run_id, account=account, role=role)),
        )

    with SessionLocal() as db:
        owner, project = _identity(db, "wrong-role")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    with pytest.raises(AwsBrokerError) as caught:
        await broker.acquire(
            project_id=project_id,
            reference=reference,
            run_id=run_id,
            control=_Control(),
            request_budget=AwsRequestBudget(),
        )

    assert caught.value.code == expected_code
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_role_session_renews_when_less_than_two_minutes_remain(monkeypatch, tmp_path):
    _keys(monkeypatch, tmp_path)
    AwsRoleBroker, AwsRequestBudget, _AwsBrokerError = _aws_types()
    current_time = [datetime.now(UTC)]
    run_id = uuid.uuid4()
    requests = []
    assume_count = 0

    async def respond(request):
        nonlocal assume_count
        requests.append(request)
        if not request.url.host.startswith("sts."):
            return httpx.Response(200, stream=_BodyStream(_bucket_location_response()))
        form = parse_qs(request.content.decode())
        action = form.get("Action", [""])[0]
        if action == "AssumeRole":
            assume_count += 1
            lifetime = 150 if assume_count == 1 else 900
            expiration = current_time[0] + timedelta(seconds=lifetime)
            return httpx.Response(
                200,
                stream=_BodyStream(
                    _assume_role_response(run_id, expiration=expiration)
                ),
            )
        if action == "GetCallerIdentity":
            return httpx.Response(200, stream=_BodyStream(_identity_response(run_id)))
        return httpx.Response(200, stream=_BodyStream(_bucket_location_response()))

    with SessionLocal() as db:
        owner, project = _identity(db, "renew")
        reference = _create_connector(db, owner, project)
        project_id = project.id
        db.commit()

    budget = AwsRequestBudget()
    broker = AwsRoleBroker(
        SessionLocal,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
        clock=lambda: current_time[0],
    )
    access = await broker.acquire(
        project_id=project_id,
        reference=reference,
        run_id=run_id,
        control=_Control(),
        request_budget=budget,
    )
    try:
        current_time[0] += timedelta(seconds=40)
        result = await access.run(
            access.client("s3").get_bucket_location,
            Bucket="synthetic-bucket",
        )
    finally:
        await access.aclose()

    assert result["LocationConstraint"] is None
    assert assume_count == 2
    assert len(requests) == 5
    assert budget.used == 5
