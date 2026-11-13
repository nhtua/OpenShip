"""PostgreSQL proves a restore keeps the recovery ask its task had already parked.

The same lane fixtures drive the compiled v5 graph against a real database, and
the restore is the real lifecycle one: archive the project the root belongs to,
then restore it. PostgreSQL records decide what the root may do afterwards, so
the open recovery request, its report, the skipped/verified state, the recorded
command and the transport epoch must all come back exactly as they were. A
restore may not answer a question, may not let the root repeat or continue on
evidence that did not survive it, and may not repin a snapshot it cannot read.
"""

from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.contracts import decode_execution_config
from src.openship.messaging.models import MessageTransportState
from src.openship.runs.models import Outbox, Run
from src.openship.runs.resume import resume_task
from src.openship.runs.schemas import ResumeChatPythonRequest
from src.openship.runs.tasks.contracts import TaskDecisionCommand
from src.openship.runs.tasks.decisions import apply_decision
from src.openship.runs.tasks.models import TaskDecisionRecord
from src.openship.workspace.models import Project
from src.openship.workspace.projects import archive_project, restore_project
from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.test_task_graph import Lane, _calls, _durable, _journey, _run_state

pytestmark = [pytest.mark.postgres]

LANE_DATABASE = "phase7a_task_restore_lane"


@pytest.fixture(scope="module")
def pg_restore_sessions(tmp_path_factory):
    """One disposable PostgreSQL database at the current schema head."""
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail(
            "TEST_POSTGRES_URL is required for the restore PostgreSQL lane",
            pytrace=False,
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

    ini = _alembic_ini(tmp_path_factory.mktemp("phase7a-task-restore"), lane_url)
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


def _crash_then_park(lane):
    """Run until the admitted call dies, then let a restarted worker park the ask."""
    with pytest.raises(Exception) as interrupted:
        lane.invoke()
    assert interrupted.value.code == "execution_interrupted"
    lane.rehold()
    parked = lane.invoke()
    assert parked["parked"] == "recovery"


def _ledger(lane, sessions) -> dict:
    """Everything this database says about the root, read with no graph state."""
    records = _durable(lane.run_id, sessions)
    with sessions() as db:
        ask = (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, kind="recovery", state="open")
            .all()
        )
        assert len(ask) == 1, "the root must hold exactly one open recovery ask"
        report = json.loads(ask[0].request_json)["recovery_report"]
        transport = db.get(MessageTransportState, 1)
        return {
            "ask": (
                str(ask[0].request_id),
                ask[0].kind,
                ask[0].plan_revision,
                ask[0].expected_digest,
                ask[0].state,
            ),
            "report": (
                report["plan_revision"],
                report["plan_digest"],
                report["step_id"],
                report["attempt"],
                str(report["operation_id"]),
                tuple(report["eligible_actions"]),
                tuple(report["evidence_refs"]),
            ),
            "epoch": transport.transport_epoch,
            "installation_id": transport.installation_id,
            "command_rows": db.query(Outbox).filter_by(run_id=lane.run_id).count(),
            "progress": records["progress"],
        }


def test_restore_waiting_recovery_report_and_private_continuation(
    pg_restore_sessions, monkeypatch
):
    """Archive and restore keep the same ask, its report, its private owner."""
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[*_journey()],
        crash=True,
        sessions=pg_restore_sessions,
    )
    _crash_then_park(lane)
    before = _ledger(lane, pg_restore_sessions)
    assert before["progress"].pending_decision.recovery_report.step_id == "fetch"
    assert before["report"][4] == str(
        _durable(lane.run_id, pg_restore_sessions)["operations"][0].id
    )
    assert _run_state(lane.run_id, pg_restore_sessions)["status"] == "waiting_input"

    # Archiving needs another active project: the lifecycle never archives the
    # only one, and the archive itself must not touch the task ledger.
    with pg_restore_sessions() as db:
        sibling = Project(
            owner_user_id=lane.user_id, name="the archive needs an active sibling"
        )
        db.add(sibling)
        db.commit()
        archived = archive_project(
            db, owner_id=lane.user_id, project_id=lane.project_id
        )
        db.commit()
        assert archived.archived_at is not None
    assert _ledger(lane, pg_restore_sessions) == before
    assert _run_state(lane.run_id, pg_restore_sessions)["status"] == "waiting_input"

    # Restoring re-opens the same question: same request identity, same report,
    # same skipped/verified state, same recorded command and same epoch.
    with pg_restore_sessions() as db:
        restored = restore_project(
            db, owner_id=lane.user_id, project_id=lane.project_id
        )
        db.commit()
        assert restored.archived_at is None
    after = _ledger(lane, pg_restore_sessions)
    assert after["ask"] == before["ask"], "restore keeps the same recovery request"
    assert after["report"] == before["report"], "restore keeps the same diagnosis"
    assert after["command_rows"] == before["command_rows"]
    assert after["epoch"] == before["epoch"]
    assert after["installation_id"] == before["installation_id"]
    assert (
        after["progress"].skipped_steps
        == before["progress"].skipped_steps
        == before["progress"].skipped_steps
    )
    assert after["progress"].verified_steps == before["progress"].verified_steps, (
        "restore invents no verified work"
    )
    assert after["progress"].call_bindings == before["progress"].call_bindings, (
        "restore keeps the interrupted call's binding"
    )
    assert after["progress"].counters == before["progress"].counters, (
        "restore neither spends nor refunds the root's budget"
    )
    assert (
        after["progress"].pending_decision.recovery_report.eligible_actions
        == before["progress"].pending_decision.recovery_report.eligible_actions
    )
    assert (
        _durable(lane.run_id, pg_restore_sessions)["operations"][0].status == "admitted"
    ), "restore does not settle a call that only may have dispatched"
    assert _run_state(lane.run_id, pg_restore_sessions)["status"] == "waiting_input"

    # The continuation stays private to the root's owner: a foreign principal
    # cannot read the root at all, so it can never answer the ask.
    with pg_restore_sessions() as db:
        foreign = User(
            username=f"foreign_{_uuid.uuid4().hex[:8]}",
            email=f"foreign-{_uuid.uuid4().hex[:8]}@example.test",
            password_hash="fixture-hash",
        )
        db.add(foreign)
        db.commit()
        with pytest.raises(HTTPException) as refused:
            resume_task(
                db,
                principal_id=foreign.id,
                run_id=lane.run_id,
                command=ResumeChatPythonRequest(
                    mode="last_step", client_request_id=_uuid.uuid4()
                ),
            )
        assert refused.value.status_code == 404
        assert refused.value.detail["error"]["code"] == "run_not_found"
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, kind="recovery", state="open")
            .count()
            == 1
        )
    assert _run_state(lane.run_id, pg_restore_sessions)["status"] == "waiting_input"


def test_missing_restored_artifact_or_snapshot_blocks(pg_restore_sessions, monkeypatch):
    """Evidence that did not survive the restore cannot be answered or repinned."""
    lane = Lane(
        monkeypatch,
        mode="auto",
        behaviors=[*_journey()],
        crash=True,
        sessions=pg_restore_sessions,
    )

    # The root pins one selected input before its call is admitted, the way an
    # admission would: the ask below is about that pinned snapshot.
    with pg_restore_sessions() as db:
        root = db.query(Run).filter_by(id=lane.run_id).one()
        execution = decode_execution_config(root.execution_configuration)
        artifact = Artifact(
            project_id=lane.project_id,
            conversation_id=lane.conversation_id,
            originating_run_id=lane.run_id,
            kind="upload",
            media_type="text/plain",
            display_name="observations.txt",
            size_bytes=12,
            sha256="2" * 64,
            relative_storage_key=f"restore-lane/{_uuid.uuid4().hex}",
            status="ready",
            source="upload",
        )
        db.add(artifact)
        db.flush()
        pinned = ArtifactRef(
            id=artifact.id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type=artifact.media_type,
        )
        root.execution_configuration = execution.model_copy(
            update={"selected_artifacts": (pinned,)}
        ).model_dump_json()
        db.commit()
        artifact_id = artifact.id

    _crash_then_park(lane)
    before = _ledger(lane, pg_restore_sessions)
    assert before["progress"].pending_decision.recovery_report.step_id == "fetch"

    # The same choice is admitted while the pinned input is still there; the
    # transaction is rolled back so the ask stays open for the case below.
    with pg_restore_sessions() as db:
        offered = (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, state="open")
            .one()
        )
        accepted = apply_decision(
            db,
            principal_id=lane.user_id,
            run_id=lane.run_id,
            command=TaskDecisionCommand(
                client_request_id=_uuid.uuid4(),
                request_id=offered.request_id,
                plan_revision=offered.plan_revision,
                expected_digest=offered.expected_digest,
                action="repeat_step",
            ),
            now=datetime.now(UTC),
        )
        assert accepted.pending_decision is None, "available evidence admits the choice"
        db.rollback()
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, state="open")
            .count()
            == 1
        )

    # The restore lost those bytes: the ledger still names the artifact, and it
    # is no longer available, so the same choice is refused.
    with pg_restore_sessions() as db:
        lost = db.query(Artifact).filter_by(id=artifact_id).one()
        lost.status = "unavailable"
        db.commit()
        offered = (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, state="open")
            .one()
        )
        assert (
            str(offered.request_id),
            offered.plan_revision,
            offered.expected_digest,
        ) == (
            before["ask"][0],
            before["ask"][2],
            before["ask"][3],
        ), "losing evidence did not replace the ask"
        with pytest.raises(HTTPException) as refused:
            apply_decision(
                db,
                principal_id=lane.user_id,
                run_id=lane.run_id,
                command=TaskDecisionCommand(
                    client_request_id=_uuid.uuid4(),
                    request_id=offered.request_id,
                    plan_revision=offered.plan_revision,
                    expected_digest=offered.expected_digest,
                    action="repeat_step",
                ),
                now=datetime.now(UTC),
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "execution_unavailable"
        db.rollback()

    assert _ledger(lane, pg_restore_sessions)["ask"] == before["ask"], (
        "a refused choice may not answer or replace the ask"
    )
    assert _ledger(lane, pg_restore_sessions)["report"] == before["report"]
    assert _calls(lane) == ["download_file"], "refusing a choice dispatched nothing"
    records = _durable(lane.run_id, pg_restore_sessions)
    assert records["attempts"] == [], "a blocked choice funds no attempt"
    assert len(records["operations"]) == 1, "a blocked choice funds no operation"

    # A snapshot this installation cannot read is refused outright: the resume
    # never repins one from today's settings, and it admits no new root.
    with pg_restore_sessions() as db:
        root = db.query(Run).filter_by(id=lane.run_id).one()
        root.execution_configuration = None
        db.commit()
        with pytest.raises(HTTPException) as refused:
            resume_task(
                db,
                principal_id=lane.user_id,
                run_id=lane.run_id,
                command=ResumeChatPythonRequest(
                    mode="last_step", client_request_id=_uuid.uuid4()
                ),
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["error"]["code"] == "task_resume_unavailable"
        assert (
            db.query(Run).filter_by(conversation_id=lane.conversation_id).count() == 1
        ), "an unreadable snapshot admits no continuation root"
        assert db.get(Run, lane.run_id).execution_configuration is None
        assert (
            db.query(TaskDecisionRecord)
            .filter_by(run_id=lane.run_id, kind="recovery", state="open")
            .count()
            == 1
        )
    assert _run_state(lane.run_id, pg_restore_sessions)["status"] == "waiting_input"
