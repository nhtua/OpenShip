from __future__ import annotations

import hashlib
import importlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ConnectorRef,
    ExecutionConfigV4,
    canonical_json,
)
from src.openship.connectors.aws.read_catalog import load_aws_read_catalog
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.credentials.keys import HmacKeyRing
from src.openship.tools.aws.contracts import AwsReadArguments, AwsToolError


def _read_module():
    try:
        return importlib.import_module("src.openship.tools.aws.read")
    except ModuleNotFoundError:
        pytest.fail("the managed AWS read tool has not been implemented")


class _Access:
    def __init__(self, response=None, *, error=None):
        self.configuration = ConnectorConfiguration(
            kind="aws",
            region="us-east-1",
            expected_account_id="123456789012",
            basic_role_arn="arn:aws:iam::123456789012:role/ReadOnly",
        )
        self.response = response or {}
        self.error = error
        self.calls = []
        self.closed = False

    def client(self, service, *, region=None):
        class Client:
            def __getattr__(self, method_name):
                def invoke(**parameters):
                    return (service, region, method_name, parameters)

                invoke.__name__ = method_name
                return invoke

        return Client()

    async def run(self, method, *, descriptor=None, **parameters):
        service, region, method_name, client_parameters = method(**parameters)
        assert client_parameters == parameters
        self.calls.append((service, region, method_name, descriptor, parameters))
        if self.error is not None:
            raise self.error
        return self.response(service, region, method_name, parameters) if callable(self.response) else self.response

    async def aclose(self):
        self.closed = True


def _context(monkeypatch, *, response=None, error=None, project_id=None):
    module = _read_module()
    catalog = load_aws_read_catalog()
    project_id = project_id or uuid.uuid4()
    connector_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    connector_ref = ConnectorRef(
        id=connector_id,
        kind="aws",
        configuration_revision=2,
        visibility="project",
        credential_version_id=uuid.uuid4(),
    )
    tool_ref = CapabilityRef(
        id="aws_read",
        version="1.0.0",
        manifest_digest="a" * 64,
    )
    execution = ExecutionConfigV4(
        graph_kind="tool",
        graph_version=3,
        allowed_capabilities=(tool_ref,),
        connector_refs=(connector_ref,),
        aws_read_catalog_digest=catalog.digest,
    )
    access = _Access(response, error=error)
    received = []

    async def acquire(reference):
        received.append(reference)
        return access

    class FakeKeyring:
        active_key_id = "aws-read-test-v1"
        _key = b"h" * 32

        def digest(self, value, *, key_id=None):
            ring = HmacKeyRing(self.active_key_id, {self.active_key_id: self._key})
            return ring.digest(value, key_id=key_id)

        def verify(self, key_id, value, signature):
            ring = HmacKeyRing(self.active_key_id, {self.active_key_id: self._key})
            return ring.verify(key_id, value, signature)

    monkeypatch.setattr(module, "get_idempotency_keyring", lambda: FakeKeyring())
    monkeypatch.setattr(
        module,
        "_resolve_connector_configuration",
        lambda _context, reference: access.configuration
        if reference == connector_ref
        else None,
        raising=False,
    )
    context = SimpleNamespace(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=project_id,
            conversation_id=uuid.uuid4(),
            action="execute_tool",
            target_id=str(uuid.uuid4()),
        ),
        execution=execution,
        operation_id=operation_id,
        session_factory=object(),
        aws_access_factory=acquire,
    )
    return module, catalog, context, access, received, connector_ref


@pytest.mark.asyncio
async def test_lists_live_buckets_without_any_local_resource_binding(monkeypatch):
    buckets = [
        {"Name": "alpha-public", "CreationDate": datetime(2024, 1, 1, tzinfo=UTC)},
        {"Name": "cross-account", "CreationDate": datetime(2024, 2, 1, tzinfo=UTC)},
    ]
    module, catalog, context, access, received, reference = _context(
        monkeypatch, response={"Buckets": buckets, "Owner": {"ID": "owner"}}
    )
    result = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="list_buckets",
        ),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )

    assert result.data["Buckets"][0]["Name"] == "alpha-public"
    assert result.schema_version == 2
    assert result.catalog_digest == catalog.digest
    assert result.connector_revision == 2
    assert result.account_id == "123456789012"
    assert received == [reference]
    assert access.calls[0][0:3] == ("s3", "us-east-1", "list_buckets")
    assert access.closed


@pytest.mark.asyncio
async def test_named_cross_account_bucket_is_read_without_listing_or_owner_guess(monkeypatch):
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch, response={"Status": "Enabled"}
    )
    result = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="get_bucket_versioning",
            region="us-west-2",
            parameters={"Bucket": "another-account-bucket"},
        ),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )

    assert result.data == {"Status": "Enabled"}
    assert len(access.calls) == 1
    assert access.calls[0][1] == "us-west-2"
    assert access.calls[0][4] == {"Bucket": "another-account-bucket"}


@pytest.mark.asyncio
async def test_explicit_region_read_does_not_depend_on_optional_bucket_location(monkeypatch):
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch, response={"Status": "Suspended"}
    )
    result = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="get_bucket_versioning",
            region="eu-west-1",
            parameters={"Bucket": "regional-bucket"},
        ),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )

    assert result.data["Status"] == "Suspended"
    assert [call[2] for call in access.calls] == ["get_bucket_versioning"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "operation", "parameters", "connector", "catalog_digest"),
    [
        ("s3", "delete_bucket", {"Bucket": "demo-bucket"}, "pinned", None),
        ("s3", "list_objects_v2", {"Bucket": "demo-bucket", "Bogus": 1}, "pinned", None),
        ("s3", "list_objects_v2", {"Bucket": "demo-bucket", "EndpointUrl": "https://attacker.invalid"}, "pinned", None),
        ("unknown", "list_buckets", {}, "pinned", None),
        ("s3", "list_buckets", {}, "foreign", None),
        ("s3", "list_buckets", {}, "pinned", "0" * 64),
    ],
)
async def test_rejected_operation_inputs_connectors_and_catalogs_make_no_provider_calls(
    monkeypatch, service, operation, parameters, connector, catalog_digest
):
    module, _catalog, context, access, received, reference = _context(monkeypatch)
    if catalog_digest is not None:
        context.execution = context.execution.model_copy(
            update={"aws_read_catalog_digest": catalog_digest}
        )
    connector_id = uuid.uuid4() if connector == "foreign" else reference.id
    with pytest.raises(AwsToolError):
        await module.execute_aws_read(
            AwsReadArguments(
                connector_id=connector_id,
                service=service,
                operation=operation,
                parameters=parameters,
            ),
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert received == []
    assert access.calls == []


@pytest.mark.asyncio
async def test_expired_signed_cursor_fails_before_connector_acquisition(monkeypatch):
    module, catalog, context, access, received, reference = _context(monkeypatch)
    parameters = {"Bucket": "example-bucket"}
    cursor = module._encode_cursor(
        {
            "namespace": module._CURSOR_NAMESPACE,
            "project_id": str(context.authorization.project_id),
            "connector_id": str(reference.id),
            "connector_revision": reference.configuration_revision,
            "catalog_digest": catalog.digest,
            "service": "s3",
            "operation": "list_objects_v2",
            "region": "us-east-1",
            "parameters_digest": hashlib.sha256(
                canonical_json(parameters)
            ).hexdigest(),
            "provider_token": "expired-token",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
    )
    with pytest.raises(AwsToolError) as caught:
        await module.execute_aws_read(
            AwsReadArguments(
                connector_id=reference.id,
                service="s3",
                operation="list_objects_v2",
                parameters=parameters,
                cursor=cursor,
            ),
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert caught.value.code == "aws_invalid_cursor"
    assert received == []
    assert access.calls == []


@pytest.mark.asyncio
async def test_read_cursor_is_signed_scoped_and_hides_provider_token(monkeypatch):
    pages = [
        {
            "Contents": [{"Key": "first.txt", "Size": 1}],
            "IsTruncated": True,
            "NextContinuationToken": "provider-token-secret-sentinel",
        },
        {"Contents": [{"Key": "second.txt", "Size": 2}], "IsTruncated": False},
    ]
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch, response=lambda *_args: pages.pop(0)
    )
    control = SimpleNamespace(raise_if_stopped=lambda: None)
    first = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="list_objects_v2",
            parameters={"Bucket": "example-bucket"},
        ),
        context,
        control,
    )
    assert first.next_cursor
    assert "provider-token-secret-sentinel" not in str(first.data)
    assert "NextContinuationToken" not in first.data

    second = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="list_objects_v2",
            parameters={"Bucket": "example-bucket"},
            cursor=first.next_cursor,
            page_size=50,
        ),
        context,
        control,
    )
    assert second.data["Contents"][0]["Key"] == "second.txt"
    assert access.calls[0][4]["MaxKeys"] == 20
    assert access.calls[1][4]["MaxKeys"] == 50
    assert access.calls[1][4]["ContinuationToken"] == "provider-token-secret-sentinel"
    assert second.next_cursor is None


@pytest.mark.asyncio
async def test_bad_or_foreign_cursor_fails_before_connector_acquisition(monkeypatch):
    module, _catalog, context, access, received, reference = _context(monkeypatch)
    arguments = AwsReadArguments(
        connector_id=reference.id,
        service="s3",
        operation="list_objects_v2",
        parameters={"Bucket": "example-bucket"},
        cursor="not-a-signed-token",
    )
    with pytest.raises(AwsToolError):
        await module.execute_aws_read(
            arguments,
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert received == []
    assert access.calls == []


@pytest.mark.asyncio
async def test_foreign_project_cursor_fails_before_connector_acquisition(monkeypatch):
    module, _catalog, context, _access, _received, reference = _context(
        monkeypatch,
        response={
            "Contents": [],
            "IsTruncated": True,
            "NextContinuationToken": "synthetic-next-token",
        },
    )
    first = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="s3",
            operation="list_objects_v2",
            parameters={"Bucket": "example-bucket"},
        ),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )
    _module, _catalog, foreign_context, access, received, foreign_ref = _context(
        monkeypatch
    )
    foreign_context.execution = foreign_context.execution.model_copy(
        update={"connector_refs": (reference,)}
    )
    monkeypatch.setattr(
        module,
        "_resolve_connector_configuration",
        lambda _context, requested: access.configuration
        if requested in {reference, foreign_ref}
        else None,
    )
    with pytest.raises(AwsToolError) as caught:
        await module.execute_aws_read(
            AwsReadArguments(
                connector_id=reference.id,
                service="s3",
                operation="list_objects_v2",
                parameters={"Bucket": "example-bucket"},
                cursor=first.next_cursor,
            ),
            foreign_context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert caught.value.code == "aws_invalid_cursor"
    assert received == []
    assert access.calls == []


@pytest.mark.asyncio
async def test_oversized_page_fails_without_returning_or_advancing_cursor(monkeypatch):
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch,
        response={
            "Contents": [{"Key": "x" * (65 * 1024)}],
            "IsTruncated": True,
            "NextContinuationToken": "must-not-advance",
        },
    )
    with pytest.raises(AwsToolError) as caught:
        await module.execute_aws_read(
            AwsReadArguments(
                connector_id=reference.id,
                service="s3",
                operation="list_objects_v2",
                parameters={"Bucket": "example-bucket"},
            ),
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert caught.value.code == "aws_output_limit_exceeded"
    assert access.closed


@pytest.mark.asyncio
async def test_oversized_continuation_page_can_retry_same_cursor_with_smaller_page(monkeypatch):
    pages = [
        {
            "Contents": [{"Key": "first"}],
            "IsTruncated": True,
            "NextContinuationToken": "retryable-provider-token",
        },
        {
            "Contents": [{"Key": "x" * (65 * 1024)}],
            "IsTruncated": True,
            "NextContinuationToken": "must-not-advance",
        },
        {"Contents": [{"Key": "second"}], "IsTruncated": False},
    ]
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch, response=lambda *_args: pages.pop(0)
    )
    base = {
        "connector_id": reference.id,
        "service": "s3",
        "operation": "list_objects_v2",
        "parameters": {"Bucket": "example-bucket"},
    }
    first = await module.execute_aws_read(
        AwsReadArguments(**base),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )
    with pytest.raises(AwsToolError) as caught:
        await module.execute_aws_read(
            AwsReadArguments(**base, cursor=first.next_cursor),
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert caught.value.code == "aws_output_limit_exceeded"
    retried = await module.execute_aws_read(
        AwsReadArguments(**base, cursor=first.next_cursor, page_size=5),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )
    assert retried.data["Contents"] == [{"Key": "second"}]
    assert retried.next_cursor is None
    assert access.calls[1][4]["ContinuationToken"] == "retryable-provider-token"
    assert access.calls[2][4]["ContinuationToken"] == "retryable-provider-token"
    assert access.calls[1][4]["MaxKeys"] == 20
    assert access.calls[2][4]["MaxKeys"] == 5


@pytest.mark.asyncio
async def test_provider_access_denial_keeps_actionable_safe_message(monkeypatch):
    from src.openship.execution.aws_transport import AwsSdkError

    module, _catalog, context, _access, _received, reference = _context(
        monkeypatch,
        error=AwsSdkError("aws_access_denied"),
    )
    with pytest.raises(AwsToolError) as caught:
        await module.execute_aws_read(
            AwsReadArguments(
                connector_id=reference.id,
                service="s3",
                operation="list_buckets",
            ),
            context,
            SimpleNamespace(raise_if_stopped=lambda: None),
        )
    assert caught.value.code == "aws_access_denied"


@pytest.mark.asyncio
async def test_sensitive_output_members_are_removed_before_observation(monkeypatch):
    sentinel = "rds-master-password-secret-sentinel"
    module, _catalog, context, access, _received, reference = _context(
        monkeypatch,
        response={
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "db-example",
                    "PendingModifiedValues": {"MasterUserPassword": sentinel},
                }
            ]
        },
    )
    observation = await module.execute_aws_read(
        AwsReadArguments(
            connector_id=reference.id,
            service="rds",
            operation="describe_db_instances",
            parameters={"DBInstanceIdentifier": "db-example"},
        ),
        context,
        SimpleNamespace(raise_if_stopped=lambda: None),
    )
    assert sentinel not in str(observation.data)
    assert "MasterUserPassword" not in str(observation.data)
    assert access.closed


def test_compact_preview_truncation_does_not_change_pagination_cursor(monkeypatch):
    module, _catalog, context, _access, _received, reference = _context(monkeypatch)
    observation_type = importlib.import_module("src.openship.tools.aws.contracts").AwsReadObservation
    tool = context.execution.allowed_capabilities[0]
    observation = observation_type(
        tool=tool,
        operation_id=context.operation_id,
        project_id=context.authorization.project_id,
        connector_id=reference.id,
        connector_revision=reference.configuration_revision,
        catalog_digest=context.execution.aws_read_catalog_digest,
        account_id="123456789012",
        service="s3",
        operation="list_objects_v2",
        region="us-east-1",
        target={"bucket": "example-bucket"},
        retrieved_at=datetime.now(UTC),
        request_count=2,
        data={"Contents": [{"Key": f"file-{index}-" + "x" * 1000} for index in range(30)]},
        outcome="partial",
        coverage={"has_more": True},
        next_cursor="signed-next-page-cursor",
    )
    from src.openship.artifacts.contracts import ArtifactRef

    result = module.project_aws_read_result(
        observation,
        ArtifactRef(
            id=uuid.uuid4(),
            sha256="c" * 64,
            size_bytes=100,
            media_type="application/json",
        ),
    )
    assert result.preview_truncated
    assert result.next_cursor == "signed-next-page-cursor"
    assert len(__import__("json").dumps(result.preview, separators=(",", ":")).encode()) <= 16 * 1024
    assert len(canonical_json(result.model_dump(mode="json"))) <= 16 * 1024
