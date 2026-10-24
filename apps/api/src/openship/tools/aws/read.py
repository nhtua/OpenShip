"""Catalog-pinned, binding-free AWS reads for the managed AWS connector."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import ConnectorRef, canonical_json
from src.openship.connectors.aws.read_catalog import (
    AwsReadCatalogError,
    AwsReadOperation,
    load_aws_read_catalog,
    require_read_operation,
    validate_parameters,
)
from src.openship.connectors.credentials.broker import ConnectorCredentialError
from src.openship.connectors.credentials.dependencies import get_idempotency_keyring
from src.openship.connectors.errors import CredentialStoreUnavailable
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.execution.aws_transport import AwsSdkError
from src.openship.tools.aws.contracts import (
    AwsReadArguments,
    AwsReadObservation,
    AwsReadResult,
    AwsToolError,
)

_REGION = re.compile(
    r"^(?:(?:af|ap|ca|eu|il|me|mx|sa)-[a-z0-9-]+-\d|"
    r"us-(?!gov-|iso-|isob-|isof-|isoeast-)[a-z0-9-]+-\d)$"
)
_CURSOR_NAMESPACE = "openship.aws.read.cursor.v1"
_CURSOR_TTL = timedelta(minutes=30)
_CURSOR_MAX_BYTES = 4096
_EVIDENCE_MAX_BYTES = 64 * 1024
_PREVIEW_MAX_BYTES = 16 * 1024
_SENSITIVE_NAMES = frozenset(
    {
        "secret", "secretstring", "secretbinary", "secretaccesskey", "secretvalue",
        "password", "privatekey", "credentials", "sessiontoken", "securitytoken",
        "clientsecret", "accesstoken", "refreshtoken",
    }
)


def _resolve_connector_configuration(context, reference: ConnectorRef):
    """Read the exact project connector revision pinned into this execution."""
    if not isinstance(reference, ConnectorRef) or reference.kind != "aws":
        raise ConnectorCredentialError("connector_scope_denied")
    factory = context.session_factory
    if factory is None:
        raise ConnectorCredentialError("connector_unavailable")
    with factory() as db:
        connector = (
            db.query(Connector)
            .filter(
                Connector.id == reference.id,
                Connector.kind == "aws",
                Connector.visibility == "project",
                Connector.project_id == context.authorization.project_id,
                Connector.enabled.is_(True),
                Connector.revoked_at.is_(None),
            )
            .first()
        )
        revision = (
            db.query(ConnectorRevision)
            .filter(
                ConnectorRevision.connector_id == reference.id,
                ConnectorRevision.revision == reference.configuration_revision,
                ConnectorRevision.credential_version_id == reference.credential_version_id,
                ConnectorRevision.visibility == "project",
                ConnectorRevision.project_id == context.authorization.project_id,
            )
            .first()
        )
        credential = (
            db.query(CredentialVersion)
            .filter(
                CredentialVersion.id == reference.credential_version_id,
                CredentialVersion.connector_id == reference.id,
                CredentialVersion.visibility == "project",
                CredentialVersion.project_id == context.authorization.project_id,
                CredentialVersion.kind == "aws_bootstrap",
                CredentialVersion.state == "active",
                CredentialVersion.revoked_at.is_(None),
            )
            .first()
        )
        if connector is None or revision is None or credential is None:
            raise ConnectorCredentialError("connector_scope_denied")
        try:
            from src.openship.connectors.contracts import ConnectorConfiguration

            configuration = ConnectorConfiguration.model_validate_json(
                revision.configuration_json
            )
        except (TypeError, ValueError):
            raise ConnectorCredentialError("connector_unavailable") from None
        if configuration.kind != "aws":
            raise ConnectorCredentialError("connector_scope_denied")
        return configuration


async def execute_aws_read(
    arguments: AwsReadArguments,
    context,
    control,
) -> AwsReadObservation:
    """Run one pinned, catalog-approved AWS read using only the basic role."""
    catalog = load_aws_read_catalog()
    execution = context.execution
    if getattr(execution, "aws_read_catalog_digest", None) != catalog.digest:
        raise AwsToolError("aws_catalog_mismatch")
    tool_refs = [ref for ref in execution.allowed_capabilities if ref.id == "aws_read"]
    if len(tool_refs) != 1:
        raise AwsToolError("aws_resource_unavailable")
    references = [
        ref
        for ref in execution.connector_refs
        if ref.kind == "aws"
        and (arguments.connector_id is None or ref.id == arguments.connector_id)
    ]
    if len(references) != 1:
        raise AwsToolError("aws_resource_unavailable")
    reference = references[0]

    try:
        descriptor = require_read_operation(arguments.service, arguments.operation)
        if descriptor.protocol not in {
            "json", "json-rpc", "query", "ec2", "rest-json", "rest-xml",
            "smithy-rpc-v2-cbor",
        }:
            raise AwsToolError("aws_unsupported_protocol")
    except AwsReadCatalogError:
        raise AwsToolError("aws_operation_not_supported") from None

    configuration = _resolve_connector_configuration(context, reference)
    region = arguments.region or configuration.region
    if not isinstance(region, str) or _REGION.fullmatch(region) is None:
        raise AwsToolError("aws_invalid_parameters")

    parameters = dict(arguments.parameters)
    pagination = descriptor.pagination or {}
    input_tokens = _token_keys(pagination.get("input_token"))
    limit_keys = _token_keys(pagination.get("limit_key"))
    if arguments.cursor is None and any(key in parameters for key in input_tokens):
        raise AwsToolError("aws_invalid_parameters")
    for key in input_tokens + limit_keys:
        parameters.pop(key, None)
    parameter_digest = hashlib.sha256(canonical_json(parameters)).hexdigest()

    provider_token = None
    if arguments.cursor is not None:
        try:
            cursor = _decode_cursor(arguments.cursor)
        except (ValueError, TypeError):
            raise AwsToolError("aws_invalid_cursor") from None
        expected_scope = {
            "namespace": _CURSOR_NAMESPACE,
            "project_id": str(context.authorization.project_id),
            "connector_id": str(reference.id),
            "connector_revision": reference.configuration_revision,
            "catalog_digest": catalog.digest,
            "service": descriptor.service,
            "operation": descriptor.operation,
            "region": region,
            "parameters_digest": parameter_digest,
        }
        if any(cursor.get(key) != value for key, value in expected_scope.items()):
            raise AwsToolError("aws_invalid_cursor")
        try:
            expires_at = datetime.fromisoformat(cursor["expires_at"])
            if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
                raise ValueError
            provider_token = cursor["provider_token"]
        except (KeyError, TypeError, ValueError):
            raise AwsToolError("aws_invalid_cursor") from None

    for key in input_tokens:
        if provider_token is not None:
            if isinstance(provider_token, Mapping):
                if key in provider_token:
                    parameters[key] = provider_token[key]
            elif len(input_tokens) == 1:
                parameters[key] = provider_token
    if limit_keys:
        if len(limit_keys) != 1:
            raise AwsToolError("aws_operation_not_supported")
        parameters[limit_keys[0]] = arguments.page_size
    try:
        validate_parameters(descriptor, parameters)
    except AwsReadCatalogError:
        raise AwsToolError("aws_invalid_parameters") from None

    try:
        control.raise_if_stopped()
        access = await context.aws_access_factory(reference)
    except AwsSdkError as exc:
        raise _tool_error(exc.code) from None

    try:
        client = access.client(descriptor.service, region=region)
        method = getattr(client, descriptor.operation)
        response = await access.run(method, descriptor=descriptor, **parameters)
        if not isinstance(response, Mapping):
            raise AwsToolError("aws_invalid_response")
        cleaned, raw_tokens, has_more = _clean_response(response, descriptor)
        encoded = canonical_json(cleaned)
        if len(encoded) > _EVIDENCE_MAX_BYTES:
            raise AwsToolError("aws_output_limit_exceeded")
        next_cursor = None
        if has_more:
            if raw_tokens is None:
                raise AwsToolError("aws_invalid_response")
            next_cursor = _encode_cursor(
                {
                    "namespace": _CURSOR_NAMESPACE,
                    "project_id": str(context.authorization.project_id),
                    "connector_id": str(reference.id),
                    "connector_revision": reference.configuration_revision,
                    "catalog_digest": catalog.digest,
                    "service": descriptor.service,
                    "operation": descriptor.operation,
                    "region": region,
                    "parameters_digest": parameter_digest,
                    "provider_token": raw_tokens,
                    "expires_at": (datetime.now(UTC) + _CURSOR_TTL).isoformat(),
                }
            )
        request_budget = getattr(access, "request_budget", None)
        request_count = getattr(request_budget, "used", 1)
        request_count = max(1, min(8, request_count))
        outcome = "partial" if next_cursor else ("no_data" if not cleaned else "measured")
        return AwsReadObservation(
            tool=tool_refs[0],
            operation_id=context.operation_id,
            project_id=context.authorization.project_id,
            connector_id=reference.id,
            connector_revision=reference.configuration_revision,
            catalog_digest=catalog.digest,
            account_id=configuration.expected_account_id,
            service=descriptor.service,
            operation=descriptor.operation,
            region=region,
            target={"parameters": _normalize_value({k: v for k, v in arguments.parameters.items()})},
            retrieved_at=datetime.now(UTC),
            request_count=request_count,
            data=cleaned,
            outcome=outcome,
            coverage={"has_more": next_cursor is not None},
            next_cursor=next_cursor,
        )
    except AwsToolError:
        raise
    except AwsSdkError as exc:
        raise _tool_error(exc.code) from None
    finally:
        await access.aclose()


def project_aws_read_result(
    observation: AwsReadObservation,
    artifact_ref: ArtifactRef,
) -> AwsReadResult:
    """Build the compact model result without changing the signed continuation."""
    preview = observation.data
    truncated = len(canonical_json(preview)) > _PREVIEW_MAX_BYTES
    if truncated:
        preview = _short_preview(preview)
    while True:
        result = AwsReadResult(
            tool=observation.tool,
            operation_id=observation.operation_id,
            connector_id=observation.connector_id,
            connector_revision=observation.connector_revision,
            catalog_digest=observation.catalog_digest,
            account_id=observation.account_id,
            service=observation.service,
            operation=observation.operation,
            region=observation.region,
            retrieved_at=observation.retrieved_at,
            outcome=observation.outcome,
            preview=preview,
            preview_truncated=truncated,
            next_cursor=observation.next_cursor,
            artifact=artifact_ref,
        )
        if len(canonical_json(result.model_dump(mode="json"))) <= _PREVIEW_MAX_BYTES:
            return result
        truncated = True
        reduced = _shrink_preview(preview)
        if reduced == preview:
            preview = {"summary": "Full AWS evidence is available in the attached artifact."}
        else:
            preview = reduced


def _tool_error(code: str) -> AwsToolError:
    allowed = AwsToolError._CODES
    return AwsToolError(code if code in allowed else "aws_unavailable")


def _token_keys(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


def _encode_cursor(payload: dict[str, Any]) -> str:
    try:
        keyring = get_idempotency_keyring()
        body = canonical_json(payload)
        key_id, signature = keyring.digest(body)
        envelope = {"kid": key_id, "payload": payload, "signature": signature}
        token = base64.urlsafe_b64encode(canonical_json(envelope)).decode().rstrip("=")
    except (CredentialStoreUnavailable, TypeError, ValueError):
        raise AwsToolError("aws_configuration_error") from None
    if len(token) > _CURSOR_MAX_BYTES:
        raise AwsToolError("aws_output_limit_exceeded")
    return token


def _decode_cursor(token: str) -> dict[str, Any]:
    if not isinstance(token, str) or not token or len(token) > _CURSOR_MAX_BYTES:
        raise TypeError("invalid cursor")
    padded = token + "=" * (-len(token) % 4)
    envelope = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    if not isinstance(envelope, dict) or set(envelope) != {"kid", "payload", "signature"}:
        raise ValueError("invalid cursor")
    payload = envelope["payload"]
    if not isinstance(payload, dict) or not isinstance(envelope["kid"], str):
        raise TypeError("invalid cursor")
    body = canonical_json(payload)
    try:
        keyring = get_idempotency_keyring()
        valid = keyring.verify(envelope["kid"], body, envelope["signature"])
    except CredentialStoreUnavailable:
        raise ValueError("invalid cursor") from None
    if not valid:
        raise ValueError("invalid cursor")
    return payload


def _clean_response(response: Mapping[str, Any], descriptor: AwsReadOperation):
    pagination = descriptor.pagination or {}
    output_tokens = _token_keys(pagination.get("output_token"))
    raw_tokens = None
    if output_tokens:
        values = [response.get(key) for key in output_tokens]
        if len(output_tokens) == 1 and values[0] is not None:
            raw_tokens = values[0]
        elif len(output_tokens) > 1:
            paired_tokens = {
                input_key: token
                for input_key, token in zip(_token_keys(pagination.get("input_token")), values)
                if token is not None
            }
            raw_tokens = paired_tokens or None
    more_key = pagination.get("more_results")
    has_more = bool(response.get(more_key)) if isinstance(more_key, str) else raw_tokens is not None
    if not has_more:
        raw_tokens = None
    output_schema = descriptor.output_schema
    cleaned = _normalize_value(
        response,
        schema=output_schema,
        root_schema=output_schema,
        omitted_keys=frozenset({"ResponseMetadata", *output_tokens}),
    )
    if not isinstance(cleaned, dict):
        raise AwsToolError("aws_invalid_response")
    return cleaned, raw_tokens, has_more


def _normalize_value(value, *, schema=None, root_schema=None, omitted_keys=frozenset()):
    if isinstance(value, Mapping):
        properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or key in omitted_keys or key.lower() == "responsemetadata":
                continue
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            member_schema = properties.get(key, {}) if isinstance(properties, Mapping) else {}
            resolved_schema = _resolve_schema(member_schema, root_schema)
            if (
                _is_sensitive_name(normalized)
                or (isinstance(member_schema, Mapping) and member_schema.get("x-openship-sensitive"))
                or (isinstance(resolved_schema, Mapping) and resolved_schema.get("x-openship-sensitive"))
            ):
                continue
            result[key] = _normalize_value(
                item,
                schema=resolved_schema,
                root_schema=root_schema,
                omitted_keys=omitted_keys,
            )
        return result
    if isinstance(value, (list, tuple)):
        item_schema = schema.get("items", {}) if isinstance(schema, Mapping) else {}
        return [
            _normalize_value(item, schema=item_schema, root_schema=root_schema, omitted_keys=omitted_keys)
            for item in value
        ]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise AwsToolError("aws_invalid_response")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AwsToolError("aws_invalid_response")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise AwsToolError("aws_invalid_response")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise AwsToolError("aws_unsupported_output")
    raise AwsToolError("aws_invalid_response")


def _resolve_schema(schema, root_schema):
    if not isinstance(schema, Mapping):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/") and isinstance(root_schema, Mapping):
        return root_schema.get("$defs", {}).get(reference.removeprefix("#/$defs/"), {})
    return schema


def _is_sensitive_name(value: str) -> bool:
    return (
        value in _SENSITIVE_NAMES
        or value.endswith(("password", "secret", "secretkey", "sessiontoken"))
    )


def _short_preview(value):
    if isinstance(value, dict):
        return {key: _short_preview(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_short_preview(item) for item in value[:8]]
    if isinstance(value, str) and len(value) > 512:
        return value[:509] + "..."
    return value


def _shrink_preview(value):
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > 16:
            items = items[: max(1, len(items) // 2)]
        return {key: _shrink_preview(item) for key, item in items}
    if isinstance(value, list):
        return [_shrink_preview(item) for item in value[: max(1, len(value) // 2)]]
    if isinstance(value, str) and len(value) > 64:
        return value[: len(value) // 2] + "..."
    return value
