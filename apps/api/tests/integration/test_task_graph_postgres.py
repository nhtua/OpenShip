"""PostgreSQL proves the v5 task graph's records survive a restarted worker.

The same lane fixtures drive the compiled graph against a real database: plan
revision, call binding, committed operation, step attempt and decision all
come from PostgreSQL records, so a resumed invocation can only act on what
this database says.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.providers.errors import ProviderError
from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.test_task_graph import (
    CAPABILITY_IDS,
    Lane,
    _durable,
    _journey,
    _run_state,
)

pytestmark = [pytest.mark.postgres]

pytestmark = [pytest.mark.postgres]

LANE_DATABASE = "phase7a_task_graph_lane"


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    """One disposable PostgreSQL database at the current schema head."""
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip(
            "no TEST_POSTGRES_URL; the task-graph PostgreSQL proof is blocked on "
            "a disposable PostgreSQL service"
        )

    lane_url = make_url(bounded_postgres_url(service_url)).set(
        database=LANE_DATABASE
    ).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{LANE_DATABASE}"'))
    finally:
        admin.dispose()

    ini = _alembic_ini(tmp_path_factory.mktemp("phase7a-task-graph"), lane_url)
    built = _run_alembic(ini, "upgrade", "head")
    assert built.returncode == 0, built.stderr
    engine = create_engine(lane_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
        finally:
            admin.dispose()


def test_pg_copilot_parks_plan_then_confirms_once(pg_sessions, monkeypatch):
    lane = Lane(monkeypatch, mode="copilot", behaviors=_journey(), sessions=pg_sessions)
    parked = lane.invoke()
    assert parked["parked"] == "plan_confirmation"
    assert len(lane.gateway.calls) == 0

    before = _durable(lane.run_id, pg_sessions)
    assert before["operations"] == []
    assert before["attempts"] == []
    assert before["progress"].status == "waiting"
    assert before["progress"].pending_decision is not None
    assert len(before["decisions"]) == 1
    assert _run_state(lane.run_id, pg_sessions)["status"] == "waiting_plan"

    lane.confirm_pending()
    lane.resume_queue("copilot-worker")
    lane.invoke()

    assert _durable(lane.run_id, pg_sessions)["progress"].pending_decision is None
    assert _run_state(lane.run_id, pg_sessions)["status"] == "running"
    assert len(lane.gateway.calls) == len(CAPABILITY_IDS)


def test_pg_restart_replays_committed_results(pg_sessions, monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey(), sessions=pg_sessions)
    lane.invoke()
    before_restart = _durable(lane.run_id, pg_sessions)
    assert _run_state(lane.run_id, pg_sessions)["status"] == "running"
    assert len(before_restart["operations"]) == 2

    # The restarted worker holds the same lease and re-invokes the graph: the
    # attempts and operations already in this database are the only work it
    # sees, so nothing already attempted runs again.
    lane.rehold()
    outcome = lane.invoke()

    after_restart = _durable(lane.run_id, pg_sessions)
    assert {row.id for row in after_restart["operations"]} == {
        row.id for row in before_restart["operations"]
    }
    assert [row.step_id for row in after_restart["attempts"]] == [
        row.step_id for row in before_restart["attempts"]
    ]
    assert len(lane.gateway.calls) == 2
    # The restarted invocation inspected nothing again: both steps' verdicts
    # are already durable in this database, so the switch ends the run from
    # these records alone, and the task is completed only because those
    # records cover every plan step.
    assert outcome["cursor"].get("verification_inspected") is None
    assert outcome["cursor"]["next_action"] == "finish"
    assert after_restart["progress"].verified_steps == ("fetch", "analyze")
    assert after_restart["task_status"] == before_restart["task_status"]
    assert after_restart["task_status"] == "completed"


def test_pg_binding_survives_dispatch_without_result(pg_sessions, monkeypatch):
    lane = Lane(monkeypatch, mode="auto", behaviors=_journey(), crash=True, sessions=pg_sessions)
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    interrupted = _durable(lane.run_id, pg_sessions)
    assert len(interrupted["operations"]) == 1
    assert interrupted["operations"][0].status == "admitted"
    assert interrupted["attempts"] == []
    assert len(interrupted["progress"].call_bindings) == 1
    assert (
        interrupted["progress"].call_bindings[-1].logical_call_id
        == interrupted["operations"][0].logical_call_id
    )
    assert interrupted["task_status"] == "planned"

    lane.rehold()
    resumed = lane.invoke()
    assert resumed["parked"] == "recovery"
    assert len(lane.gateway.calls) == 1
    after_resume = _durable(lane.run_id, pg_sessions)
    assert after_resume["attempts"] == []
    assert after_resume["progress"].call_bindings == interrupted["progress"].call_bindings
    assert len(after_resume["operations"]) == 1
    assert after_resume["task_status"] == "waiting"
    assert _run_state(lane.run_id, pg_sessions)["status"] == "waiting_input"
    pending = after_resume["progress"].pending_decision
    assert pending is not None
    assert pending.kind == "recovery"
    assert pending.recovery_report is not None
