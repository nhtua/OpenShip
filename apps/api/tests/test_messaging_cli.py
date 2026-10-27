"""Operator messaging CLI reports topology drift without exposing credentials."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.openship.messaging import cli
from src.openship.messaging.capacity import RetainedSourceScan
from src.openship.messaging.topology import ConsumerResult, StreamResult, TopologyReport


def _report(*, status="ready"):
    return TopologyReport(
        str(uuid.uuid4()),
        (
            StreamResult(
                "OS_EVENTS", status, ("max_bytes",) if status == "drift" else ()
            ),
            StreamResult(
                "OS_ADVISORIES",
                status,
                ("max_bytes",) if status == "drift" else (),
            ),
        ),
        (
            ConsumerResult("OS_RUNS", "run-v1", "ready"),
            ConsumerResult("OS_MAINTENANCE", "maintenance-v1", "ready"),
            ConsumerResult("OS_ADVISORIES", "exhaustion-v1", "ready"),
        ),
    )


async def _ready_capacity():
    return {"status": "ready", "alert": False, "stored_bytes": 0}


def test_bootstrap_check_reports_ready_without_applying(monkeypatch, capsys):
    calls = []

    async def ensure_topology(*, apply):
        calls.append(apply)
        return _report()

    monkeypatch.setattr(cli, "ensure_topology", ensure_topology)

    assert cli.main(["bootstrap", "--check"]) == 0
    assert calls == [False]
    assert json.loads(capsys.readouterr().out)["ready"] is True


def test_bootstrap_apply_reports_drift_without_rewriting_existing_streams(
    monkeypatch, capsys
):
    calls = []

    async def ensure_topology(*, apply):
        calls.append(apply)
        return _report(status="drift")

    monkeypatch.setattr(cli, "ensure_topology", ensure_topology)

    assert cli.main(["bootstrap", "--apply"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert calls == [True]
    assert output["streams"][0]["differences"] == ["max_bytes"]


def test_bootstrap_errors_report_type_only(monkeypatch, capsys):
    async def ensure_topology(*, apply):
        raise RuntimeError("password=must-not-be-printed")

    monkeypatch.setattr(cli, "ensure_topology", ensure_topology)

    assert cli.main(["bootstrap", "--check"]) == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "must-not-be-printed" not in output


def test_reconcile_apply_requires_operator_before_accessing_services(capsys):
    assert cli.main(["reconcile", "--apply"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "refused"
    assert output["errors"] == ["operator_identity_required"]
    assert output["read_only"] is False


def test_reconcile_preview_is_read_only_and_reports_bounded_candidates(
    monkeypatch, capsys
):
    installation_id = uuid.uuid4()
    source = SimpleNamespace(
        seq=23,
        subject=f"openship.{installation_id.hex}.v1.command.run.execute",
        data=b"private command body",
        time=datetime.now(UTC),
    )

    class JetStream:
        async def consumer_info(self, stream, durable):
            return SimpleNamespace(
                paused=False,
                num_ack_pending=1,
                delivered=SimpleNamespace(stream_seq=30),
            )

        async def stream_info(self, stream):
            return SimpleNamespace(
                state=SimpleNamespace(bytes=0),
                config=SimpleNamespace(max_bytes=64 * 1024 * 1024),
            )

    class Client:
        def jetstream(self, *, timeout):
            assert timeout == 5
            return JetStream()

        async def close(self):
            return None

    async def connect(**_kwargs):
        return Client()

    async def scan(*_args, **_kwargs):
        return RetainedSourceScan((source,), 30, 23, False)

    monkeypatch.setattr(
        cli,
        "_database_report",
        lambda: {
            "installation_id": str(installation_id),
            "active": True,
            "admission_open": False,
            "execution_open": False,
        },
    )
    monkeypatch.setattr(cli, "connect_nats", connect)

    async def ready_topology(**_kwargs):
        return _report()

    monkeypatch.setattr(cli, "ensure_topology", ready_topology)
    monkeypatch.setattr(cli, "scan_retained_run_sources", scan)

    class NoApplySupervisor:
        def __init__(self, *_args, **_kwargs):
            pytest.fail("preview must not create an applying supervisor")

    monkeypatch.setattr(cli, "ExhaustionSupervisor", NoApplySupervisor)

    assert cli.main(["reconcile", "--preview", "--limit", "1"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["read_only"] is True
    assert report["through_sequence"] == 30
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["sequence"] == 23
    assert "private command body" not in output


def test_reconcile_apply_refuses_before_scanning_if_a_consumer_is_not_drained(
    monkeypatch, capsys
):
    installation_id = uuid.uuid4()

    class JetStream:
        async def consumer_info(self, stream, durable):
            pending = 1 if stream == "OS_RUNS" else 0
            return SimpleNamespace(paused=True, num_ack_pending=pending)

    class Client:
        def jetstream(self, *, timeout):
            return JetStream()

        async def close(self):
            return None

    async def connect(**_kwargs):
        return Client()

    async def ready_topology(**_kwargs):
        return _report()

    async def no_scan(*_args, **_kwargs):
        pytest.fail("apply must refuse before reading candidate sources")

    monkeypatch.setattr(
        cli,
        "_database_report",
        lambda: {
            "installation_id": str(installation_id),
            "active": True,
            "admission_open": False,
            "execution_open": False,
        },
    )
    monkeypatch.setattr(cli, "connect_nats", connect)
    monkeypatch.setattr(cli, "ensure_topology", ready_topology)
    monkeypatch.setattr(cli, "scan_retained_run_sources", no_scan)

    assert cli.main(["reconcile", "--apply", "--operator", "oncall@example.test"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert report["errors"] == ["consumer_not_drained:OS_RUNS:run-v1"]


@pytest.mark.parametrize("ready, expected", [(True, 0), (False, 1)])
def test_doctor_combines_database_and_topology_status(
    monkeypatch, capsys, ready, expected
):
    monkeypatch.setattr(cli, "_advisory_capacity_report", _ready_capacity)
    monkeypatch.setattr(
        cli,
        "_database_report",
        lambda: {"status": "ready", "active": False},
    )

    async def ensure_topology(*, apply):
        assert apply is False
        return _report(status="ready" if ready else "missing")

    monkeypatch.setattr(cli, "ensure_topology", ensure_topology)

    assert cli.main(["doctor"]) == expected
    output = json.loads(capsys.readouterr().out)
    assert output["database"]["active"] is False
    assert output["ready"] is ready


def test_check_and_doctor_report_missing_consumer_as_not_ready(monkeypatch, capsys):
    report = TopologyReport(
        "00000000-0000-0000-0000-000000000001",
        (
            StreamResult("OS_RUNS", "ready"),
            StreamResult("OS_ADVISORIES", "ready"),
        ),
        (
            ConsumerResult("OS_RUNS", "run-v1", "missing"),
            ConsumerResult("OS_MAINTENANCE", "maintenance-v1", "ready"),
            ConsumerResult("OS_ADVISORIES", "exhaustion-v1", "ready"),
        ),
    )

    async def check_only(*, apply):
        assert not apply
        return report

    monkeypatch.setattr(cli, "ensure_topology", check_only)
    monkeypatch.setattr(cli, "_database_report", lambda: {"status": "ready"})
    monkeypatch.setattr(cli, "_advisory_capacity_report", _ready_capacity)
    assert cli.main(["bootstrap", "--check"]) == 1
    assert json.loads(capsys.readouterr().out)["consumers"][0]["status"] == "missing"
    assert cli.main(["doctor"]) == 1
    assert json.loads(capsys.readouterr().out)["ready"] is False


def test_doctor_reports_capacity_alert_and_fails_readiness(monkeypatch, capsys):
    async def ensure_topology(*, apply):
        assert apply is False
        return _report()

    async def alert_capacity():
        return {
            "status": "alert",
            "alert": True,
            "full": False,
            "stored_bytes": 48 * 1024 * 1024,
        }

    monkeypatch.setattr(cli, "_database_report", lambda: {"status": "ready"})
    monkeypatch.setattr(cli, "ensure_topology", ensure_topology)
    monkeypatch.setattr(cli, "_advisory_capacity_report", alert_capacity)

    assert cli.main(["doctor"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["advisory_capacity"]["alert"] is True
    assert output["ready"] is False
