import hashlib
import json

import pytest
from pydantic import ValidationError

from src.openship.capabilities.catalog import (
    list_capability_manifests,
    list_legacy_search_manifests,
    require_phase4_dispatchable,
    resolve_capability,
)
from src.openship.capabilities.contracts import (
    CapabilityManifest,
    CapabilityRef,
    OutputPolicy,
)
from src.openship.execution.errors import CapabilityError
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
