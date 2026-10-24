from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ConnectorRef
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.runs.agent_graph import _available_tools
from src.openship.tools.aws import s3_metadata
from src.openship.tools.aws.contracts import (
    AwsS3MetadataArgumentsV2,
    AwsS3MetadataObservationV2,
    AwsS3MetadataResultV2,
    AwsToolError,
)
from src.openship.tools.aws.s3_metadata import normalize_versioning_configuration

_TOOL_ID = "aws_s3_bucket_metadata"


@pytest.mark.parametrize(
    ("response", "versioning", "mfa_delete"),
    [
        ({"Status": "Enabled", "MFADelete": "Enabled"}, "enabled", "enabled"),
        ({"Status": "Suspended", "MFADelete": "Disabled"}, "suspended", "disabled"),
        ({}, "not_enabled", "not_reported"),
        ({"Status": "Enabled"}, "enabled", "not_reported"),
    ],
)
def test_versioning_normalization_preserves_absent_states(
    response, versioning, mfa_delete
):
    assert normalize_versioning_configuration(response) == (versioning, mfa_delete)


@pytest.mark.parametrize(
    "response",
    [None, [], {"Status": "Unknown"}, {"MFADelete": "Unknown"}, {"Status": 1}],
)
def test_versioning_normalization_rejects_malformed_or_unknown_states(response):
    with pytest.raises((TypeError, ValueError)):
        normalize_versioning_configuration(response)


def test_metadata_manifest_uses_a_direct_bucket_target():
    manifests = {item.id: item for item in list_capability_manifests()}
    metadata = manifests[_TOOL_ID]
    storage = manifests["aws_s3_bucket_storage"]

    assert metadata.effect == "read_only_external"
    assert metadata.version == "2.0.0"
    assert metadata.implementation_digest != storage.implementation_digest
    assert metadata.connector_kinds == ("aws",)
    assert metadata.operations == (
        "get_caller_identity",
        "get_bucket_location",
        "get_bucket_versioning",
    )
    assert metadata.target_kinds == ()
    assert "resource_binding_id" not in metadata.input_schema["properties"]
    assert {"connector_id", "bucket_name", "region", "expected_owner"}.issubset(
        metadata.input_schema["properties"]
    )
    assert metadata.outputs[0].name == "s3-bucket-metadata.json"
    assert storage.target_kinds == ()
    assert storage.operations == (
        "get_caller_identity",
        "get_bucket_location",
        "get_metric_data",
    )
    assert metadata.limits["timeout_seconds"] == 30
    assert metadata.limits["max_requests"] == 8
    assert metadata.limits["max_http_response_bytes"] == 2 * 1024 * 1024
    assert metadata.limits["max_observation_bytes"] == 64 * 1024
    assert metadata.limits["max_model_result_bytes"] == 16 * 1024


def test_metadata_is_a_manual_helper_and_contracts_have_no_binding_fields():
    manifest = next(
        item for item in list_capability_manifests() if item.id == _TOOL_ID
    )
    capability = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    execution = SimpleNamespace(allowed_capabilities=(capability,))
    assert _available_tools(execution, frozenset({_TOOL_ID})) == ()

    arguments = AwsS3MetadataArgumentsV2(
        connector_id=uuid4(), bucket_name="public-demo-bucket"
    )
    assert arguments.model_dump(mode="json") == {
        "connector_id": str(arguments.connector_id),
        "bucket_name": "public-demo-bucket",
        "region": None,
        "expected_owner": None,
    }
    with pytest.raises(ValidationError):
        AwsS3MetadataArgumentsV2.model_validate(
            {
                "connector_id": str(uuid4()),
                "bucket_name": "public-demo-bucket",
                "resource_binding_id": str(uuid4()),
            }
        )
    assert "resource_binding_id" not in AwsS3MetadataArgumentsV2.model_fields
    assert "resource_binding_id" not in AwsS3MetadataObservationV2.model_fields
    assert "resource_binding_id" not in AwsS3MetadataResultV2.model_fields
    assert "raw_response" not in AwsS3MetadataObservationV2.model_fields
    assert "session_token" not in AwsS3MetadataObservationV2.model_fields
    assert "measurement_at" not in AwsS3MetadataObservationV2.model_fields


class _Access:
    def __init__(self, *, location="us-east-1", versioning=None, identity=None):
        self.configuration = SimpleNamespace(
            expected_account_id="123456789012",
            basic_role_arn="arn:aws:iam::123456789012:role/read-role",
            region="us-east-1",
        )
        self.location = location
        self.versioning = versioning or {}
        self.identity = identity or {
            "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/read-role/run-session",
        }
        self.calls = []
        self.clients = []
        self.closed = False
        self.request_budget = SimpleNamespace(used=2)

    def client(self, service, *, region=None):
        self.clients.append((service, region))
        if service == "sts":
            def get_caller_identity():
                return None

            return SimpleNamespace(get_caller_identity=get_caller_identity)
        assert service == "s3"

        def get_bucket_location(**_kwargs):
            return None

        def get_bucket_versioning(**_kwargs):
            return None

        return SimpleNamespace(
            get_bucket_location=get_bucket_location,
            get_bucket_versioning=get_bucket_versioning,
        )

    async def run(self, method, **parameters):
        self.calls.append((method.__name__, parameters))
        self.request_budget.used += 1
        if method.__name__ == "get_caller_identity":
            return self.identity
        if method.__name__ == "get_bucket_location":
            return {"LocationConstraint": self.location}
        if method.__name__ == "get_bucket_versioning":
            return self.versioning
        raise AssertionError("unexpected AWS operation")

    async def aclose(self):
        self.closed = True


def _direct_metadata_context(connector_ref, access):
    manifest = next(item for item in list_capability_manifests() if item.id == _TOOL_ID)
    capability = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )

    async def acquire(reference):
        assert reference == connector_ref
        return access

    return SimpleNamespace(
        authorization=SimpleNamespace(project_id=uuid4()),
        execution=SimpleNamespace(
            allowed_capabilities=(capability,),
            connector_refs=(connector_ref,),
            resource_binding_refs=(),
        ),
        operation_id=uuid4(),
        aws_access_factory=acquire,
    )


def _connector_ref():
    return ConnectorRef(
        id=uuid4(),
        kind="aws",
        configuration_revision=4,
        visibility="project",
        credential_version_id=uuid4(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "expected_owner", "expected_calls", "location_parameters"),
    [
        (
            None,
            "123456789012",
            ["get_caller_identity", "get_bucket_location", "get_bucket_versioning"],
            {"Bucket": "never-registered-bucket", "ExpectedBucketOwner": "123456789012"},
        ),
        (
            "us-east-1",
            None,
            ["get_caller_identity", "get_bucket_versioning"],
            None,
        ),
    ],
)
async def test_direct_metadata_reads_unregistered_bucket_and_uses_optional_inputs(
    region, expected_owner, expected_calls, location_parameters
):
    connector_ref = _connector_ref()
    access = _Access(versioning={"Status": "Enabled", "MFADelete": "Disabled"})
    context = _direct_metadata_context(connector_ref, access)

    observation = await s3_metadata.aws_s3_bucket_metadata(
        AwsS3MetadataArgumentsV2(
            connector_id=connector_ref.id,
            bucket_name="never-registered-bucket",
            region=region,
            expected_owner=expected_owner,
        ),
        context,
        InvocationControl(),
    )

    assert observation.bucket_name == "never-registered-bucket"
    assert observation.region == "us-east-1"
    assert observation.connector_id == connector_ref.id
    assert observation.versioning_status == "enabled"
    assert observation.mfa_delete_status == "disabled"
    assert observation.request_count == (5 if region is None else 4)
    assert [name for name, _parameters in access.calls] == expected_calls
    if location_parameters is not None:
        assert access.calls[1][1] == location_parameters
        assert access.calls[2][1] == location_parameters
    else:
        assert access.calls[1][1] == {"Bucket": "never-registered-bucket"}
    assert ("s3", "us-east-1") in access.clients
    assert access.closed


@pytest.mark.asyncio
async def test_metadata_cancellation_stops_before_versioning_request():
    connector_ref = _connector_ref()
    access = _Access()
    context = _direct_metadata_context(connector_ref, access)

    class CancelBeforeVersioning:
        def __init__(self):
            self.checks = 0

        def raise_if_stopped(self):
            self.checks += 1
            if self.checks == 3:
                raise ProviderError("cancelled")

    with pytest.raises(ProviderError, match="cancelled"):
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArgumentsV2(
                connector_id=connector_ref.id,
                bucket_name="never-registered-bucket",
            ),
            context,
            CancelBeforeVersioning(),
        )

    assert [name for name, _parameters in access.calls] == [
        "get_caller_identity",
        "get_bucket_location",
    ]
    assert access.closed


@pytest.mark.asyncio
async def test_metadata_rejects_a_connector_that_is_not_pinned():
    connector_ref = _connector_ref()
    other_ref = _connector_ref()
    access = _Access()
    context = _direct_metadata_context(other_ref, access)

    with pytest.raises(AwsToolError) as refused:
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArgumentsV2(
                connector_id=connector_ref.id,
                bucket_name="never-registered-bucket",
            ),
            context,
            InvocationControl(),
        )

    assert refused.value.code == "aws_resource_unavailable"
    assert access.calls == []
    assert access.closed is False


@pytest.mark.asyncio
async def test_metadata_rejects_malformed_versioning_response():
    connector_ref = _connector_ref()
    access = _Access(versioning={"Status": "Unknown"})
    context = _direct_metadata_context(connector_ref, access)

    with pytest.raises(AwsToolError) as refused:
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArgumentsV2(
                connector_id=connector_ref.id,
                bucket_name="never-registered-bucket",
                region="us-east-1",
            ),
            context,
            InvocationControl(),
        )

    assert refused.value.code == "aws_invalid_metadata_response"
    assert access.closed


def test_metadata_retrieval_timestamp_is_timezone_aware():
    assert datetime.now(UTC).utcoffset() is not None
