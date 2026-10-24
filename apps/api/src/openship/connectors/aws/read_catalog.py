"""Pinned, offline AWS read-operation catalog and Botocore shape validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import botocore
import botocore.session
from botocore.model import Shape

_DATA_PATH = Path(__file__).with_name("data") / "read_operations.v1.json"
_MAX_SCHEMA_BYTES = 16 * 1024
_MAX_OUTPUT_SCHEMA_BYTES = 64 * 1024
_MAX_SCHEMA_DEPTH = 16
_MAX_PARAMETER_BYTES = 16 * 1024
_MAX_PAGE_SIZE = 50
_SCHEMA_CONVERSION_VERSION = 2
_SNAPSHOT_OPERATION_FIELDS = (
    "service",
    "operation",
    "api_name",
    "iam_actions",
    "input_schema_digest",
    "output_schema_digest",
    "protocol",
    "pagination",
    "supported",
    "unsupported_reason",
)
_ACTION_PROPERTIES = (
    "IsList",
    "IsPermissionManagement",
    "IsTaggingOnly",
    "IsWrite",
)
_SAFE_PROTOCOLS = frozenset(
    {"json", "json-rpc", "query", "ec2", "rest-json", "rest-xml", "smithy-rpc-v2-cbor"}
)
_SECRET_OPERATIONS = frozenset(
    {
        ("secretsmanager", "get_secret_value"),
        ("secretsmanager", "batch_get_secret_value"),
        ("ssm", "get_parameter"),
        ("ssm", "get_parameters"),
        ("ssm", "get_parameters_by_path"),
        ("appconfigdata", "get_latest_configuration"),
    }
)
_CREDENTIAL_OPERATIONS = frozenset(
    {
        ("sts", "assume_role"),
        ("sts", "assume_role_with_saml"),
        ("sts", "assume_role_with_web_identity"),
        ("sts", "get_federation_token"),
        ("sts", "get_session_token"),
        ("sts", "get_web_identity_token"),
    }
)
# AWS's official S3 mapping associates ListObjectsV2 with the S3 Object Lambda
# ListBucket action as well as regular S3 actions. The operation still uses the
# S3 SDK endpoint; AWS evaluates which action applies to the requested target.
# Keep this documented exception exact and require its own complete annotations.
_CROSS_SERVICE_AUTHORIZATION_ALLOWLIST = {
    ("s3", "list_objects_v2"): frozenset({"s3-object-lambda"}),
}
_SENSITIVE_FIELDS = frozenset(
    {
        "secret",
        "secretstring",
        "secretbinary",
        "secretaccesskey",
        "secretvalue",
        "password",
        "privatekey",
        "credentials",
        "sessiontoken",
        "securitytoken",
        "clientsecret",
        "accesstoken",
        "refreshtoken",
    }
)


class AwsReadCatalogError(ValueError):
    """Safe catalog/schema refusal with no provider response data attached."""


@dataclass(frozen=True, slots=True)
class AwsReadOperation:
    service: str
    operation: str
    api_name: str
    iam_actions: tuple[str, ...]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    protocol: str
    pagination: Mapping[str, Any] | None
    supported: bool
    unsupported_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AwsReadCatalogPage:
    items: tuple[AwsReadOperation, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AwsReadCatalog:
    digest: str
    sdk_version: str
    source: Mapping[str, Any]
    operations: tuple[AwsReadOperation, ...]

    def list_operations(
        self,
        *,
        service: str | None = None,
        operation: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> AwsReadCatalogPage:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise AwsReadCatalogError("catalog page limit must be between 1 and 50")
        if operation is not None and service is None:
            raise AwsReadCatalogError("service is required when looking up one operation")
        if cursor is not None and len(cursor) > 2048:
            raise AwsReadCatalogError("catalog cursor is invalid")
        selected = tuple(
            item
            for item in self.operations
            if (service is None or item.service == service)
            and (operation is None or item.operation == operation)
        )
        offset = _decode_cursor(cursor, self.digest, service, operation) if cursor else 0
        if offset > len(selected):
            raise AwsReadCatalogError("catalog cursor is outside the selected page scope")
        page = selected[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = (
            _encode_cursor(self.digest, service, operation, next_offset)
            if next_offset < len(selected)
            else None
        )
        return AwsReadCatalogPage(items=page, next_cursor=next_cursor)

    def get_operation(self, service: str, operation: str) -> AwsReadOperation:
        for item in self.operations:
            if item.service == service and item.operation == operation:
                return item
        raise AwsReadCatalogError("AWS operation is not present in the pinned catalog")


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key != "digest"}
    return _canonical_value_digest(content)


def _canonical_value_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _schema_digest(value: Any) -> str:
    hex_digest = _canonical_value_digest(value)
    encoded = base64.b64encode(bytes.fromhex(hex_digest)).decode("ascii")
    return f"sha256:b64:{encoded}"


def _decode_operation(raw: Mapping[str, Any], *, sdk_session) -> AwsReadOperation:
    required = {
        "service",
        "operation",
        "api_name",
        "iam_actions",
        "input_schema_digest",
        "output_schema_digest",
        "protocol",
        "pagination",
        "supported",
        "unsupported_reason",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise AwsReadCatalogError("catalog operation descriptor is incomplete")
    if any(not isinstance(raw[key], str) or not raw[key] for key in ("service", "operation", "api_name", "protocol")):
        raise AwsReadCatalogError("catalog operation identity is invalid")
    if not isinstance(raw["supported"], bool):
        raise AwsReadCatalogError("catalog support state is invalid")
    if raw["supported"] and raw["unsupported_reason"] is not None:
        raise AwsReadCatalogError("supported catalog operation has a refusal reason")
    if not raw["supported"] and not isinstance(raw["unsupported_reason"], str):
        raise AwsReadCatalogError("unsupported catalog operation has no refusal reason")
    actions = raw["iam_actions"]
    if not isinstance(actions, list) or any(not isinstance(action, str) for action in actions):
        raise AwsReadCatalogError("catalog action list is invalid")
    pagination = raw["pagination"]
    if pagination is not None and not isinstance(pagination, dict):
        raise AwsReadCatalogError("catalog pagination metadata is invalid")
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}
    if raw["supported"]:
        for key in ("input_schema_digest", "output_schema_digest"):
            value = raw[key]
            if not isinstance(value, str) or not value.startswith("sha256:b64:"):
                raise AwsReadCatalogError("catalog schema digest is invalid")
            try:
                decoded = base64.b64decode(value.removeprefix("sha256:b64:"), validate=True)
            except (ValueError, TypeError) as exc:
                raise AwsReadCatalogError("catalog schema digest is invalid") from exc
            if len(decoded) != 32:
                raise AwsReadCatalogError("catalog schema digest is invalid")
        try:
            service_model = sdk_session.get_service_model(raw["service"])
            operation_model = service_model.operation_model(raw["api_name"])
            input_schema = cap_page_input_schema(
                operation_schema(operation_model.input_shape),
                pagination,
            )
            output_schema = operation_schema(
                operation_model.output_shape,
                max_bytes=_MAX_OUTPUT_SCHEMA_BYTES,
            )
        except (AwsReadCatalogError, KeyError, ValueError, botocore.exceptions.BotoCoreError) as exc:
            raise AwsReadCatalogError("catalog SDK schema is incompatible") from exc
        if (
            _schema_digest(input_schema) != raw["input_schema_digest"]
            or _schema_digest(output_schema) != raw["output_schema_digest"]
        ):
            raise AwsReadCatalogError("catalog SDK schema digest does not match")
    elif raw["input_schema_digest"] is not None or raw["output_schema_digest"] is not None:
        raise AwsReadCatalogError("unsupported catalog operation has schema digests")
    return AwsReadOperation(
        service=raw["service"],
        operation=raw["operation"],
        api_name=raw["api_name"],
        iam_actions=tuple(actions),
        input_schema=input_schema,
        output_schema=output_schema,
        protocol=raw["protocol"],
        pagination=pagination,
        supported=raw["supported"],
        unsupported_reason=raw["unsupported_reason"],
    )


def load_aws_read_catalog(path: str | Path | None = None) -> AwsReadCatalog:
    """Load a checked-in catalog; runtime never fetches AWS metadata."""
    if path is None:
        return _packaged_catalog()
    catalog_path = Path(path) if path is not None else _DATA_PATH
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AwsReadCatalogError("AWS read catalog is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AwsReadCatalogError("AWS read catalog schema version is unsupported")
    if payload.get("schema_conversion_version") != _SCHEMA_CONVERSION_VERSION:
        raise AwsReadCatalogError("AWS read catalog schema conversion version is unsupported")
    if payload.get("sdk_version") != botocore.__version__:
        raise AwsReadCatalogError("AWS read catalog SDK version does not match Botocore")
    digest = payload.get("digest")
    if not isinstance(digest, str) or not hmac.compare_digest(digest, _canonical_digest(payload)):
        raise AwsReadCatalogError("AWS read catalog digest is invalid")
    source = payload.get("source")
    operations = payload.get("operations")
    if not isinstance(source, dict) or not isinstance(operations, list):
        raise AwsReadCatalogError("AWS read catalog is incomplete")
    fields = payload.get("operation_fields")
    if fields != list(_SNAPSHOT_OPERATION_FIELDS):
        raise AwsReadCatalogError("AWS read catalog operation fields are unsupported")
    sdk_session = botocore.session.get_session()
    decoded_items = []
    for row in operations:
        if not isinstance(row, list) or len(row) != len(fields):
            raise AwsReadCatalogError("AWS read catalog operation row is malformed")
        raw = dict(zip(fields, row, strict=True))
        decoded_items.append(_decode_operation(raw, sdk_session=sdk_session))
    decoded = tuple(decoded_items)
    keys = [(item.service, item.operation) for item in decoded]
    if keys != sorted(set(keys)):
        raise AwsReadCatalogError("AWS read catalog operations are not uniquely sorted")
    return AwsReadCatalog(
        digest=digest,
        sdk_version=payload["sdk_version"],
        source=source,
        operations=decoded,
    )


@lru_cache(maxsize=1)
def _packaged_catalog() -> AwsReadCatalog:
    return load_aws_read_catalog(_DATA_PATH)


def require_read_operation(service: str, operation: str) -> AwsReadOperation:
    descriptor = _packaged_catalog().get_operation(service, operation)
    if not descriptor.supported:
        raise AwsReadCatalogError("AWS operation is unsupported by the pinned read catalog")
    return descriptor


def classify_aws_operation(
    *,
    service: str,
    operation: Mapping[str, Any],
    actions: Mapping[tuple[str, str], Mapping[str, Any]],
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    protocol: str,
    pagination: Mapping[str, Any] | None = None,
    reference_service: str | None = None,
) -> AwsReadOperation:
    """Classify one official operation mapping; unknown metadata is refused."""
    api_name = operation.get("Name")
    sdk_methods = [
        entry
        for entry in operation.get("SDK", [])
        if entry.get("Package") == "Boto3" and entry.get("Name") == service
    ]
    method_names = {entry.get("Method") for entry in sdk_methods}
    sdk_operation = next(iter(method_names)) if len(method_names) == 1 else None
    action_refs = operation.get("AuthorizedActions")
    iam_actions: list[str] = []
    reason: str | None = None
    expected_prefix = reference_service or service

    if not isinstance(api_name, str) or not api_name:
        reason = "missing_api_name"
    elif not isinstance(sdk_operation, str) or not sdk_operation:
        reason = "missing_sdk_mapping"
    elif not isinstance(action_refs, list) or not action_refs:
        reason = "missing_authorized_action"
    else:
        for action_ref in action_refs:
            if not isinstance(action_ref, dict):
                reason = "invalid_authorized_action"
                break
            action_service = action_ref.get("Service")
            action_name = action_ref.get("Name")
            if not isinstance(action_service, str) or not isinstance(action_name, str):
                reason = "invalid_authorized_action"
                break
            allowed_cross_services = _CROSS_SERVICE_AUTHORIZATION_ALLOWLIST.get(
                (service.casefold(), str(sdk_methods[0].get("Method", "")).casefold()),
                frozenset(),
            )
            if action_service != expected_prefix and action_service not in allowed_cross_services:
                reason = "cross_service_authorization"
                break
            annotation = actions.get((action_service, action_name))
            if not isinstance(annotation, Mapping):
                reason = "missing_authorized_action"
                break
            if any(type(annotation.get(key)) is not bool for key in _ACTION_PROPERTIES):
                reason = "incomplete_action_annotations"
                break
            if annotation["IsWrite"] or annotation["IsPermissionManagement"] or annotation["IsTaggingOnly"]:
                reason = "operation_requires_write_action"
                break
            iam_actions.append(f"{action_service}:{action_name}")

    normalized_service = service.casefold()
    normalized_operation = sdk_operation.casefold() if sdk_operation else "unknown"
    if reason is None and (normalized_service, normalized_operation) in _SECRET_OPERATIONS:
        reason = "application_secret_operation"
    if reason is None and (normalized_service, normalized_operation) in _CREDENTIAL_OPERATIONS:
        reason = "credential_vending_operation"
    if reason is None and protocol not in _SAFE_PROTOCOLS:
        reason = "unsupported_protocol"
    if reason is None and (
        _contains_catalog_unsupported_shape(input_schema)
        or _contains_catalog_unsupported_shape(output_schema)
    ):
        reason = "unsupported_binary_shape"
    if reason is None:
        try:
            _check_bounded_schema(input_schema, max_bytes=_MAX_SCHEMA_BYTES)
            _check_bounded_schema(output_schema, max_bytes=_MAX_OUTPUT_SCHEMA_BYTES)
        except AwsReadCatalogError:
            reason = "unsupported_schema_expansion"

    return AwsReadOperation(
        service=service,
        operation=sdk_operation or "unknown",
        api_name=api_name if isinstance(api_name, str) else "unknown",
        iam_actions=tuple(sorted(set(iam_actions))),
        input_schema=input_schema,
        output_schema=output_schema,
        protocol=protocol,
        pagination=pagination,
        supported=reason is None,
        unsupported_reason=reason,
    )


def _contains_catalog_unsupported_shape(schema: Mapping[str, Any]) -> bool:
    if isinstance(schema, dict):
        if schema.get("x-openship-unsupported"):
            return True
        return any(_contains_catalog_unsupported_shape(item) for item in schema.values())
    if isinstance(schema, list):
        return any(_contains_catalog_unsupported_shape(item) for item in schema)
    return False


def operation_schema(
    shape: Shape | None,
    *,
    max_bytes: int = _MAX_SCHEMA_BYTES,
) -> dict[str, Any]:
    if shape is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    definitions: dict[str, Any] = {}
    result = _shape_schema(
        shape,
        ancestors=frozenset(),
        definitions=definitions,
        depth=0,
        root=True,
    )
    if definitions:
        result["$defs"] = dict(sorted(definitions.items()))
    _check_bounded_schema(result, max_bytes=max_bytes)
    return result


def cap_page_input_schema(
    schema: dict[str, Any],
    pagination: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply OpenShip's one-page ceiling to Botocore paginator limit fields."""
    if not pagination:
        return schema
    limit_keys = pagination.get("limit_key", ())
    if isinstance(limit_keys, str):
        limit_keys = (limit_keys,)
    for key_path in limit_keys:
        if not isinstance(key_path, str):
            continue
        current = schema
        parts = key_path.split(".")
        for part in parts[:-1]:
            current = current.get("properties", {}).get(part, {})
            if not isinstance(current, dict):
                break
        else:
            field_schema = current.get("properties", {}).get(parts[-1])
            if isinstance(field_schema, dict) and isinstance(field_schema.get("$ref"), str):
                reference = field_schema["$ref"]
                if reference.startswith("#/$defs/"):
                    field_schema = schema.get("$defs", {}).get(
                        reference.removeprefix("#/$defs/"), field_schema
                    )
            if isinstance(field_schema, dict) and field_schema.get("type") == "integer":
                field_schema["maximum"] = min(
                    field_schema.get("maximum", _MAX_PAGE_SIZE),
                    _MAX_PAGE_SIZE,
                )
                field_schema["minimum"] = max(field_schema.get("minimum", 1), 1)
    return schema


def _shape_schema(
    shape: Shape,
    *,
    ancestors: frozenset[int],
    definitions: dict[str, Any],
    depth: int,
    root: bool = False,
) -> dict[str, Any]:
    if depth > _MAX_SCHEMA_DEPTH:
        raise AwsReadCatalogError("unsupported schema depth")
    identity = id(shape)
    if identity in ancestors:
        raise AwsReadCatalogError("unsupported recursive schema")
    shape_name = shape.name
    if not root and shape_name in definitions:
        return {"$ref": f"#/$defs/{shape_name}"}
    if not root:
        definitions[shape_name] = {}
    ancestors = ancestors | {identity}
    shape_type = shape.type_name
    schema: dict[str, Any] = {}
    if shape_type == "structure":
        members = getattr(shape, "members", {})
        required = list(getattr(shape, "required_members", ()))
        if getattr(shape, "metadata", {}).get("union"):
            choices = []
            for member_name, member in sorted(members.items()):
                member_schema = _shape_schema(
                    member,
                    ancestors=ancestors,
                    definitions=definitions,
                    depth=depth + 1,
                )
                if _sensitive_output_field(member_name, member):
                    member_schema = dict(member_schema)
                    member_schema["x-openship-sensitive"] = True
                choices.append(
                    {
                        "type": "object",
                        "properties": {member_name: member_schema},
                        "required": [member_name],
                        "additionalProperties": False,
                    }
                )
            schema = {"oneOf": choices}
        else:
            properties = {}
            for member_name, member in sorted(members.items()):
                member_schema = _shape_schema(
                    member,
                    ancestors=ancestors,
                    definitions=definitions,
                    depth=depth + 1,
                )
                if _sensitive_output_field(member_name, member):
                    member_schema = dict(member_schema)
                    member_schema["x-openship-sensitive"] = True
                properties[member_name] = member_schema
            schema = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                schema["required"] = sorted(required)
    elif shape_type == "list":
        schema = {
            "type": "array",
            "items": _shape_schema(
                shape.member,
                ancestors=ancestors,
                definitions=definitions,
                depth=depth + 1,
            ),
        }
    elif shape_type == "map":
        schema = {
            "type": "object",
            "additionalProperties": _shape_schema(
                shape.value,
                ancestors=ancestors,
                definitions=definitions,
                depth=depth + 1,
            ),
        }
    elif shape_type in {"string", "character", "enum"}:
        schema = {"type": "string"}
        if shape.enum:
            schema["enum"] = sorted(set(shape.enum))
        _copy_bound(shape, schema, "min", "minLength")
        _copy_bound(shape, schema, "max", "maxLength")
    elif shape_type in {"integer", "long", "short", "byte"}:
        schema = {"type": "integer"}
        _copy_bound(shape, schema, "min", "minimum")
        _copy_bound(shape, schema, "max", "maximum")
    elif shape_type in {"float", "double", "bigdecimal"}:
        schema = {"type": "number"}
        _copy_bound(shape, schema, "min", "minimum")
        _copy_bound(shape, schema, "max", "maximum")
    elif shape_type == "boolean":
        schema = {"type": "boolean"}
    elif shape_type == "timestamp":
        schema = {"type": "string", "format": "date-time"}
    elif shape_type == "blob":
        schema = {"type": "string", "contentEncoding": "base64", "x-openship-unsupported": "binary"}
    elif shape_type == "document":
        schema = {"type": ["object", "array", "string", "number", "boolean", "null"]}
    else:
        raise AwsReadCatalogError("unsupported Botocore shape type")

    metadata = getattr(shape, "metadata", {})
    collection_bounds = (
        (("min", "minProperties"), ("max", "maxProperties"))
        if shape_type == "map"
        else (("min", "minItems"), ("max", "maxItems"))
    )
    if shape_type in {"list", "map"}:
        for model_key, schema_key in collection_bounds:
            if isinstance(metadata.get(model_key), int):
                schema[schema_key] = metadata[model_key]
    if "enum" in metadata and shape_type not in {"string", "character", "enum"}:
        schema["enum"] = sorted(set(metadata["enum"]))
    if metadata.get("sensitive") is True:
        schema["x-openship-sensitive"] = True
    if root:
        return schema
    definitions[shape_name] = schema
    return {"$ref": f"#/$defs/{shape_name}"}


def _sensitive_output_field(name: str, shape: Shape) -> bool:
    normalized = name.casefold().replace("_", "")
    return shape.metadata.get("sensitive") is True or normalized in _SENSITIVE_FIELDS or normalized.endswith(
        (
            "secretstring",
            "secretbinary",
            "secretaccesskey",
            "secretvalue",
            "password",
            "privatekey",
            "sessiontoken",
            "securitytoken",
            "clientsecret",
            "accesstoken",
            "refreshtoken",
        )
    )


def _copy_bound(shape: Shape, schema: dict[str, Any], metadata_key: str, schema_key: str) -> None:
    value = getattr(shape, "metadata", {}).get(metadata_key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        schema[schema_key] = value


def _check_bounded_schema(
    schema: Mapping[str, Any],
    *,
    max_bytes: int = _MAX_SCHEMA_BYTES,
) -> None:
    try:
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    except (TypeError, ValueError) as exc:
        raise AwsReadCatalogError("unsupported schema encoding") from exc
    if len(encoded) > max_bytes:
        raise AwsReadCatalogError("unsupported schema expansion")


def _encode_cursor(digest: str, service: str | None, operation: str | None, offset: int) -> str:
    raw = json.dumps([digest, service, operation, offset], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None, digest: str, service: str | None, operation: str | None) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        saved_digest, saved_service, saved_operation, offset = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AwsReadCatalogError("catalog cursor is invalid") from exc
    if (
        saved_digest != digest
        or saved_service != service
        or saved_operation != operation
        or type(offset) is not int
        or offset < 0
    ):
        raise AwsReadCatalogError("catalog cursor scope is invalid")
    return offset


def validate_parameters(descriptor: AwsReadOperation, parameters: Mapping[str, Any]) -> None:
    if not descriptor.supported:
        raise AwsReadCatalogError("AWS operation is unsupported by the pinned read catalog")
    if not isinstance(parameters, Mapping):
        raise AwsReadCatalogError("AWS parameters must be an object")
    if len(json.dumps(parameters, separators=(",", ":"), default=str).encode()) > _MAX_PARAMETER_BYTES:
        raise AwsReadCatalogError("AWS parameters exceed the allowed size")
    _validate_schema_value(
        descriptor.input_schema,
        descriptor.input_schema,
        parameters,
        path=(),
        depth=0,
    )


def _validate_schema_value(
    root_schema: Mapping[str, Any],
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: tuple[str, ...],
    depth: int,
) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise AwsReadCatalogError("AWS parameters exceed the allowed nesting depth")
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definition_name = reference.removeprefix("#/$defs/")
        definitions = root_schema.get("$defs", {})
        definition = definitions.get(definition_name) if isinstance(definitions, Mapping) else None
        if not isinstance(definition, Mapping):
            raise AwsReadCatalogError("AWS parameter schema reference is invalid")
        _validate_schema_value(
            root_schema,
            definition,
            value,
            path=path,
            depth=depth + 1,
        )
        return
    if "oneOf" in schema:
        if not any(
            _schema_matches(root_schema, choice, value, path=path, depth=depth + 1)
            for choice in schema["oneOf"]
        ):
            raise AwsReadCatalogError("AWS parameters do not match a supported union shape")
        return
    if "type" in schema and not _matches_type(schema["type"], value):
        raise AwsReadCatalogError("AWS parameter type is invalid")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise AwsReadCatalogError("AWS parameter value is not an allowed enum member")
    if isinstance(value, dict):
        _check_numeric_bound(len(value), schema.get("minProperties"), minimum=True)
        _check_numeric_bound(len(value), schema.get("maxProperties"), minimum=False)
        properties = schema.get("properties", {})
        unknown = set(value) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise AwsReadCatalogError("AWS parameters contain an unknown parameter")
        required = schema.get("required", ())
        if any(key not in value for key in required):
            raise AwsReadCatalogError("AWS parameters are missing a required parameter")
        if isinstance(properties, dict):
            for key, item in value.items():
                child_schema = properties.get(key, schema.get("additionalProperties"))
                if isinstance(child_schema, dict):
                    _validate_schema_value(
                        root_schema,
                        child_schema,
                        item,
                        path=(*path, key),
                        depth=depth + 1,
                    )
    elif isinstance(value, list):
        _check_numeric_bound(len(value), schema.get("minItems"), minimum=True)
        _check_numeric_bound(len(value), schema.get("maxItems"), minimum=False)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                _validate_schema_value(
                    root_schema,
                    item_schema,
                    item,
                    path=path,
                    depth=depth + 1,
                )
    elif isinstance(value, str):
        _check_numeric_bound(len(value), schema.get("minLength"), minimum=True)
        _check_numeric_bound(len(value), schema.get("maxLength"), minimum=False)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _check_numeric_bound(value, schema.get("minimum"), minimum=True)
        _check_numeric_bound(value, schema.get("maximum"), minimum=False)


def _schema_matches(
    root_schema: Mapping[str, Any],
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: tuple[str, ...],
    depth: int,
) -> bool:
    try:
        _validate_schema_value(root_schema, schema, value, path=path, depth=depth)
        return True
    except AwsReadCatalogError:
        return False


def _matches_type(expected: Any, value: Any) -> bool:
    types = [expected] if isinstance(expected, str) else expected
    matches = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return any(kind in matches and matches[kind](value) for kind in types)


def _check_numeric_bound(value: float, bound: Any, *, minimum: bool) -> None:
    if bound is None:
        return
    invalid = value < bound if minimum else value > bound
    if invalid:
        raise AwsReadCatalogError("AWS parameter is below its minimum" if minimum else "AWS parameter exceeds its maximum")


def descriptor_from_sdk_operation(service: str, api_name: str) -> AwsReadOperation:
    """Convenience lookup for callers that already selected a catalog operation."""
    session = botocore.session.get_session()
    model = session.get_service_model(service)
    method = model.operation_model(api_name).name
    return require_read_operation(service, _method_name(method))


def _method_name(api_name: str) -> str:
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", api_name).lower()


__all__ = [
    "AwsReadCatalog",
    "AwsReadCatalogError",
    "AwsReadCatalogPage",
    "AwsReadOperation",
    "classify_aws_operation",
    "descriptor_from_sdk_operation",
    "load_aws_read_catalog",
    "operation_schema",
    "require_read_operation",
    "validate_parameters",
]
