"""Operator messaging CLI reports topology drift without exposing credentials."""

import json
import uuid

import pytest

from src.openship.messaging import cli
from src.openship.messaging.topology import StreamResult, TopologyReport


def _report(*, status="ready"):
    return TopologyReport(
        str(uuid.uuid4()),
        (
            StreamResult(
                "OS_EVENTS", status, ("max_bytes",) if status == "drift" else ()
            ),
        ),
    )


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


@pytest.mark.parametrize("ready, expected", [(True, 0), (False, 1)])
def test_doctor_combines_database_and_topology_status(
    monkeypatch, capsys, ready, expected
):
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
