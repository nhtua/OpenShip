from __future__ import annotations

import hashlib
import json
import urllib.request
from types import SimpleNamespace

import botocore.session
import pytest

from src.openship.connectors.aws.read_catalog import (
    AwsReadCatalogError,
    classify_aws_operation,
    load_aws_read_catalog,
    operation_schema,
    require_read_operation,
    validate_parameters,
)
from src.openship.tools.aws.read_catalog import (
    AwsReadCatalogArguments,
    aws_read_catalog,
)


def _resolve(schema, value):
    reference = value.get("$ref")
    return schema["$defs"][reference.removeprefix("#/$defs/")] if reference else value


def _annotations(*, is_list=False, is_write=False, is_permission_management=False):
    return {
        "IsList": is_list,
        "IsPermissionManagement": is_permission_management,
        "IsTaggingOnly": False,
        "IsWrite": is_write,
    }


def _action(service, name, annotations):
    return {
        "Name": name,
        "Service": service,
        "Annotations": {"Properties": annotations},
    }


def _operation(*, service="s3", name="ListBuckets", actions=None):
    return {
        "Name": name,
        "AuthorizedActions": actions
        if actions is not None
        else [_action(service, name, _annotations(is_list=True))],
        "SDK": [{"Name": service, "Method": "list_buckets", "Package": "Boto3"}],
    }


def test_read_catalog_requires_complete_action_annotations():
    descriptor = classify_aws_operation(
        service="s3",
        operation=_operation(),
        actions={("s3", "ListBuckets"): _annotations(is_list=True)},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert descriptor.supported is True
    assert descriptor.iam_actions == ("s3:ListBuckets",)

    missing_annotation = classify_aws_operation(
        service="s3",
        operation=_operation(),
        actions={("s3", "ListBuckets"): {"IsWrite": False}},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert missing_annotation.supported is False
    assert missing_annotation.unsupported_reason == "incomplete_action_annotations"

    missing_action = classify_aws_operation(
        service="s3",
        operation=_operation(),
        actions={},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert missing_action.supported is False
    assert missing_action.unsupported_reason == "missing_authorized_action"

    cross_service = classify_aws_operation(
        service="s3",
        operation=_operation(actions=[_action("ec2", "ListBuckets", _annotations())]),
        actions={("ec2", "ListBuckets"): _annotations()},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert cross_service.supported is False
    assert cross_service.unsupported_reason == "cross_service_authorization"

    mixed_actions = classify_aws_operation(
        service="s3",
        operation=_operation(
            actions=[
                _action("s3", "ListBuckets", _annotations(is_list=True)),
                _action("s3", "PutBucketPolicy", _annotations(is_write=True)),
            ]
        ),
        actions={
            ("s3", "ListBuckets"): _annotations(is_list=True),
            ("s3", "PutBucketPolicy"): _annotations(is_write=True),
        },
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert mixed_actions.supported is False
    assert mixed_actions.unsupported_reason == "operation_requires_write_action"

    allowed_s3_alias = {
        "Name": "ListObjectsV2",
        "AuthorizedActions": [
            {"Name": "ListBucket", "Service": "s3"},
            {"Name": "ListBucket", "Service": "s3-object-lambda"},
        ],
        "SDK": [{"Name": "s3", "Method": "list_objects_v2", "Package": "Boto3"}],
    }
    reviewed_cross_service = classify_aws_operation(
        service="s3",
        operation=allowed_s3_alias,
        actions={
            ("s3", "ListBucket"): _annotations(is_list=True),
            ("s3-object-lambda", "ListBucket"): _annotations(is_list=True),
        },
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="rest-xml",
    )
    assert reviewed_cross_service.supported is True


def test_get_prefix_does_not_prove_read_only():
    operation = _operation(service="sts", name="GetSessionToken")
    operation["SDK"][0]["Method"] = "get_session_token"
    descriptor = classify_aws_operation(
        service="sts",
        operation=operation,
        actions={("sts", "GetSessionToken"): _annotations()},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"Credentials": {"type": "object"}}},
        protocol="query",
    )
    assert descriptor.supported is False
    assert descriptor.unsupported_reason == "credential_vending_operation"


def test_sensitive_sdk_output_marker_is_preserved_for_redaction():
    descriptor = classify_aws_operation(
        service="s3",
        operation=_operation(),
        actions={("s3", "ListBuckets"): _annotations(is_list=True)},
        input_schema={"type": "object", "properties": {}},
        output_schema={
            "type": "object",
            "properties": {"Value": {"$ref": "#/$defs/SensitiveValue"}},
            "$defs": {"SensitiveValue": {"type": "string", "x-openship-sensitive": True}},
        },
        protocol="rest-xml",
    )
    assert descriptor.supported is True
    assert descriptor.unsupported_reason is None

    password = SimpleNamespace(
        name="SensitivePassword",
        type_name="string",
        metadata={"sensitive": True},
        enum=[],
    )
    output = SimpleNamespace(
        name="Output",
        type_name="structure",
        members={"MasterUserPassword": password},
        required_members=(),
        metadata={},
    )
    output_schema = operation_schema(output)
    assert output_schema["properties"]["MasterUserPassword"]["x-openship-sensitive"] is True


@pytest.mark.parametrize(
    ("service", "operation_name", "api_name", "parameters"),
    [
        ("secretsmanager", "GetSecretValue", "get_secret_value", {"SecretId": "x"}),
        ("ssm", "GetParameter", "get_parameter", {"Name": "x", "WithDecryption": False}),
    ],
)
def test_secret_value_operations_are_explicitly_unsupported(
    service, operation_name, api_name, parameters
):
    operation = _operation(service=service, name=operation_name)
    operation["SDK"][0]["Method"] = api_name
    descriptor = classify_aws_operation(
        service=service,
        operation=operation,
        actions={(service, operation_name): _annotations()},
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        protocol="json",
    )
    assert descriptor.supported is False
    assert descriptor.unsupported_reason == "application_secret_operation"
    with pytest.raises(AwsReadCatalogError, match="unsupported"):
        validate_parameters(descriptor, parameters)


def test_unknown_sdk_version_or_digest_fails_closed(tmp_path, monkeypatch):
    payload = {
        "schema_version": 1,
        "schema_conversion_version": 1,
        "sdk_version": "0.0.0",
        "source": "https://example.invalid",
        "operation_fields": [
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
        ],
        "operations": [],
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AwsReadCatalogError, match="SDK version"):
        load_aws_read_catalog(catalog_path)

    payload["sdk_version"] = botocore.__version__
    payload["digest"] = "0" * 64
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AwsReadCatalogError, match="digest"):
        load_aws_read_catalog(catalog_path)


def test_operation_schema_preserves_nested_required_fields():
    model = botocore.session.get_session().get_service_model("s3")
    operation_model = model.operation_model("ListObjectsV2")
    schema = operation_schema(operation_model.input_shape)
    properties = schema["properties"]
    assert _resolve(schema, properties["Bucket"])["type"] == "string"
    assert "Prefix" in properties
    assert "Bucket" in schema["required"]

    ec2 = botocore.session.get_session().get_service_model("ec2")
    instance_schema = operation_schema(
        ec2.operation_model("DescribeInstances").input_shape
    )
    instance_filters = _resolve(instance_schema, instance_schema["properties"]["Filters"])
    assert instance_filters["type"] == "array"
    filter_schema = _resolve(instance_schema, instance_filters["items"])
    values_schema = _resolve(instance_schema, filter_schema["properties"]["Values"])
    assert _resolve(instance_schema, values_schema["items"])["type"] == "string"


def test_operation_schema_preserves_enums_and_bounded_lists_and_maps():
    session = botocore.session.get_session()
    analyzer = session.get_service_model("accessanalyzer")
    request_schema = operation_schema(
        analyzer.operation_model("CheckAccessNotGranted").input_shape
    )
    policy_type = _resolve(request_schema, request_schema["properties"]["policyType"])
    assert policy_type["enum"] == [
        "IDENTITY_POLICY",
        "RESOURCE_POLICY",
    ]
    access = _resolve(request_schema, request_schema["properties"]["access"])
    assert access["maxItems"] == 1

    resource_tags = analyzer.shape_for("AclGrantee")
    union_schema = operation_schema(resource_tags)
    assert len(union_schema["oneOf"]) == 2
    assert all(len(choice["required"]) == 1 for choice in union_schema["oneOf"])

    registry = session.get_service_model("agent-registry-control")
    tags_schema = operation_schema(registry.shape_for("ResourceTagsMap"))
    assert tags_schema["maxProperties"] == 50


def test_schema_size_refusal_is_separate_from_bounded_ec2_response_schema():
    model = botocore.session.get_session().get_service_model("ec2")
    output_shape = model.operation_model("DescribeInstances").output_shape
    with pytest.raises(AwsReadCatalogError, match="schema expansion"):
        operation_schema(output_shape)
    bounded_schema = operation_schema(output_shape, max_bytes=64 * 1024)
    assert len(json.dumps(bounded_schema, separators=(",", ":")).encode()) <= 64 * 1024


def test_operation_schema_refuses_recursive_shapes():
    recursive = SimpleNamespace(
        name="Recursive",
        type_name="structure",
        members={},
        required_members=(),
        metadata={},
    )
    recursive.members["child"] = recursive
    with pytest.raises(AwsReadCatalogError, match="recursive"):
        operation_schema(recursive)


def test_operation_schema_refuses_excessive_nesting_depth():
    leaf = SimpleNamespace(
        name="Leaf",
        type_name="string",
        metadata={},
        enum=[],
    )
    nested = leaf
    for index in range(20):
        nested = SimpleNamespace(
            name=f"Level{index}",
            type_name="structure",
            members={"child": nested},
            required_members=(),
            metadata={},
        )
    with pytest.raises(AwsReadCatalogError, match="schema depth"):
        operation_schema(nested)


def test_parameter_validation_rejects_unknown_fields_and_over_bounds():
    from src.openship.connectors.aws.read_catalog import descriptor_from_sdk_operation

    descriptor = descriptor_from_sdk_operation("s3", "ListObjectsV2")
    with pytest.raises(AwsReadCatalogError, match="unknown parameter"):
        validate_parameters(descriptor, {"Bucket": "demo", "Unexpected": True})
    with pytest.raises(AwsReadCatalogError, match="maximum"):
        validate_parameters(descriptor, {"Bucket": "demo", "MaxKeys": 1001})


def test_catalog_lookup_is_local_and_lists_are_paged(monkeypatch):
    catalog = load_aws_read_catalog()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("runtime catalog must not access network"))
    first = catalog.list_operations(limit=1)
    assert len(first.items) == 1
    assert first.next_cursor is not None
    second = catalog.list_operations(limit=1, cursor=first.next_cursor)
    assert len(second.items) == 1
    assert second.items[0].service != first.items[0].service or second.items[0].operation != first.items[0].operation
    with pytest.raises(AwsReadCatalogError):
        require_read_operation("s3", "create_bucket")


@pytest.mark.parametrize(
    ("service", "operation"),
    [
        ("s3", "list_buckets"),
        ("s3", "get_bucket_location"),
        ("s3", "get_bucket_versioning"),
        ("s3", "get_bucket_policy_status"),
        ("s3", "get_public_access_block"),
        ("s3", "get_bucket_acl"),
        ("cloudwatch", "get_metric_data"),
        ("ec2", "describe_instances"),
        ("rds", "describe_db_instances"),
        ("sts", "get_caller_identity"),
    ],
)
def test_catalog_supports_the_phase_five_read_fixtures(service, operation):
    descriptor = require_read_operation(service, operation)
    assert descriptor.supported is True


@pytest.mark.asyncio
async def test_local_catalog_tool_returns_pages_and_parameter_schema_without_aws():
    class _Control:
        def raise_if_stopped(self):
            return None

    first = await aws_read_catalog(
        AwsReadCatalogArguments(service="s3", limit=1),
        object(),
        _Control(),
    )
    assert len(first.operations) == 1
    assert first.next_cursor

    operation = await aws_read_catalog(
        AwsReadCatalogArguments(service="s3", operation="get_bucket_versioning"),
        object(),
        _Control(),
    )
    assert operation.operations[0].supported is True
    assert operation.operations[0].parameter_schema
