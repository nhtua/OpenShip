"""Capacity alerting and bounded retained-source reconciliation contracts."""

import builtins
import logging
import uuid
from types import SimpleNamespace

import pytest

from src.openship.messaging.capacity import (
    capacity_report,
    scan_retained_run_sources,
)
from src.openship.messaging.capacity_recovery import recovery_gate_errors
from src.openship.messaging.exhaustion_supervisor import ExhaustionSupervisor


def test_advisory_capacity_alert_is_inclusive_at_75_percent():
    assert capacity_report(47 * 1024 * 1024, 64 * 1024 * 1024).alert is False
    report = capacity_report(48 * 1024 * 1024, 64 * 1024 * 1024)
    assert report.alert is True
    assert report.full is False
    assert report.utilization_percent == 75.0


def test_advisory_capacity_reports_full_and_rejects_invalid_limits():
    assert capacity_report(64, 64).full is True
    with pytest.raises(ValueError):
        capacity_report(1, 0)
    with pytest.raises(ValueError):
        capacity_report(-1, 64)


def test_recovery_apply_requires_closed_gates_and_paused_drained_consumers():
    consumers = {
        ("OS_RUNS", "run-v1"): SimpleNamespace(paused=True, num_ack_pending=0),
        ("OS_MAINTENANCE", "maintenance-v1"): SimpleNamespace(
            paused=True, num_ack_pending=0
        ),
    }
    assert (
        recovery_gate_errors(
            transport_active=True,
            admission_open=False,
            execution_open=False,
            consumers=consumers,
        )
        == ()
    )

    consumers[("OS_RUNS", "run-v1")] = SimpleNamespace(paused=False, num_ack_pending=1)
    assert recovery_gate_errors(
        transport_active=True,
        admission_open=True,
        execution_open=False,
        consumers=consumers,
    ) == (
        "admission_gate_open",
        "consumer_not_paused:OS_RUNS:run-v1",
        "consumer_not_drained:OS_RUNS:run-v1",
    )


@pytest.mark.asyncio
async def test_retained_source_scan_is_subject_filtered_bounded_and_resumable():
    subject = "openship.installation.v1.command.run.execute"
    expected_subject = subject
    messages = [
        SimpleNamespace(seq=2, subject=subject, data=b"two", time="t2"),
        SimpleNamespace(
            seq=3,
            subject="openship.installation.v1.event.run",
            data=b"event",
            time="t3",
        ),
        SimpleNamespace(seq=5, subject=subject, data=b"five", time="t5"),
        SimpleNamespace(seq=7, subject=subject, data=b"seven", time="t7"),
        SimpleNamespace(seq=9, subject=subject, data=b"nine", time="t9"),
    ]

    class JetStream:
        async def get_msg(self, stream, *, seq, subject, next):
            assert stream == "OS_RUNS"
            assert subject == expected_subject
            assert next is True
            return builtins.next(
                message
                for message in messages
                if message.subject == subject and message.seq >= seq
            )

    first = await scan_retained_run_sources(
        JetStream(),
        stream="OS_RUNS",
        subject=subject,
        after_sequence=0,
        through_sequence=8,
        limit=2,
    )
    assert [message.seq for message in first.messages] == [2, 5]
    assert first.truncated is True
    assert first.next_after_sequence == 5

    second = await scan_retained_run_sources(
        JetStream(),
        stream="OS_RUNS",
        subject=subject,
        after_sequence=first.next_after_sequence,
        through_sequence=8,
        limit=2,
    )
    assert [message.seq for message in second.messages] == [7]
    assert second.truncated is False


@pytest.mark.asyncio
async def test_supervisor_logs_capacity_threshold_full_and_recovery(caplog):
    capacities = [48 * 1024 * 1024, 64 * 1024 * 1024, 47 * 1024 * 1024]

    class JetStream:
        async def stream_info(self, stream):
            assert stream == "OS_ADVISORIES"
            stored = capacities.pop(0)
            return SimpleNamespace(
                state=SimpleNamespace(bytes=stored),
                config=SimpleNamespace(max_bytes=64 * 1024 * 1024),
            )

    class Client:
        def jetstream(self, *, timeout):
            assert timeout == 2.0
            return JetStream()

    supervisor = ExhaustionSupervisor(
        Client(), installation_id=uuid.uuid4(), session_factory=None
    )
    with caplog.at_level(logging.INFO):
        await supervisor._check_advisory_capacity()
        await supervisor._check_advisory_capacity()
        await supervisor._check_advisory_capacity()

    assert "advisory_capacity_alert" in caplog.text
    assert "advisory_capacity_full" in caplog.text
    assert "advisory_capacity_recovered" in caplog.text
