"""PostgreSQL proves a recovery ask survives a lost approval.

The same lane fixtures drive the compiled v5 graph against a real database: a
worker that died after its admit, the durable ask the next worker offers, an
approval whose transaction never reached this database, and the re-entry that
has to re-prompt exactly that ask. PostgreSQL records decide what a resumed
worker may do, so a rolled-back choice may neither answer itself nor fund a
second effect.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Outbox
from src.openship.runs.tasks.contracts import TaskDecisionCommand
from src.openship.runs.tasks.decisions import apply_decision
from src.openship.runs.tasks.models import TaskDecisionRecord
from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.test_task_graph import (
    Lane,
    _call,
    _calls,
    _durable,
    _journey,
    _run_state,
)

pytestmark = [pytest.mark.postgres]

LANE_DATABASE = "phase7a_task_recovery_lane"


@pytest.fixture(scope="module")
def pg_recovery_sessions(tmp_path_factory):
    """One disposable PostgreSQL database at the current schema head."""
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip(
            "no TEST_POSTGRES_URL; the recovery PostgreSQL proof is blocked on "
            "a disposable PostgreSQL service"
        )

    lane_url = (
        make_url(bounded_postgres_url(service_url))
        .set(database=LANE_DATABASE)
        .render_as_string(hide_password=False)
    )
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{LANE_DATABASE}"'))
    finally:
        admin.dispose()

    ini = _alembic_ini(tmp_path_factory.mktemp("phase7a-task-recovery"), lane_url)
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


def test_approval_rollback_reprompts_same_request(pg_recovery_sessions, monkeypatch):
    """An unrecorded approval leaves the ask open, and re-entry re-prompts it."""
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[
            *_journey()[:3],
            _call(
                "provider-repeat",
                "download_file",
                {"url": "https://observations.test/day.csv"},
            ),
            _journey()[3],
        ],
        crash=True,
        sessions=pg_recovery_sessions,
    )
    with pytest.raises(ProviderError) as refused:
        lane.invoke()
    assert refused.value.code == "execution_interrupted"

    # The admit is committed in this database and the worker died: nothing
    # settled, and this database cannot say whether the call ran.
    interrupted = _durable(lane.run_id, pg_recovery_sessions)
    assert interrupted["attempts"] == []
    assert len(interrupted["operations"]) == 1
    dead = interrupted["operations"][0]
    assert dead.status == "admitted"
    assert interrupted["progress"].pending_decision is None

    # A restarted worker re-reads the same lease: the uncertainty becomes one
    # durable ask, and the diagnosis itself dispatched and asked nothing.
    lane.rehold()
    parked = lane.invoke()
    assert parked["parked"] == "recovery"
    ask = _durable(lane.run_id, pg_recovery_sessions)["progress"].pending_decision
    assert ask is not None and ask.kind == "recovery"
    assert ask.recovery_report is not None
    assert ask.recovery_report.step_id == "fetch"
    assert ask.recovery_report.attempt == 1
    assert ask.recovery_report.operation_id == dead.id
    identity = (ask.request_id, ask.plan_revision, ask.expected_digest)
    assert lane.transport.requests == 3
    assert _calls(lane) == ["download_file"]
    assert _run_state(lane.run_id, pg_recovery_sessions)["status"] == "waiting_input"

    # The user's approval started and rolled back: this database still holds no
    # choice, no command record and no outbox delivery for it.
    with pg_recovery_sessions() as db:
        open_rows = db.query(TaskDecisionRecord).filter_by(
            run_id=lane.run_id, state="open"
        )
        assert open_rows.count() == 1, "the run must offer exactly one open request"
        offered = open_rows.one()
        assert (
            offered.request_id,
            offered.plan_revision,
            offered.expected_digest,
        ) == identity
        apply_decision(
            db,
            principal_id=lane.user_id,
            run_id=lane.run_id,
            command=TaskDecisionCommand(
                client_request_id=uuid.uuid4(),
                request_id=offered.request_id,
                plan_revision=offered.plan_revision,
                expected_digest=offered.expected_digest,
                action="repeat_step",
            ),
            now=datetime.now(UTC),
        )
        db.rollback()
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, state="open")
            .count()
            == 1
        )
        assert (
            db.query(Outbox)
            .filter_by(run_id=lane.run_id, destination="command")
            .count()
            == 0
        )
        assert (
            _run_state(lane.run_id, pg_recovery_sessions)["status"] == "waiting_input"
        )

    # The next worker to read these records re-prompts the same ask: a lost
    # approval may not answer itself, and it may not invent a second question.
    lane.rehold()
    again = lane.invoke()
    assert again["parked"] == "recovery"
    reasked = _durable(lane.run_id, pg_recovery_sessions)["progress"].pending_decision
    assert reasked is not None and reasked.kind == "recovery"
    assert (
        reasked.request_id,
        reasked.plan_revision,
        reasked.expected_digest,
    ) == identity
    assert reasked.recovery_report is not None
    assert (
        reasked.recovery_report.step_id,
        reasked.recovery_report.attempt,
        reasked.recovery_report.operation_id,
    ) == ("fetch", 1, dead.id)
    after = _durable(lane.run_id, pg_recovery_sessions)
    assert len(after["decisions"]) == 1
    assert after["progress"].call_bindings == interrupted["progress"].call_bindings
    assert after["operations"] == interrupted["operations"]
    assert after["attempts"] == []
    assert _run_state(lane.run_id, pg_recovery_sessions)["status"] == "waiting_input"
    # Nothing ran while the ask was offered a second time: the diagnosis asked
    # the provider nothing at all, so the sequence never advanced.
    assert lane.transport.requests == 3
    assert _calls(lane) == ["download_file"]
