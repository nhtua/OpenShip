"""Runtime-owned tool availability contains only safe per-capability state."""

from datetime import UTC, datetime, timedelta

from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.availability import (
    observe_tool_availability,
    publish_tool_availability,
    read_tool_availability,
)
from src.openship.execution.models import ToolAvailability


def test_each_search_tool_availability_depends_only_on_its_own_binding(monkeypatch):
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "brave_web_search,tavily_web_search"
    )
    test_value = "<<NOT_A_CREDENTIAL>>"
    values = {
        "BRAVE_SEARCH_API_KEY": test_value,
        "TAVILY_API_KEY": None,
    }

    observations = observe_tool_availability(
        enabled_ids=settings.enabled_tool_ids,
        credential_check=lambda binding: values.get(binding) is not None,
    )

    actual = {item.capability_id: (item.available, item.reason) for item in observations}
    assert actual["brave_web_search"] == (True, None)
    assert actual["tavily_web_search"] == (False, "missing_credentials")
    assert actual["workspace_list_files"] == (False, "disabled")
    assert actual["workspace_read_file"] == (False, "disabled")
    assert all(test_value not in repr(item) for item in observations)


def test_bound_inventory_readiness_does_not_depend_on_artifact_store_or_credentials(
    monkeypatch,
):
    def unexpected(_value=None):
        raise AssertionError("inventory readiness must not check storage or credentials")

    monkeypatch.setattr(
        "src.openship.execution.availability.configured_store", unexpected
    )
    observations = observe_tool_availability(
        enabled_ids=frozenset({"aws_s3_list_bound_buckets"}),
        credential_check=unexpected,
    )

    inventory = next(
        item
        for item in observations
        if item.capability_id == "aws_s3_list_bound_buckets"
    )
    assert (inventory.available, inventory.reason) == (True, None)


def test_runtime_availability_upserts_safe_observations_and_expires_them(monkeypatch):
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "brave_web_search,tavily_web_search"
    )
    db = SessionLocal()
    now = datetime.now(UTC)
    observations = observe_tool_availability(
        enabled_ids=settings.enabled_tool_ids,
        credential_check=lambda binding: binding == "BRAVE_SEARCH_API_KEY",
        observed_at=now,
    )

    publish_tool_availability(db, "availability-worker", observations)
    db.commit()
    publish_tool_availability(db, "availability-worker", observations)
    db.commit()

    rows = db.query(ToolAvailability).all()
    assert len(rows) == 10
    assert all(row.worker_id == "availability-worker" for row in rows)
    assert all(not hasattr(row, "credential_binding_ref") for row in rows)
    fresh = read_tool_availability(db, now=now)
    assert fresh == {
        "brave_web_search": (True, None),
        "tavily_web_search": (False, "missing_credentials"),
        "workspace_list_files": (False, "disabled"),
        "workspace_read_file": (False, "disabled"),
        "aws_check_connection": (False, "disabled"),
        "aws_s3_bucket_storage": (False, "disabled"),
        "aws_s3_list_bound_buckets": (False, "disabled"),
        "aws_s3_bucket_metadata": (False, "disabled"),
        "aws_read_catalog": (False, "disabled"),
        "aws_read": (False, "disabled"),
    }
    expired = read_tool_availability(db, now=now + timedelta(seconds=31))
    assert expired == {
        "brave_web_search": (False, "runtime_unavailable"),
        "tavily_web_search": (False, "runtime_unavailable"),
        "workspace_list_files": (False, "runtime_unavailable"),
        "workspace_read_file": (False, "runtime_unavailable"),
        "aws_check_connection": (False, "runtime_unavailable"),
        "aws_s3_bucket_storage": (False, "runtime_unavailable"),
        "aws_s3_list_bound_buckets": (False, "runtime_unavailable"),
        "aws_s3_bucket_metadata": (False, "runtime_unavailable"),
        "aws_read_catalog": (False, "runtime_unavailable"),
        "aws_read": (False, "runtime_unavailable"),
    }
    db.close()
