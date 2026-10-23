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
    AwsS3MetadataArguments,
    AwsS3MetadataObservation,
    AwsS3MetadataResult,
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
def test_versioning_normalization_preserves_absent_states(response, versioning, mfa_delete):
    assert normalize_versioning_configuration(response) == (versioning, mfa_delete)


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"Status": "Unknown"},
        {"MFADelete": "Unknown"},
        {"Status": 1},
        {"MFADelete": False},
    ],
)
def test_versioning_normalization_rejects_malformed_or_unknown_states(response):
    with pytest.raises((TypeError, ValueError)):
        normalize_versioning_configuration(response)


def test_metadata_manifest_is_bucket_scoped_and_does_not_change_storage_operations():
    manifests = {item.id: item for item in list_capability_manifests()}
    metadata = manifests[_TOOL_ID]
    storage = manifests["aws_s3_bucket_storage"]

    assert metadata.effect == "read_only_external"
    assert metadata.implementation_digest != storage.implementation_digest
    assert metadata.connector_kinds == ("aws",)
    assert metadata.operations == (
        "get_caller_identity",
        "get_bucket_location",
        "get_bucket_versioning",
    )
    assert metadata.target_kinds == ("aws_s3_bucket",)
    assert metadata.outputs[0].name == "s3-bucket-metadata.json"
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


def test_metadata_is_model_selectable_only_with_an_admitted_resource_binding():
    manifest = next(
        item for item in list_capability_manifests() if item.id == _TOOL_ID
    )
    capability = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    execution = SimpleNamespace(
        allowed_capabilities=(capability,),
        selected_resource_binding_refs=(),
    )

    assert _available_tools(execution, frozenset({_TOOL_ID})) == ()

    execution.selected_resource_binding_refs = (object(),)
    tools = _available_tools(execution, frozenset({_TOOL_ID}))
    assert len(tools) == 1
    assert tools[0].name == _TOOL_ID


def test_metadata_contracts_expose_only_bucket_identity_normalized_state_and_evidence():
    arguments = AwsS3MetadataArguments(resource_binding_id=uuid4())
    assert arguments.model_dump(mode="json") == {
        "resource_binding_id": str(arguments.resource_binding_id)
    }
    with pytest.raises(ValidationError):
        AwsS3MetadataArguments.model_validate(
            {"resource_binding_id": str(uuid4()), "bucket_name": "invented"}
        )

    observation_fields = set(AwsS3MetadataObservation.model_fields)
    assert "versioning_status" in observation_fields
    assert "mfa_delete_status" in observation_fields
    assert "raw_response" not in observation_fields
    assert "session_token" not in observation_fields
    assert "measurement_at" not in observation_fields
    assert "artifact" in AwsS3MetadataResult.model_fields


def test_metadata_timestamp_is_timezone_aware():
    assert datetime.now(UTC).utcoffset() is not None


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
        self.closed = False
        self.request_budget = SimpleNamespace(used=5)

    def client(self, service):
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
        if method.__name__ == "get_caller_identity":
            return self.identity
        if "ExpectedBucketOwner" in parameters and len(self.calls) == 2:
            return {"LocationConstraint": self.location}
        if method.__name__ == "get_bucket_versioning":
            return self.versioning
        raise AssertionError("unexpected AWS operation")

    async def aclose(self):
        self.closed = True


def _metadata_context(binding, connector_ref, access):
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


def _metadata_binding():
    connector_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        resource_object_id=uuid4(),
        revision=3,
        connector_id=connector_id,
        connector_revision=4,
        account_id="123456789012",
        expected_owner="123456789012",
        region="us-east-1",
        bucket_name="synthetic-bound-bucket",
    ), ConnectorRef(
        id=connector_id,
        kind="aws",
        configuration_revision=4,
        visibility="project",
        credential_version_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_metadata_handler_verifies_identity_owner_location_and_projects_normalized_state(monkeypatch):
    binding, connector_ref = _metadata_binding()
    access = _Access(versioning={"Status": "Enabled", "MFADelete": "Disabled"})
    monkeypatch.setattr(s3_metadata, "_owned_binding", lambda _context, _id: binding)
    monkeypatch.setattr(s3_metadata, "_connector_reference", lambda _context, _binding: connector_ref)
    context = _metadata_context(binding, connector_ref, access)

    observation = await s3_metadata.aws_s3_bucket_metadata(
        AwsS3MetadataArguments(resource_binding_id=binding.id),
        context,
        InvocationControl(),
    )

    assert observation.versioning_status == "enabled"
    assert observation.mfa_delete_status == "disabled"
    assert observation.request_count == 5
    assert observation.resource_binding_id == binding.id
    assert not hasattr(observation, "measurement_at")
    assert [name for name, _parameters in access.calls] == [
        "get_caller_identity",
        "get_bucket_location",
        "get_bucket_versioning",
    ]
    assert access.calls[1][1] == {
        "Bucket": binding.bucket_name,
        "ExpectedBucketOwner": binding.expected_owner,
    }
    assert access.calls[2][1] == access.calls[1][1]
    assert access.closed is True


@pytest.mark.asyncio
async def test_metadata_handler_rejects_an_unselected_binding_before_acquiring_aws(monkeypatch):
    binding, connector_ref = _metadata_binding()
    access = _Access()
    context = _metadata_context(binding, connector_ref, access)

    with pytest.raises(AwsToolError) as refused:
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArguments(resource_binding_id=binding.id),
            context,
            InvocationControl(),
        )

    assert refused.value.code == "aws_resource_unavailable"
    assert access.calls == []
    assert access.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "999999999999"),
        ("expected_owner", "999999999999"),
        ("region", "eu-west-1"),
    ],
)
async def test_metadata_handler_rejects_binding_identity_mismatch_before_aws(
    monkeypatch, field, value
):
    binding, connector_ref = _metadata_binding()
    binding = SimpleNamespace(**{**vars(binding), field: value})
    access = _Access()
    monkeypatch.setattr(s3_metadata, "_owned_binding", lambda _context, _id: binding)
    monkeypatch.setattr(s3_metadata, "_connector_reference", lambda _context, _binding: connector_ref)
    context = _metadata_context(binding, connector_ref, access)

    with pytest.raises(AwsToolError) as refused:
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArguments(resource_binding_id=binding.id),
            context,
            InvocationControl(),
        )

    assert refused.value.code == "aws_resource_unavailable"
    assert access.calls == []
    assert access.closed is True


@pytest.mark.asyncio
async def test_metadata_handler_cancellation_stops_before_the_next_sdk_request(monkeypatch):
    binding, connector_ref = _metadata_binding()
    access = _Access()
    monkeypatch.setattr(s3_metadata, "_owned_binding", lambda _context, _id: binding)
    monkeypatch.setattr(s3_metadata, "_connector_reference", lambda _context, _binding: connector_ref)
    context = _metadata_context(binding, connector_ref, access)

    class CancelBeforeVersioning:
        def __init__(self):
            self.checks = 0

        def raise_if_stopped(self):
            self.checks += 1
            if self.checks == 3:
                raise ProviderError("cancelled")

    with pytest.raises(ProviderError, match="cancelled"):
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArguments(resource_binding_id=binding.id),
            context,
            CancelBeforeVersioning(),
        )

    assert [name for name, _parameters in access.calls] == [
        "get_caller_identity",
        "get_bucket_location",
    ]
    assert access.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "location", "versioning", "error_code"),
    [
        (
            {"Account": "999999999999", "Arn": "arn:aws:sts::999999999999:assumed-role/read-role/session"},
            "us-east-1",
            {},
            "aws_identity_mismatch",
        ),
        (None, "eu-west-1", {}, "aws_region_mismatch"),
        (None, "us-east-1", {"Status": "Unknown"}, "aws_invalid_metadata_response"),
    ],
)
async def test_metadata_handler_fails_closed_on_identity_region_and_response_errors(
    monkeypatch, identity, location, versioning, error_code
):
    binding, connector_ref = _metadata_binding()
    access = _Access(identity=identity, location=location, versioning=versioning)
    monkeypatch.setattr(s3_metadata, "_owned_binding", lambda _context, _id: binding)
    monkeypatch.setattr(s3_metadata, "_connector_reference", lambda _context, _binding: connector_ref)
    context = _metadata_context(binding, connector_ref, access)

    with pytest.raises(AwsToolError) as refused:
        await s3_metadata.aws_s3_bucket_metadata(
            AwsS3MetadataArguments(resource_binding_id=binding.id),
            context,
            InvocationControl(),
        )

    assert refused.value.code == error_code
    assert access.closed is True
