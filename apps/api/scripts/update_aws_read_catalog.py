#!/usr/bin/env python3
"""Regenerate the pinned AWS read catalog from AWS SAR and Botocore models.

This developer command is the only catalog component that accesses the network.
Runtime catalog lookup reads the checked-in JSON snapshot and never downloads
or refreshes operation metadata.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import botocore
import botocore.session
from botocore.client import xform_name

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src.openship.connectors.aws.read_catalog import (
    _MAX_OUTPUT_SCHEMA_BYTES,
    _SCHEMA_CONVERSION_VERSION,
    _SNAPSHOT_OPERATION_FIELDS,
    AwsReadOperation,
    _canonical_digest,
    _schema_digest,
    cap_page_input_schema,
    classify_aws_operation,
    operation_schema,
)

MAPPING_URL = "https://servicereference.us-east-1.amazonaws.com/v1/mapping.json"
SERVICE_INDEX_URL = "https://servicereference.us-east-1.amazonaws.com/"
EXPECTED_HOST = "servicereference.us-east-1.amazonaws.com"
OUTPUT_PATH = (
    APP_ROOT
    / "src"
    / "openship"
    / "connectors"
    / "aws"
    / "data"
    / "read_operations.v1.json"
)


def _fetch_json(url: str) -> Any:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != EXPECTED_HOST:
        raise ValueError("catalog source must use the official AWS Service Reference host")
    request = Request(url, headers={"User-Agent": "OpenShip AWS read catalog updater"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _service_references(
    mapping: dict[str, Any], service_index: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, str]]:
    boto3 = mapping.get("SDK", {}).get("Python", {}).get("Boto3", {})
    if not isinstance(boto3, dict):
        raise TypeError("AWS mapping has no Python Boto3 service index")
    session = botocore.session.get_session()
    available = set(session.get_available_services())
    sdk_mapping = {name: entry for name, entry in boto3.items() if name in available}
    urls: dict[str, str] = {}
    for entry in sdk_mapping.values():
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            urls[entry["url"]] = entry.get("service", "")
    for entry in service_index:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            urls[entry["url"]] = entry.get("service", "")
    return sdk_mapping, urls


def _download_references(urls: dict[str, str]) -> dict[str, dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fetch_json, url): url for url in urls}
        results = {}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            results[url] = future.result()
    return results


def _action_index(references: dict[str, dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for reference in references.values():
        service_prefix = reference.get("Name")
        if not isinstance(service_prefix, str):
            continue
        for action in reference.get("Actions", []):
            if not isinstance(action, dict) or not isinstance(action.get("Name"), str):
                continue
            properties = action.get("Annotations", {}).get("Properties", {})
            key = (service_prefix, action["Name"])
            previous = index.get(key)
            if previous is not None and previous != properties:
                raise ValueError("AWS reference contains conflicting action annotations")
            index[key] = properties
    return index


def _operation_mappings(
    references: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[dict[str, Any], str]]]:
    mappings: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
    for reference in references.values():
        reference_service = reference.get("Name")
        for operation in reference.get("Operations", []):
            if not isinstance(operation, dict):
                continue
            for sdk in operation.get("SDK", []):
                if sdk.get("Package") == "Boto3" and isinstance(sdk.get("Name"), str) and isinstance(sdk.get("Method"), str):
                    mappings.setdefault((sdk["Name"], sdk["Method"]), []).append(
                        (operation, reference_service)
                    )
    return mappings


def _paginators(session, service: str) -> dict[str, dict[str, Any]]:
    try:
        paginator_model = session.get_component("data_loader").load_service_model(
            service, "paginators-1"
        )
    except botocore.exceptions.DataNotFoundError:
        return {}
    result = {}
    for api_name, config in paginator_model.get("pagination", {}).items():
        result[api_name] = {
            key: config[key]
            for key in ("input_token", "output_token", "limit_key", "result_key", "more_results")
            if key in config
        }
    return result


def _unsupported_descriptor(
    service: str,
    method: str,
    api_name: str,
    protocol: str,
    reason: str,
) -> AwsReadOperation:
    return AwsReadOperation(
        service=service,
        operation=method,
        api_name=api_name,
        iam_actions=(),
        input_schema={},
        output_schema={},
        protocol=protocol,
        pagination=None,
        supported=False,
        unsupported_reason=reason,
    )


def build_catalog() -> dict[str, Any]:
    mapping = _fetch_json(MAPPING_URL)
    service_index = _fetch_json(SERVICE_INDEX_URL)
    if not isinstance(service_index, list):
        raise TypeError("AWS service index has an unexpected format")
    sdk_mapping, urls = _service_references(mapping, service_index)
    references = _download_references(urls)
    actions = _action_index(references)
    operation_mappings = _operation_mappings(references)
    session = botocore.session.get_session()
    descriptors: list[AwsReadOperation] = []

    for service in sorted(sdk_mapping):
        service_entry = sdk_mapping[service]
        reference_service = service_entry.get("service", "")
        model = session.get_service_model(service)
        protocol = model.metadata.get("protocol", "unknown")
        pagination = _paginators(session, service)
        api_by_method = {xform_name(name): name for name in model.operation_names}
        for method, api_name in sorted(api_by_method.items()):
            matches = operation_mappings.get((service, method), [])
            if len(matches) != 1:
                descriptors.append(
                    _unsupported_descriptor(
                        service,
                        method,
                        api_name,
                        protocol,
                        "missing_operation_mapping" if not matches else "ambiguous_operation_mapping",
                    )
                )
                continue
            operation, operation_service = matches[0]
            if operation_service != reference_service:
                descriptors.append(
                    _unsupported_descriptor(
                        service, method, api_name, protocol, "cross_service_operation_mapping"
                    )
                )
                continue
            operation_model = model.operation_model(api_name)
            schema_error = None
            operation_pagination = pagination.get(api_name)
            try:
                input_schema = operation_schema(operation_model.input_shape)
                output_schema = operation_schema(
                    operation_model.output_shape,
                    max_bytes=_MAX_OUTPUT_SCHEMA_BYTES,
                )
                input_schema = cap_page_input_schema(input_schema, operation_pagination)
            except ValueError:
                input_schema = {}
                output_schema = {}
                schema_error = "unsupported_schema_expansion"
            descriptor = classify_aws_operation(
                service=service,
                operation=operation,
                actions=actions,
                input_schema=input_schema,
                output_schema=output_schema,
                protocol=protocol,
                pagination=operation_pagination,
                reference_service=reference_service,
            )
            if schema_error and descriptor.supported:
                descriptor = replace(
                    descriptor,
                    supported=False,
                    unsupported_reason=schema_error,
                )
            if not descriptor.supported:
                descriptor = replace(descriptor, input_schema={}, output_schema={})
            descriptors.append(descriptor)

    descriptors.sort(key=lambda item: (item.service, item.operation))
    payload = {
        "schema_version": 1,
        "sdk_version": botocore.__version__,
        "schema_conversion_version": _SCHEMA_CONVERSION_VERSION,
        "operation_fields": list(_SNAPSHOT_OPERATION_FIELDS),
        "source": {
            "mapping_url": MAPPING_URL,
            "service_index_url": SERVICE_INDEX_URL,
            "service_reference_urls": sorted(urls),
            "service_reference_versions": {
                reference.get("Name", "unknown"): reference.get("Version", "unknown")
                for reference in sorted(references.values(), key=lambda item: item.get("Name", ""))
            },
        },
        "operations": [
            [
                item.service,
                item.operation,
                item.api_name,
                list(item.iam_actions),
                _schema_digest(item.input_schema) if item.supported else None,
                _schema_digest(item.output_schema) if item.supported else None,
                item.protocol,
                item.pagination,
                item.supported,
                item.unsupported_reason,
            ]
            for item in descriptors
        ],
    }
    payload["digest"] = _canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    payload = build_catalog()
    metadata = {key: value for key, value in payload.items() if key != "operations"}
    header = json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=True).rstrip()
    rows = ",\n".join(
        "    " + json.dumps(row, separators=(",", ":"), ensure_ascii=True)
        for row in payload["operations"]
    )
    encoded = f'{header[:-1]},\n  "operations": [\n{rows}\n  ]\n}}\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    supported_index = payload["operation_fields"].index("supported")
    supported = sum(item[supported_index] for item in payload["operations"])
    print(
        f"Wrote {len(payload['operations'])} operations ({supported} supported) "
        f"for Botocore {botocore.__version__} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
