"""Bounded daily S3 storage metrics through the shared AWS SDK runner."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, time, timedelta

from pydantic import ValidationError

from src.openship.capabilities.contracts import CapabilityRef, canonical_json
from src.openship.execution.aws_transport import AwsSdkError

from .checks import normalize_bucket_region, validate_caller_identity
from .contracts import (
    AwsStorageArguments,
    AwsStorageObservation,
    AwsStorageResult,
    AwsToolError,
    NormalizedStorageMetrics,
    StorageCoverage,
    StorageTypeMeasurement,
)

STORAGE_TYPES_V1 = (
    "DeepArchiveObjectOverhead",
    "DeepArchiveS3ObjectOverhead",
    "DeepArchiveStagingStorage",
    "DeepArchiveStorage",
    "GlacierObjectOverhead",
    "GlacierS3ObjectOverhead",
    "GlacierStagingStorage",
    "GlacierStorage",
    "GlacierInstantRetrievalStorage",
    "GlacierIRSizeOverhead",
    "IntelligentTieringAAStorage",
    "IntelligentTieringAIAStorage",
    "IntelligentTieringDAAStorage",
    "IntelligentTieringFAStorage",
    "IntelligentTieringIAStorage",
    "OneZoneIASizeOverhead",
    "OneZoneIAStorage",
    "StandardStorage",
    "StandardIAObjectOverhead",
    "StandardIASizeOverhead",
    "StandardIAStorage",
    "ReducedRedundancyStorage",
)
_METRIC_ID = re.compile(r"^m(?:0[0-9]|1[0-9]|2[01])$")
_PAGE_LIMIT = 4
_MAX_DATAPOINTS = 1024
_STALE_AFTER = timedelta(hours=48)


def normalize_metric_results(
    results: list[dict],
    *,
    query_ids: dict[str, str],
    queried_types: tuple[str, ...],
    retrieved_at: datetime,
    truncated: bool = False,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    """Validate a bounded CloudWatch response and preserve each type's time."""
    if (
        len(query_ids) > 64
        or len(queried_types) > len(STORAGE_TYPES_V1)
        or len(set(queried_types)) != len(queried_types)
        or any(value not in STORAGE_TYPES_V1 for value in queried_types)
        or retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() is None
    ):
        raise ValueError("metric request scope is invalid")
    ids = tuple(query_ids)
    if len(ids) != len(set(ids)) or any(
        not _METRIC_ID.fullmatch(value) for value in ids
    ):
        raise ValueError("metric query IDs are invalid")
    if len(set(query_ids.values())) != len(query_ids) or any(
        value not in queried_types for value in query_ids.values()
    ):
        raise ValueError("metric query types are invalid")
    if not isinstance(results, list) or len(results) > 64:
        raise ValueError("metric response exceeds the datapoint limit")

    by_id = {}
    total_datapoints = 0
    for result in results:
        if not isinstance(result, dict):
            raise TypeError("metric response is invalid")
        metric_id = result.get("Id")
        if metric_id not in query_ids or metric_id in by_id:
            raise ValueError("metric response contains an unexpected query")
        if result.get("StatusCode") not in ("Complete", "PartialData"):
            raise ValueError("metric response is incomplete")
        timestamps = result.get("Timestamps")
        values = result.get("Values")
        if (
            not isinstance(timestamps, list)
            or not isinstance(values, list)
            or len(timestamps) != len(values)
        ):
            raise ValueError("metric response values do not align")
        total_datapoints += len(values)
        if total_datapoints > _MAX_DATAPOINTS:
            raise ValueError("metric response exceeds the datapoint limit")
        normalized = {}
        for timestamp, value in zip(timestamps, values, strict=True):
            if (
                not isinstance(timestamp, datetime)
                or timestamp.tzinfo is None
                or timestamp.utcoffset() is None
                or not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("metric datapoint is invalid")
            instant = timestamp.astimezone(UTC)
            if (
                window_start is not None and instant < window_start.astimezone(UTC)
            ) or (window_end is not None and instant > window_end.astimezone(UTC)):
                raise ValueError("metric datapoint is outside the requested window")
            if instant in normalized:
                raise ValueError("metric response contains duplicate timestamps")
            normalized[instant] = float(value)
        by_id[metric_id] = (normalized, result.get("StatusCode") == "PartialData")

    data_by_type: dict[str, dict[datetime, float]] = {}
    partial_types = set()
    for query_id, storage_type in query_ids.items():
        data, partial = by_id.get(query_id, ({}, False))
        data_by_type[storage_type] = data
        if partial:
            partial_types.add(storage_type)
    present = {storage_type for storage_type, values in data_by_type.items() if values}
    missing = set(queried_types) - present
    all_timestamps = [set(values) for values in data_by_type.values() if values]
    common_timestamps = set.intersection(*all_timestamps) if all_timestamps else set()
    measurement_at = max(common_timestamps) if common_timestamps else None
    measurements = tuple(
        StorageTypeMeasurement(
            storage_type=storage_type,
            measured_at=timestamp,
            value_bytes=value,
            stale=retrieved_at.astimezone(UTC) - timestamp > _STALE_AFTER,
        )
        for storage_type, values in data_by_type.items()
        for timestamp, value in sorted(values.items(), reverse=True)
    )
    subtotal = None
    if measurement_at is not None:
        subtotal = float(
            sum(
                values[measurement_at]
                for values in data_by_type.values()
                if measurement_at in values
            )
        )
    complete = (
        set(queried_types) == set(STORAGE_TYPES_V1)
        and not missing
        and not partial_types
        and not truncated
    )
    outcome = "no_data" if not present else "measured" if complete else "partial"
    stale = (
        retrieved_at.astimezone(UTC) - measurement_at > _STALE_AFTER
        if measurement_at is not None
        else any(item.stale for item in measurements)
    )
    return NormalizedStorageMetrics(
        outcome=outcome,
        stale=stale,
        measurement_at=measurement_at,
        reported_storage_subtotal_bytes=subtotal,
        storage_types=measurements,
        coverage=StorageCoverage(
            queried_types=queried_types,
            reported_types=tuple(value for value in queried_types if value in present),
            missing_types=tuple(value for value in queried_types if value in missing),
            partial_types=tuple(
                value for value in queried_types if value in partial_types
            ),
            query_count=len(query_ids),
            truncated=truncated,
        ),
    )


async def aws_s3_bucket_storage(
    arguments: AwsStorageArguments,
    context,
    control,
) -> AwsStorageObservation:
    """Read only the pinned S3 bucket's documented daily storage metrics."""
    binding = _owned_binding(context, arguments.resource_binding_id)
    reference = _connector_reference(context, binding)
    access = await context.aws_access_factory(reference)
    try:
        configuration = access.configuration
        if (
            binding.account_id != configuration.expected_account_id
            or binding.expected_owner != configuration.expected_account_id
            or binding.region != configuration.region
        ):
            raise AwsToolError("aws_resource_unavailable")
        caller_identity = await access.run(access.client("sts").get_caller_identity)
        validate_caller_identity(
            caller_identity,
            expected_account_id=binding.account_id,
            expected_role_arn=configuration.basic_role_arn,
        )
        location = await access.run(
            access.client("s3").get_bucket_location,
            Bucket=binding.bucket_name,
            ExpectedBucketOwner=binding.expected_owner,
        )
        if (
            normalize_bucket_region(location.get("LocationConstraint"))
            != binding.region
        ):
            raise AwsToolError("aws_region_mismatch")

        query_ids = {
            f"m{index:02d}": storage_type
            for index, storage_type in enumerate(STORAGE_TYPES_V1)
        }
        start = datetime.combine(
            datetime.now(UTC).date() - timedelta(days=7), time.min, tzinfo=UTC
        )
        end = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
        queries = [
            {
                "Id": query_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/S3",
                        "MetricName": "BucketSizeBytes",
                        "Dimensions": [
                            {"Name": "BucketName", "Value": binding.bucket_name},
                            {"Name": "StorageType", "Value": storage_type},
                        ],
                    },
                    "Period": 86400,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
            for query_id, storage_type in query_ids.items()
        ]
        client = access.client("cloudwatch")
        token = None
        results_by_id = {}
        truncated = False
        for _page in range(_PAGE_LIMIT):
            control.raise_if_stopped()
            parameters = {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampDescending",
                "MaxDatapoints": _MAX_DATAPOINTS,
            }
            if token is not None:
                parameters["NextToken"] = token
            response = await access.run(client.get_metric_data, **parameters)
            page = response.get("MetricDataResults")
            if not isinstance(page, list):
                raise AwsToolError("aws_invalid_metric_response")
            for item in page:
                if not isinstance(item, dict) or item.get("Id") not in query_ids:
                    raise AwsToolError("aws_invalid_metric_response")
                metric_id = item["Id"]
                existing = results_by_id.setdefault(
                    metric_id,
                    {
                        "Id": metric_id,
                        "Timestamps": [],
                        "Values": [],
                        "StatusCode": "Complete",
                    },
                )
                timestamps = item.get("Timestamps", [])
                values = item.get("Values", [])
                if not isinstance(timestamps, list) or not isinstance(values, list):
                    raise AwsToolError("aws_invalid_metric_response")
                existing["Timestamps"].extend(timestamps)
                existing["Values"].extend(values)
                if item.get("StatusCode") == "PartialData":
                    existing["StatusCode"] = "PartialData"
            if (
                sum(len(item["Values"]) for item in results_by_id.values())
                > _MAX_DATAPOINTS
            ):
                raise AwsToolError("aws_output_limit_exceeded")
            token = response.get("NextToken")
            if not token:
                break
            if _page + 1 == _PAGE_LIMIT or access.request_budget.remaining <= 0:
                truncated = True
                break
        retrieved_at = datetime.now(UTC)
        metrics = normalize_metric_results(
            list(results_by_id.values()),
            query_ids=query_ids,
            queried_types=STORAGE_TYPES_V1,
            retrieved_at=retrieved_at,
            truncated=truncated,
            window_start=start,
            window_end=end,
        )
        observation = AwsStorageObservation(
            tool=_capability_ref(context),
            operation_id=str(context.operation_id),
            project_id=context.authorization.project_id,
            resource_id=binding.resource_object_id,
            resource_binding_id=binding.id,
            binding_revision=binding.revision,
            connector_id=reference.id,
            connector_revision=reference.configuration_revision,
            account_id=binding.account_id,
            region=binding.region,
            bucket_name=binding.bucket_name,
            retrieved_at=retrieved_at,
            metrics=metrics,
        )
        if len(canonical_json(observation.model_dump(mode="json"))) > 64 * 1024:
            raise AwsToolError("aws_output_limit_exceeded")
        return observation
    except AwsToolError:
        raise
    except AwsSdkError as error:
        code = error.code if error.code in AwsToolError._CODES else "aws_unavailable"
        raise AwsToolError(code) from None
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AwsToolError("aws_invalid_metric_response") from None
    finally:
        await access.aclose()


def project_storage_result(observation: AwsStorageObservation, artifact_ref):
    metrics = observation.metrics
    return AwsStorageResult(
        operation_id=observation.operation_id,
        resource_id=observation.resource_id,
        resource_binding_id=observation.resource_binding_id,
        binding_revision=observation.binding_revision,
        account_id=observation.account_id,
        region=observation.region,
        bucket_name=observation.bucket_name,
        retrieved_at=observation.retrieved_at,
        outcome=metrics.outcome,
        stale=metrics.stale,
        measurement_at=metrics.measurement_at,
        reported_storage_subtotal_bytes=metrics.reported_storage_subtotal_bytes,
        coverage=metrics.coverage,
        artifact=artifact_ref,
    ).model_dump(mode="json")


def _owned_binding(context, binding_id):
    from src.openship.resources.models import ResourceBinding

    references = [
        ref
        for ref in getattr(context.execution, "resource_binding_refs", ())
        if ref.id == binding_id
    ]
    if len(references) != 1 or context.session_factory is None:
        raise AwsToolError("aws_resource_unavailable")
    target = references[0]
    with context.session_factory() as db:
        binding = (
            db.query(ResourceBinding)
            .filter(
                ResourceBinding.id == binding_id,
                ResourceBinding.project_id == context.authorization.project_id,
                ResourceBinding.revision == target.revision,
                ResourceBinding.connector_id == target.connector_id,
                ResourceBinding.connector_revision == target.connector_revision,
                ResourceBinding.state == "active",
            )
            .first()
        )
        if binding is None:
            raise AwsToolError("aws_resource_unavailable")
        return binding


def _connector_reference(context, binding):
    refs = [
        ref
        for ref in getattr(context.execution, "connector_refs", ())
        if ref.id == binding.connector_id
        and ref.kind == "aws"
        and ref.configuration_revision == binding.connector_revision
    ]
    if len(refs) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return refs[0]


def _capability_ref(context) -> CapabilityRef:
    matches = [
        ref
        for ref in context.execution.allowed_capabilities
        if ref.id == "aws_s3_bucket_storage"
    ]
    if len(matches) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return matches[0]
