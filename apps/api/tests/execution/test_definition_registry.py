from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.openship.capabilities.catalog import (
    list_capability_manifests,
    resolve_capability,
)
from src.openship.capabilities.contracts import (
    CapabilityManifestV2,
    CapabilityRef,
    ConnectorRef,
    ExecutionConfig,
    ExecutionConfigV2,
    ResourceBindingRef,
    canonical_digest,
    decode_capability_manifest,
    decode_execution_config,
)
from src.openship.capabilities.definitions import (
    ToolDefinitionEntry,
    get_tool_definition,
    implementation_digest_for,
    register_tool_definition,
    unregister_tool_definition,
)


class ArgumentsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    count: int = Field(ge=1, le=3)


class PublicResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    label: str
    result_id: uuid.UUID


def _manifest() -> CapabilityManifestV2:
    implementation_files = ("tests/execution/test_definition_registry.py",)
    fields: dict[str, Any] = {
        "schema_version": 2,
        "id": "registry_test_tool",
        "version": "1.0.0",
        "implementation_digest": implementation_digest_for(implementation_files),
        "description": "A bounded test registry tool.",
        "input_schema": ArgumentsModel.model_json_schema(),
        "output_schema": PublicResultModel.model_json_schema(),
        "effect": "read_only_local",
        "enablement_requirements": (),
        "limits": {"max_model_result_bytes": 1024},
        "outputs": (),
        "connector_kinds": (),
        "operations": (),
        "target_kinds": (),
        "execution_kind": "maintained",
    }
    return CapabilityManifestV2(**fields, manifest_digest=canonical_digest(fields))


def test_registered_definition_supplies_validation_projection_and_model_description():
    manifest = _manifest()
    entry = ToolDefinitionEntry(
        manifest=manifest,
        description="A bounded test registry tool.",
        argument_model=ArgumentsModel,
        result_model=PublicResultModel,
        handler=lambda arguments, _context, _control: arguments,
        implementation_files=("tests/execution/test_definition_registry.py",),
        result_projector=lambda value: {
            "label": value["private_label"],
            "result_id": value["private_result_id"],
        },
    )
    register_tool_definition(entry)
    try:
        ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        resolved = resolve_capability(ref, frozenset({manifest.id}))
        definition = get_tool_definition(resolved)

        assert definition is entry
        assert definition.description == "A bounded test registry tool."
        assert definition.validate_arguments({"count": 2}) == ArgumentsModel(count=2)
        result_id = uuid.uuid4()
        assert definition.project_result(
            {"private_label": "safe", "private_result_id": str(result_id)}
        ) == {"label": "safe", "result_id": str(result_id)}
        with pytest.raises(ValidationError):
            definition.validate_arguments({"count": 4})
    finally:
        unregister_tool_definition(manifest.id)


def test_execution_config_decoder_preserves_v1_and_round_trips_v2_references():
    legacy = ExecutionConfig(graph_kind="tool", graph_version=1)
    assert decode_execution_config(legacy.model_dump_json()) == legacy

    connector = ConnectorRef(
        id=uuid.uuid4(),
        kind="aws",
        configuration_revision=3,
        visibility="global",
        credential_version_id=uuid.uuid4(),
    )
    binding = ResourceBindingRef(
        id=uuid.uuid4(),
        revision=4,
        connector_id=connector.id,
        connector_revision=connector.configuration_revision,
    )
    managed = ExecutionConfigV2(
        graph_kind="tool",
        graph_version=2,
        connector_refs=(connector,),
        resource_binding_refs=(binding,),
        selected_resource_binding_refs=(binding,),
        admitted_context_message_ids=(uuid.uuid4(),),
    )

    decoded = decode_execution_config(managed.model_dump_json())

    assert decoded == managed
    assert "secret" not in managed.model_dump_json().lower()

    secret_bearing = managed.model_dump(mode="json")
    secret_bearing["settings"] = {"credential": "must-not-be-stored"}
    with pytest.raises(ValidationError):
        ExecutionConfigV2.model_validate_json(json.dumps(secret_bearing))


def test_capability_manifest_decoder_preserves_v1_and_reads_connector_requirements():
    legacy = next(
        manifest
        for manifest in list_capability_manifests()
        if manifest.schema_version == 1
    )
    assert decode_capability_manifest(legacy.model_dump_json()) == legacy

    fields: dict[str, Any] = {
        "schema_version": 2,
        "id": "managed_test_tool",
        "version": "1.0.0",
        "implementation_digest": "2" * 64,
        "description": "A bounded managed test tool.",
        "input_schema": ArgumentsModel.model_json_schema(),
        "output_schema": PublicResultModel.model_json_schema(),
        "effect": "read_only_external",
        "enablement_requirements": ("connector",),
        "limits": {"timeout_seconds": 5},
        "outputs": (),
        "connector_kinds": ("brave",),
        "operations": ("web_search",),
        "target_kinds": (),
        "execution_kind": "maintained",
    }
    current = CapabilityManifestV2(
        **fields,
        manifest_digest=canonical_digest(fields),
    )

    assert decode_capability_manifest(current.model_dump_json()) == current


def test_execution_config_decoder_rejects_unknown_versions():
    with pytest.raises(ValueError, match="unsupported execution configuration version"):
        decode_execution_config({"schema_version": 4, "graph_kind": "tool"})
