from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
