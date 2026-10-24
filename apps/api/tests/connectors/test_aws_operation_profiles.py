from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.openship.capabilities.contracts import ConnectorRef
from src.openship.connectors.aws.clients import AwsRoleAccess
from src.openship.connectors.aws.read_catalog import require_read_operation
from src.openship.execution.aws_transport import AwsRequestBudget, AwsSdkError
from src.openship.execution.gateway import ToolGateway
from src.openship.providers.control import InvocationControl


class _Broker:
    @staticmethod
    def _validate_before_dispatch(**_kwargs):
        return None

    @staticmethod
    def _usable(_session, _now):
        return True

    @staticmethod
    def _now():
        return datetime.now(UTC)


class _SdkContext:
    def __init__(self):
        self.calls = []

    def client(self, service, *, region=None):
        self.client_requests = getattr(self, "client_requests", [])
        self.client_requests.append((service, region))
        api_names = {
            "get_bucket_versioning": "GetBucketVersioning",
            "list_buckets": "ListBuckets",
            "describe_instances": "DescribeInstances",
        }
        return SimpleNamespace(
            meta=SimpleNamespace(
                method_to_api_mapping=api_names,
                service_model=SimpleNamespace(operation_names=set(api_names.values())),
            ),
            get_bucket_versioning=self._get_bucket_versioning,
            list_buckets=self._list_buckets,
            describe_instances=self._describe_instances,
        )

    def _get_bucket_versioning(self, **parameters):
        return parameters

    def _list_buckets(self, **parameters):
        return parameters

    def _describe_instances(self, **parameters):
        return parameters

    async def run(self, method, **parameters):
        self.calls.append((method.__name__, parameters))
        return method(**parameters)

    async def aclose(self):
        return None


def _access(capability_id, allowed_operations=None):
    context = _SdkContext()
    access = AwsRoleAccess(
        broker=_Broker(),
        project_id=uuid.uuid4(),
        reference=ConnectorRef(
            id=uuid.uuid4(),
            kind="aws",
            configuration_revision=1,
            visibility="project",
            credential_version_id=uuid.uuid4(),
        ),
        run_id=uuid.uuid4(),
        control=InvocationControl(),
        request_budget=AwsRequestBudget(),
        configuration=SimpleNamespace(region="us-east-1"),
        session=None,
        sdk_context=context,
        capability_id=capability_id,
        allowed_operations=allowed_operations,
    )
    return access, context


@pytest.mark.asyncio
async def test_metadata_capability_can_read_versioning_state_through_scoped_access():
    access, sdk = _access("aws_s3_bucket_metadata")
    try:
        result = await access.run(
            access.client("s3").get_bucket_versioning,
            Bucket="synthetic-bucket",
            ExpectedBucketOwner="123456789012",
        )
    finally:
        await access.aclose()

    assert result == {
        "Bucket": "synthetic-bucket",
        "ExpectedBucketOwner": "123456789012",
    }
    assert sdk.calls == [
        (
            "_get_bucket_versioning",
            {"Bucket": "synthetic-bucket", "ExpectedBucketOwner": "123456789012"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability_id",
    ["aws_s3_bucket_storage", "aws_check_connection"],
)
async def test_metadata_operation_is_not_available_to_other_aws_capabilities(
    capability_id,
):
    access, sdk = _access(capability_id)
    try:
        with pytest.raises(AwsSdkError) as refused:
            await access.run(
                access.client("s3").get_bucket_versioning,
                Bucket="synthetic-bucket",
                ExpectedBucketOwner="123456789012",
            )
        assert refused.value.code == "aws_operation_not_supported"
    finally:
        await access.aclose()

    assert sdk.calls == []


@pytest.mark.asyncio
async def test_gateway_passes_the_admitted_capability_into_the_role_access_profile():
    reference = ConnectorRef(
        id=uuid.uuid4(),
        kind="aws",
        configuration_revision=2,
        visibility="project",
        credential_version_id=uuid.uuid4(),
    )
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    control = InvocationControl()
    received = {}
    access = object()

    class Broker:
        async def acquire(self, **kwargs):
            received.update(kwargs)
            return access

    accesses = []
    factory = ToolGateway._aws_access_factory(
        broker=Broker(),
        capability_id="aws_s3_bucket_metadata",
        allowed_operations=("get_caller_identity", "get_bucket_location", "get_bucket_versioning"),
        project_id=project_id,
        run_id=run_id,
        request_budget=AwsRequestBudget(),
        control_holder=lambda: control,
        references=(reference,),
        accesses=accesses,
    )

    assert await factory(reference) is access
    assert received["capability_id"] == "aws_s3_bucket_metadata"
    assert received["allowed_operations"] == (
        "get_caller_identity",
        "get_bucket_location",
        "get_bucket_versioning",
    )
    assert received["project_id"] == project_id
    assert received["run_id"] == run_id
    assert received["reference"] == reference
    assert accesses == [access]


@pytest.mark.asyncio
async def test_generic_access_dispatches_only_the_exact_catalog_service_operation_pair():
    access, sdk = _access("aws_read", ("s3.list_buckets",))
    descriptor = require_read_operation("s3", "list_buckets")
    try:
        result = await access.run(
            access.client("s3", region="us-west-2").list_buckets,
            descriptor=descriptor,
        )
    finally:
        await access.aclose()

    assert result == {}
    assert sdk.calls == [("_list_buckets", {})]
    assert sdk.client_requests == [("s3", "us-west-2")]


@pytest.mark.asyncio
async def test_generic_access_refuses_same_named_or_unpinned_service_methods_before_sdk_call():
    access, sdk = _access("aws_read", ("s3.list_buckets",))
    descriptor = require_read_operation("s3", "list_buckets")
    try:
        with pytest.raises(AwsSdkError) as wrong_service:
            await access.run(
                access.client("ec2").list_buckets,
                descriptor=descriptor,
            )
        assert wrong_service.value.code == "aws_operation_not_supported"
        with pytest.raises(AwsSdkError) as no_descriptor:
            await access.run(access.client("s3").list_buckets)
        assert no_descriptor.value.code == "aws_operation_not_supported"
        with pytest.raises(AwsSdkError) as not_in_manifest:
            await access.run(
                access.client("ec2").describe_instances,
                descriptor=require_read_operation("ec2", "describe_instances"),
            )
        assert not_in_manifest.value.code == "aws_operation_not_supported"
    finally:
        await access.aclose()

    assert sdk.calls == []
