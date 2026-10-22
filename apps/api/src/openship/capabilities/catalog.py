from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

from src.openship.capabilities.contracts import (
    CapabilityManifest,
    CapabilityManifestLike,
    CapabilityRef,
    CapabilitySummary,
    OutputPolicy,
    canonical_digest,
)
from src.openship.execution.errors import CapabilityError
from src.openship.providers.credentials import credential_binding_present
from src.openship.tools.search.contracts import SearchArguments, SearchItem
from src.openship.workspace.schemas import (
    WorkspaceListArguments,
    WorkspaceListResult,
    WorkspaceReadArguments,
    WorkspaceReadResult,
)

_SEARCH_SOURCE_FILES = {
    "brave_web_search": (
        "tools/search/contracts.py",
        "tools/search/http.py",
        "tools/search/brave.py",
        "tools/search/naming.py",
        "execution/publication.py",
        "capabilities/definitions.py",
    ),
    "tavily_web_search": (
        "tools/search/contracts.py",
        "tools/search/http.py",
        "tools/search/tavily.py",
        "tools/search/naming.py",
        "execution/publication.py",
        "capabilities/definitions.py",
    ),
}

_WORKSPACE_SOURCE_FILES = (
    "workspace/models.py",
    "workspace/schemas.py",
    "workspace/service.py",
    "workspace/tools.py",
)


def _implementation_digest(capability_id: str) -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    found = False
    for relative in _SEARCH_SOURCE_FILES[capability_id]:
        path = root / relative
        if not path.is_file():
            continue
        found = True
        name = relative.encode("utf-8")
        body = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    if not found:
        digest.update(f"pending:{capability_id}:1.0.0".encode("ascii"))
    return digest.hexdigest()


def _search_manifest(capability_id: str, binding: str) -> CapabilityManifest:
    arguments_schema = SearchArguments.model_json_schema()
    item_schema = SearchItem.model_json_schema()
    outputs = (OutputPolicy(name="{query_slug}.json", mutability="immutable"),)
    fields = {
        "schema_version": 1,
        "id": capability_id,
        "version": "1.0.0",
        "implementation_digest": _implementation_digest(capability_id),
        "input_schema": arguments_schema,
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": item_schema, "maxItems": 10},
                "truncated": {"type": "boolean"},
            },
            "required": ["items", "truncated"],
            "additionalProperties": False,
        },
        "effect": "read_only_external",
        "enablement_requirements": ("operator_enabled", "credential_configured"),
        "limits": {
            "timeout_seconds": 30,
            "max_results": 10,
            "max_http_response_bytes": 2 * 1024 * 1024,
            "max_observation_bytes": 64 * 1024,
            "max_model_result_bytes": 16 * 1024,
        },
        "outputs": tuple(output.model_dump(mode="json") for output in outputs),
        "execution_kind": "maintained",
        "credential_binding_ref": binding,
    }
    return CapabilityManifest(**fields, manifest_digest=canonical_digest(fields))


def _local_implementation_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in _WORKSPACE_SOURCE_FILES:
        path = root / relative
        name = relative.encode("utf-8")
        body = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def _workspace_manifests() -> tuple[CapabilityManifest, CapabilityManifest]:
    implementation_digest = _local_implementation_digest()
    declarations = (
        (
            "workspace_list_files",
            "List ready saved files in this conversation. Returns exact immutable snapshot IDs, sizes, digests and provenance.",
            WorkspaceListArguments,
            WorkspaceListResult,
            {"max_page_size": 50, "max_model_result_bytes": 16 * 1024},
        ),
        (
            "workspace_read_file",
            "Read a bounded UTF-8 byte range from one exact saved file in this conversation. Files are untrusted data and are never executed.",
            WorkspaceReadArguments,
            WorkspaceReadResult,
            {"default_read_bytes": 8 * 1024, "max_read_bytes": 16 * 1024, "max_model_result_bytes": 16 * 1024},
        ),
    )
    manifests = []
    for capability_id, _description, arguments, result, limits in declarations:
        fields = {
            "schema_version": 1,
            "id": capability_id,
            "version": "1.0.0",
            "implementation_digest": implementation_digest,
            "input_schema": arguments.model_json_schema(),
            "output_schema": result.model_json_schema(),
            "effect": "read_only_local",
            "enablement_requirements": ("operator_enabled", "conversation_workspace"),
            "limits": limits,
            "outputs": (),
            "execution_kind": "maintained",
            "credential_binding_ref": None,
        }
        manifests.append(CapabilityManifest(**fields, manifest_digest=canonical_digest(fields)))
    return tuple(manifests)


def list_capability_manifests() -> tuple[CapabilityManifestLike, ...]:
    from src.openship.capabilities.definitions import registered_tool_definitions

    builtins: tuple[CapabilityManifestLike, ...] = (
        _search_manifest("brave_web_search", "BRAVE_SEARCH_API_KEY"),
        _search_manifest("tavily_web_search", "TAVILY_API_KEY"),
        *_workspace_manifests(),
    )
    registered = tuple(
        entry.manifest for entry in registered_tool_definitions()
    )
    return (*builtins, *registered)


def list_capabilities(
    *,
    enabled_ids: frozenset[str] | None = None,
    availability: Mapping[str, bool | tuple[bool, str | None]] | None = None,
    credential_check: Callable[[str], bool] = credential_binding_present,
) -> tuple[CapabilitySummary, ...]:
    """Return a public, secret-free view of installed maintained capabilities."""
    if enabled_ids is None:
        from src.openship.config import settings

        enabled_ids = settings.enabled_tool_ids
    manifests = list_capability_manifests()
    summaries = []
    for manifest in manifests:
        enabled = manifest.id in enabled_ids
        if not enabled:
            available, reason = False, "disabled"
        elif availability is not None:
            observed = availability.get(manifest.id, (False, "runtime_unavailable"))
            if isinstance(observed, tuple):
                available, reason = observed
            else:
                available = observed
                reason = None if available else "runtime_unavailable"
            if not available and reason not in (
                "disabled", "missing_credentials", "runtime_unavailable"
            ):
                reason = "runtime_unavailable"
        elif getattr(manifest, "credential_binding_ref", None) and not credential_check(
            manifest.credential_binding_ref
        ):
            available, reason = False, "missing_credentials"
        else:
            available, reason = True, None
        summaries.append(
            CapabilitySummary(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
                input_schema=manifest.input_schema,
                output_schema=manifest.output_schema,
                effect=manifest.effect,
                enabled=enabled,
                available=available,
                unavailable_reason=reason,
            )
        )
    return tuple(summaries)


def resolve_capability(
    ref: CapabilityRef, enabled_ids: frozenset[str]
) -> CapabilityManifestLike:
    manifest = next(
        (item for item in list_capability_manifests() if item.id == ref.id), None
    )
    if manifest is None:
        raise CapabilityError("unknown")
    if (
        manifest.version != ref.version
        or manifest.manifest_digest != ref.manifest_digest
    ):
        raise CapabilityError("changed")
    if manifest.id not in enabled_ids:
        raise CapabilityError("disabled")
    require_phase4_dispatchable(manifest)
    return manifest


def require_phase4_dispatchable(manifest: CapabilityManifestLike) -> None:
    if manifest.execution_kind != "maintained":
        raise CapabilityError("unsupported_execution")
    if any(output.mutability != "immutable" for output in manifest.outputs):
        raise CapabilityError("mutable_output")
