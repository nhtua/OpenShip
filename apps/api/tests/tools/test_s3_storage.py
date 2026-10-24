from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ConnectorRef
from src.openship.providers.control import InvocationControl
from src.openship.tools.aws import s3_storage
from src.openship.tools.aws.contracts import (
    AwsStorageArgumentsV2,
    AwsStorageObservationV2,
)
from src.openship.tools.aws.s3_storage import (
    STORAGE_TYPES_V1,
    normalize_metric_results,
)

_NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _metric(metric_id, timestamps, values, status="Complete"):
    return {
        "Id": metric_id,
        "Timestamps": timestamps,
        "Values": values,
        "StatusCode": status,
        "Messages": [],
    }


def test_storage_types_are_a_frozen_general_purpose_set():
    assert len(STORAGE_TYPES_V1) == 22
    assert "ExpressOneZoneStorage" not in STORAGE_TYPES_V1
    assert "IntelligentTieringDAAStorage" in STORAGE_TYPES_V1
    assert "DeepArchiveS3ObjectOverhead" in STORAGE_TYPES_V1


def test_metric_normalization_preserves_gaps_and_never_adds_different_dates():
    common = _NOW - timedelta(days=1)
    older = _NOW - timedelta(days=2)
    results = [
        _metric("m00", [common, older], [0.0, 100.0]),
        _metric("m01", [common], [50.0]),
    ]

    observation = normalize_metric_results(
        results,
        query_ids={"m00": STORAGE_TYPES_V1[0], "m01": STORAGE_TYPES_V1[1]},
        queried_types=STORAGE_TYPES_V1[:3],
        retrieved_at=_NOW,
    )

    assert observation.outcome == "partial"
    assert observation.measurement_at == common
    assert observation.reported_storage_subtotal_bytes == 50
    assert observation.coverage.missing_types == (STORAGE_TYPES_V1[2],)
    assert observation.storage_types[0].value_bytes == 0
    assert observation.storage_types[0].measured_at == common


def test_metric_normalization_returns_no_data_for_empty_success():
    observation = normalize_metric_results(
        [_metric("m00", [], [])],
        query_ids={"m00": STORAGE_TYPES_V1[0]},
        queried_types=STORAGE_TYPES_V1[:1],
        retrieved_at=_NOW,
    )

    assert observation.outcome == "no_data"
    assert observation.measurement_at is None
    assert observation.reported_storage_subtotal_bytes is None


def test_partial_and_truncated_results_keep_their_coverage_explicit():
    observation = normalize_metric_results(
        [
            _metric("m00", [_NOW - timedelta(days=1)], [3.0], status="PartialData"),
            _metric("m01", [_NOW - timedelta(days=1)], [7.0]),
        ],
        query_ids={"m00": STORAGE_TYPES_V1[0], "m01": STORAGE_TYPES_V1[1]},
        queried_types=STORAGE_TYPES_V1[:3],
        retrieved_at=_NOW,
        truncated=True,
    )

    assert observation.outcome == "partial"
    assert observation.coverage.partial_types == (STORAGE_TYPES_V1[0],)
    assert observation.coverage.missing_types == (STORAGE_TYPES_V1[2],)
    assert observation.coverage.truncated is True
    assert observation.reported_storage_subtotal_bytes == 10


def test_all_frozen_storage_types_at_one_time_form_a_measured_subtotal():
    timestamp = _NOW - timedelta(days=1)
    query_ids = {
        f"m{index:02d}": storage_type
        for index, storage_type in enumerate(STORAGE_TYPES_V1)
    }
    results = [
        _metric(query_id, [timestamp], [float(index)])
        for index, query_id in enumerate(query_ids)
    ]

    observation = normalize_metric_results(
        results,
        query_ids=query_ids,
        queried_types=STORAGE_TYPES_V1,
        retrieved_at=_NOW,
    )

    assert observation.outcome == "measured"
    assert observation.measurement_at == timestamp
    assert observation.reported_storage_subtotal_bytes == sum(range(22))
    assert observation.coverage.missing_types == ()


@pytest.mark.parametrize(
    "timestamps, values",
    [
        ([_NOW, _NOW - timedelta(minutes=1)], [1.0]),
        ([_NOW, _NOW], [1.0, 2.0]),
        ([_NOW + timedelta(days=1)], [1.0]),
    ],
)
def test_metric_normalization_rejects_unaligned_duplicate_or_out_of_window_data(
    timestamps, values
):
    with pytest.raises(ValueError):
        normalize_metric_results(
            [_metric("m00", timestamps, values)],
            query_ids={"m00": STORAGE_TYPES_V1[0]},
            queried_types=STORAGE_TYPES_V1[:1],
            retrieved_at=_NOW,
            window_start=_NOW - timedelta(days=7),
            window_end=_NOW,
        )


def test_metric_normalization_enforces_the_datapoint_limit():
    timestamp = _NOW - timedelta(days=1)
    with pytest.raises(ValueError):
        normalize_metric_results(
            [_metric("m00", [timestamp] * 1025, [1.0] * 1025)],
            query_ids={"m00": STORAGE_TYPES_V1[0]},
            queried_types=STORAGE_TYPES_V1[:1],
            retrieved_at=_NOW,
        )


@pytest.mark.parametrize(
    "result",
    [
        _metric("m00", [_NOW], [float("nan")]),
        _metric("m00", [_NOW], [-1.0]),
        _metric("m00", [_NOW, _NOW - timedelta(days=1)], [1.0]),
        _metric("m00", [_NOW], [1.0], status="InternalError"),
    ],
)
def test_metric_normalization_rejects_invalid_or_unreported_values(result):
    with pytest.raises(ValueError):
        normalize_metric_results(
            [result],
            query_ids={"m00": STORAGE_TYPES_V1[0]},
            queried_types=STORAGE_TYPES_V1[:1],
            retrieved_at=_NOW,
        )


def test_stale_is_based_on_measurement_time_not_retrieval_time():
    measurement = _NOW - timedelta(days=3)
    observation = normalize_metric_results(
        [_metric("m00", [measurement], [10.0])],
        query_ids={"m00": STORAGE_TYPES_V1[0]},
        queried_types=STORAGE_TYPES_V1[:1],
        retrieved_at=_NOW,
    )

    assert observation.stale is True
    assert observation.reported_storage_subtotal_bytes == 10


def test_storage_manifest_uses_a_direct_bucket_target_without_resource_kinds():
    manifest = next(
        item
        for item in list_capability_manifests()
        if item.id == "aws_s3_bucket_storage"
    )

    assert manifest.version == "2.0.0"
    assert manifest.target_kinds == ()
    assert "resource_binding_id" not in manifest.input_schema["properties"]
    assert {"connector_id", "bucket_name", "region", "expected_owner"}.issubset(
        manifest.input_schema["properties"]
    )


class _DirectAccess:
    def __init__(self, *, fail_location=False):
        self.configuration = SimpleNamespace(
            expected_account_id="123456789012",
            basic_role_arn="arn:aws:iam::123456789012:role/read-role",
            region="us-east-1",
        )
        self.request_budget = SimpleNamespace(used=2, remaining=8)
        self.fail_location = fail_location
        self.calls = []
        self.clients = []
        self.closed = False

    def client(self, service, *, region=None):
        self.clients.append((service, region))
        if service == "sts":
            def get_caller_identity():
                return None

            return SimpleNamespace(get_caller_identity=get_caller_identity)
        if service == "s3":
            def get_bucket_location(**_kwargs):
                return None

            return SimpleNamespace(get_bucket_location=get_bucket_location)
        assert service == "cloudwatch"

        def get_metric_data(**_kwargs):
            return None

        return SimpleNamespace(get_metric_data=get_metric_data)

    async def run(self, method, **parameters):
        self.calls.append((method.__name__, parameters))
        if method.__name__ == "get_caller_identity":
            self.request_budget.used += 1
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/read-role/session",
            }
        if method.__name__ == "get_bucket_location":
            if self.fail_location:
                from src.openship.execution.aws_transport import AwsSdkError

                raise AwsSdkError("aws_access_denied")
            self.request_budget.used += 1
            return {"LocationConstraint": "us-east-1"}
        assert method.__name__ == "get_metric_data"
        self.request_budget.used += 1
        measured_at = datetime.now(UTC) - timedelta(days=1)
        return {
            "MetricDataResults": [
                {
                    "Id": query["Id"],
                    "Timestamps": [measured_at],
                    "Values": [float(index + 1)],
                    "StatusCode": "Complete",
                }
                for index, query in enumerate(parameters["MetricDataQueries"])
            ]
        }

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_storage_helper_uses_supplied_region_without_bucket_location_permission(
    monkeypatch,
):
    manifest = next(
        item
        for item in list_capability_manifests()
        if item.id == "aws_s3_bucket_storage"
    )
    connector_ref = ConnectorRef(
        id=uuid4(),
        kind="aws",
        configuration_revision=4,
        visibility="project",
        credential_version_id=uuid4(),
    )
    access = _DirectAccess(fail_location=True)
    async def acquire(reference):
        assert reference == connector_ref
        return access

    context = SimpleNamespace(
        authorization=SimpleNamespace(project_id=uuid4()),
        execution=SimpleNamespace(
            allowed_capabilities=(
                CapabilityRef(
                    id=manifest.id,
                    version=manifest.version,
                    manifest_digest=manifest.manifest_digest,
                ),
            ),
            connector_refs=(connector_ref,),
        ),
        operation_id=uuid4(),
        aws_access_factory=acquire,
    )

    observation = await s3_storage.aws_s3_bucket_storage(
        AwsStorageArgumentsV2(
            connector_id=connector_ref.id,
            bucket_name="public-demo-bucket",
            region="us-east-1",
        ),
        context,
        InvocationControl(),
    )

    assert observation.bucket_name == "public-demo-bucket"
    assert observation.region == "us-east-1"
    assert observation.metrics.outcome == "measured"
    assert "resource_id" not in AwsStorageObservationV2.model_fields
    assert "resource_binding_id" not in AwsStorageObservationV2.model_fields
    assert [name for name, _parameters in access.calls] == [
        "get_caller_identity",
        "get_metric_data",
    ]
    metric_client = [item for item in access.clients if item[0] == "cloudwatch"]
    assert metric_client == [("cloudwatch", "us-east-1")]
    assert access.closed
