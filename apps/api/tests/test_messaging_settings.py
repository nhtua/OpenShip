"""Capacity declarations must bound local execution and shared broker backlog."""

import pytest
from pydantic import ValidationError

from src.openship.config import Settings


@pytest.mark.parametrize(
    "values",
    [
        {"worker_run_concurrency": 5},
        {"worker_background_concurrency": 2},
        {"worker_run_concurrency": 0},
        {"worker_background_concurrency": 129},
        {"messaging_run_max_ack_pending": 0},
        {"messaging_maintenance_max_ack_pending": 1025},
    ],
)
def test_capacity_rejects_invalid_bounds_and_local_slots_above_aggregate(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_capacity_defaults_and_explicit_multi_process_declaration():
    defaults = Settings(_env_file=None)
    assert (
        defaults.worker_run_concurrency == defaults.messaging_run_max_ack_pending == 4
    )
    assert (
        defaults.worker_background_concurrency
        == defaults.messaging_maintenance_max_ack_pending
        == 1
    )
    configured = Settings(
        _env_file=None,
        worker_run_concurrency=8,
        messaging_run_max_ack_pending=16,
        messaging_maintenance_max_ack_pending=2,
    )
    assert configured.worker_run_concurrency == 8
    assert configured.messaging_run_max_ack_pending == 16


def test_capacity_declarations_load_from_environment(monkeypatch):
    for name, value in (
        ("WORKER_RUN_CONCURRENCY", "8"),
        ("WORKER_BACKGROUND_CONCURRENCY", "2"),
        ("MESSAGING_RUN_MAX_ACK_PENDING", "16"),
        ("MESSAGING_MAINTENANCE_MAX_ACK_PENDING", "4"),
    ):
        monkeypatch.setenv(name, value)
    configured = Settings(_env_file=None)
    assert configured.worker_run_concurrency == 8
    assert configured.worker_background_concurrency == 2
    assert configured.messaging_run_max_ack_pending == 16
    assert configured.messaging_maintenance_max_ack_pending == 4
