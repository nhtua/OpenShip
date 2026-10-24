import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.catalog import (
    list_capability_manifests,
    list_legacy_search_manifests,
    require_phase4_dispatchable,
    resolve_capability,
)
from src.openship.capabilities.contracts import (
    CapabilityManifest,
    CapabilityManifestV3,
    CapabilityRef,
    ExecutionConfigV3,
    ExecutionConfigV4,
    OutputPolicy,
    canonical_digest,
    decode_capability_manifest,
    decode_execution_config,
)
from src.openship.execution.errors import CapabilityError
from src.openship.tools.aws.contracts import (
    AwsReadArguments,
    AwsReadObservation,
    AwsReadResult,
    AwsS3BucketTargetArguments,
    AwsS3MetadataObservationV2,
)
from src.openship.tools.search.contracts import SearchArguments, SearchItem


def test_tool_ids_and_connector_kinds_are_independent():
    manifests = {item.id: item for item in list_capability_manifests()}
    legacy = {item.id: item for item in list_legacy_search_manifests()}

    assert set(manifests) == {
        "brave_web_search",
        "tavily_web_search",
        "workspace_list_files",
        "workspace_read_file",
        "aws_check_connection",
        "aws_s3_bucket_storage",
        "aws_s3_list_bound_buckets",
        "aws_s3_bucket_metadata",
    }
    assert manifests["brave_web_search"].connector_kinds == ("brave",)
    assert manifests["tavily_web_search"].connector_kinds == ("tavily",)
    assert legacy["brave_web_search"].credential_binding_ref == "BRAVE_SEARCH_API_KEY"
    assert legacy["tavily_web_search"].credential_binding_ref == "TAVILY_API_KEY"
    assert manifests["brave_web_search"].id != manifests["tavily_web_search"].id
    assert "provider" not in manifests["brave_web_search"].input_schema["properties"]
    assert "provider" not in manifests["tavily_web_search"].input_schema["properties"]
    for capability_id in ("workspace_list_files", "workspace_read_file"):
        manifest = manifests[capability_id]
        assert manifest.effect == "read_only_local"
        assert manifest.credential_binding_ref is None
        assert manifest.outputs == ()
        assert "path" not in manifest.input_schema["properties"]

    inventory = manifests["aws_s3_list_bound_buckets"]
    assert inventory.effect == "read_only_local"
    assert inventory.connector_kinds == ()
    assert inventory.operations == ()
    assert inventory.target_kinds == ()
    assert inventory.outputs == ()
    assert inventory.enablement_requirements == ("operator_enabled",)


def test_manifest_digest_covers_implementation_schema_and_limits():
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    unsigned = manifest.model_dump(mode="json", exclude={"manifest_digest"})
    expected = hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()

    assert manifest.manifest_digest == expected
    changed_schema = {**unsigned, "input_schema": {"type": "string"}}
    changed_limits = {**unsigned, "limits": {**unsigned["limits"], "max_results": 9}}
    assert (
        hashlib.sha256(
            json.dumps(
                changed_schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        != manifest.manifest_digest
    )
    assert (
        hashlib.sha256(
            json.dumps(
                changed_limits,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        != manifest.manifest_digest
    )


def test_changed_disabled_unknown_capability_refused():
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )

    assert resolve_capability(ref, enabled_ids=frozenset({manifest.id})) == manifest
    with pytest.raises(CapabilityError) as disabled:
        resolve_capability(ref, enabled_ids=frozenset())
    assert disabled.value.code == "disabled"
    with pytest.raises(CapabilityError) as changed:
        resolve_capability(
            ref.model_copy(update={"manifest_digest": "0" * 64}),
            enabled_ids=frozenset({manifest.id}),
        )
    assert changed.value.code == "changed"
    with pytest.raises(CapabilityError) as unknown:
        resolve_capability(
            ref.model_copy(update={"id": "arbitrary_tool"}),
            enabled_ids=frozenset({"arbitrary_tool"}),
        )
    assert unknown.value.code == "unknown"


def test_mutable_output_policy_not_dispatchable_in_phase4():
    manifest = next(
        item for item in list_legacy_search_manifests() if item.id == "brave_web_search"
    )
    unsigned = manifest.model_dump(mode="json", exclude={"manifest_digest"})
    unsigned["outputs"] = ({"name": "search-results.json", "mutability": "mutable"},)
    unsigned["enablement_requirements"] = tuple(unsigned["enablement_requirements"])
    unsigned["manifest_digest"] = hashlib.sha256(
        json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    mutable = CapabilityManifest.model_validate(unsigned)

    with pytest.raises(CapabilityError) as mutable_error:
        require_phase4_dispatchable(mutable)
    assert mutable_error.value.code == "mutable_output"
    assert (
        OutputPolicy(name="search-results.json", mutability="mutable").mutability
        == "mutable"
    )


def test_search_arguments_and_items_enforce_utf8_bounds():
    assert (
        SearchArguments(query="terraform state", max_results=5).query
        == "terraform state"
    )
    with pytest.raises(ValidationError):
        SearchArguments(query="é" * 1025)
    with pytest.raises(ValidationError):
        SearchArguments(query="one " * 76)
    with pytest.raises(ValidationError):
        SearchArguments(query="valid", endpoint="https://attacker.invalid")
    with pytest.raises(ValidationError):
        SearchItem(title="é" * 257, url="https://example.com", snippet="ok", rank=1)


def test_execution_v4_does_not_accept_resource_binding_references():
    connector_id = "d2c54ef2-fcd7-47fa-8719-64259b4d910e"
    payload = {
        "schema_version": 4,
        "graph_kind": "chat",
        "graph_version": 4,
        "connector_refs": [
            {
                "id": connector_id,
                "kind": "aws",
                "configuration_revision": 1,
                "visibility": "project",
                "credential_version_id": "0e64e8a3-c38f-45bb-91a8-d1eb48495b66",
            }
        ],
        "aws_read_catalog_digest": "a" * 64,
    }
    decoded = decode_execution_config(payload)
    assert isinstance(decoded, ExecutionConfigV4)
    assert decoded.aws_read_catalog_digest == "a" * 64
    with pytest.raises(ValidationError):
        decode_execution_config(
            {**payload, "resource_binding_refs": [{"id": connector_id}]}
        )

    # Existing versioned payloads keep their original decoder identities.
    assert isinstance(
        decode_execution_config(
            {"schema_version": 3, "graph_kind": "chat", "graph_version": 1}
        ),
        ExecutionConfigV3,
    )


def test_manifest_v3_pins_generic_aws_operation_set_to_catalog_digest():
    unsigned = {
        "schema_version": 3,
        "id": "aws_read",
        "version": "1.0.0",
        "implementation_digest": "b" * 64,
        "manifest_digest": "0" * 64,
        "description": "Read supported AWS metadata.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "effect": "read_only_external",
        "enablement_requirements": ("operator_enabled",),
        "limits": {"max_tool_calls": 1},
        "outputs": (),
        "connector_kinds": ("aws",),
        "operations": ("s3.list_buckets",),
        "target_kinds": (),
        "execution_kind": "maintained",
        "aws_read_catalog_digest": "c" * 64,
    }
    unsigned["manifest_digest"] = canonical_digest(
        {key: value for key, value in unsigned.items() if key != "manifest_digest"}
    )
    decoded = decode_capability_manifest(unsigned)
    assert isinstance(decoded, CapabilityManifestV3)
    assert decoded.aws_read_catalog_digest == "c" * 64

    unrestricted = {**unsigned, "operations": ()}
    unrestricted["manifest_digest"] = canonical_digest(
        {key: value for key, value in unrestricted.items() if key != "manifest_digest"}
    )
    with pytest.raises(ValidationError):
        decode_capability_manifest(unrestricted)
    missing_catalog = {
        key: value
        for key, value in unsigned.items()
        if key != "aws_read_catalog_digest"
    }
    missing_catalog["manifest_digest"] = canonical_digest(
        {
            key: value
            for key, value in missing_catalog.items()
            if key != "manifest_digest"
        }
    )
    with pytest.raises(ValidationError):
        decode_capability_manifest(missing_catalog)


def test_aws_read_contracts_bind_targets_to_connector_and_bound_evidence():
    from uuid import UUID

    connector = UUID("d2c54ef2-fcd7-47fa-8719-64259b4d910e")
    tool = CapabilityRef(
        id="aws_read",
        version="1.0.0",
        manifest_digest="a" * 64,
    )
    arguments = AwsReadArguments(
        connector_id=connector,
        service="s3",
        operation="list_objects_v2",
        region="us-east-1",
        parameters={"Bucket": "example-bucket"},
        page_size=10,
    )
    assert arguments.page_size == 10
    with pytest.raises(ValidationError):
        AwsReadArguments(
            connector_id=connector,
            service="s3",
            operation="list_objects_v2",
            parameters={},
            page_size=51,
        )

    observation = AwsReadObservation(
        tool=tool,
        operation_id=UUID("bb60f797-4f22-4c53-b1d6-68d8462efcd8"),
        project_id=UUID("0e64e8a3-c38f-45bb-91a8-d1eb48495b66"),
        connector_id=connector,
        connector_revision=1,
        catalog_digest="c" * 64,
        account_id="123456789012",
        service="s3",
        operation="list_objects_v2",
        region="us-east-1",
        target={"bucket": "example-bucket"},
        retrieved_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
        request_count=2,
        data={"contents": []},
        outcome="measured",
        coverage={"truncated": False},
    )
    assert observation.schema_version == 2
    assert observation.target["bucket"] == "example-bucket"
    result = AwsReadResult(
        tool=tool,
        operation_id=UUID("bb60f797-4f22-4c53-b1d6-68d8462efcd8"),
        connector_id=connector,
        connector_revision=1,
        catalog_digest="c" * 64,
        account_id="123456789012",
        service="s3",
        operation="list_objects_v2",
        region="us-east-1",
        retrieved_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
        outcome="measured",
        preview={"contents": []},
        preview_truncated=False,
        artifact=ArtifactRef(
            id=UUID("30ac38ab-f757-4d8e-b490-8d765859fca9"),
            sha256="e" * 64,
            size_bytes=9,
            media_type="application/json",
        ),
    )
    assert result.artifact.size_bytes == 9
    assert "resource_binding_id" not in result.model_dump()

    direct_target = AwsS3BucketTargetArguments(
        connector_id=connector,
        bucket_name="example-bucket",
        expected_bucket_owner="123456789012",
    )
    assert direct_target.bucket_name == "example-bucket"
    with pytest.raises(ValidationError):
        AwsS3BucketTargetArguments(
            connector_id=connector,
            bucket_name="example-bucket",
            resource_binding_id="d2c54ef2-fcd7-47fa-8719-64259b4d910e",
        )
    metadata = AwsS3MetadataObservationV2(
        tool=CapabilityRef(
            id="aws_s3_bucket_metadata",
            version="1.0.0",
            manifest_digest="d" * 64,
        ),
        operation_id=UUID("bb60f797-4f22-4c53-b1d6-68d8462efcd8"),
        project_id=UUID("0e64e8a3-c38f-45bb-91a8-d1eb48495b66"),
        connector_id=connector,
        connector_revision=1,
        account_id="123456789012",
        region="us-east-1",
        bucket_name="example-bucket",
        versioning_status="enabled",
        mfa_delete_status="not_reported",
        request_count=3,
        retrieved_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
    )
    assert metadata.schema_version == 2
    assert "resource_id" not in metadata.model_dump()
