"""Read-only inventory used before the Phase 6A queue cutover."""

import json
import uuid

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    text,
)
from sqlalchemy.orm import Session

from src.openship.messaging import cli
from src.openship.messaging.migration import plan_cutover


def _phase5_engine():
    metadata = MetaData()
    runs = Table(
        "runs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String(20), nullable=False),
    )
    jobs = Table(
        "run_jobs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("run_id", Uuid, nullable=False),
    )
    events = Table(
        "events",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("conversation_id", Uuid, nullable=False),
        Column("sequence", Integer, nullable=False),
    )
    outbox = Table(
        "outbox",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("event_id", Uuid),
        Column("payload", String, nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return engine, runs, jobs, events, outbox


def test_preview_inventories_phase5_runs_and_duplicate_jobs_without_writes():
    engine, runs, jobs, events, outbox = _phase5_engine()
    queued_id, waiting_id, terminal_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    event_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            [
                {"id": queued_id, "status": "queued"},
                {"id": waiting_id, "status": "waiting_connector"},
                {"id": terminal_id, "status": "completed"},
            ],
        )
        connection.execute(
            jobs.insert(),
            [
                {"id": uuid.uuid4(), "run_id": queued_id},
                {"id": uuid.uuid4(), "run_id": queued_id},
                {"id": uuid.uuid4(), "run_id": waiting_id},
            ],
        )
        connection.execute(
            events.insert(),
            [{"id": event_id, "conversation_id": conversation_id, "sequence": 0}],
        )
        connection.execute(
            outbox.insert(),
            [{"id": uuid.uuid4(), "event_id": event_id, "payload": "private prompt"}],
        )

    cutover_id = uuid.uuid4()
    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=cutover_id)
        assert report.schema_stage == "phase5"
        assert report.cutover_id == cutover_id
        assert report.read_only is True
        assert report.run_counts == {
            "queued": 1,
            "waiting_connector": 1,
            "completed": 1,
        }
        assert report.job_rows == 3
        assert report.duplicate_job_groups == 1
        assert report.duplicate_job_run_ids == (str(queued_id),)
        assert report.event_rows == 1
        assert report.outbox_rows == 1
        assert "private prompt" not in report.to_json()

    with engine.connect() as connection:
        assert len(connection.execute(runs.select()).all()) == 3
        assert len(connection.execute(jobs.select()).all()) == 3
        assert len(connection.execute(events.select()).all()) == 1
        assert len(connection.execute(outbox.select()).all()) == 1
    engine.dispose()


def test_cutover_preview_cli_is_read_only_and_never_prints_outbox_payload(
    monkeypatch, capsys
):
    engine, runs, jobs, events, outbox = _phase5_engine()
    run_id, event_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(runs.insert(), {"id": run_id, "status": "queued"})
        connection.execute(jobs.insert(), {"id": uuid.uuid4(), "run_id": run_id})
        connection.execute(
            events.insert(),
            [{"id": event_id, "conversation_id": conversation_id, "sequence": 0}],
        )
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "event_id": event_id,
                    "payload": "never print this prompt",
                }
            ],
        )

    monkeypatch.setattr(cli, "SessionLocal", lambda: Session(engine))
    cutover_id = uuid.uuid4()
    assert cli.main(["cutover", "preview", "--cutover-id", str(cutover_id)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["read_only"] is True
    assert report["status"] == "preview"
    assert report["cutover_id"] == str(cutover_id)
    assert report["run_counts"] == {"queued": 1}
    assert "never print this prompt" not in output

    with engine.connect() as connection:
        assert len(connection.execute(runs.select()).all()) == 1
        assert len(connection.execute(jobs.select()).all()) == 1
        assert len(connection.execute(events.select()).all()) == 1
        assert len(connection.execute(outbox.select()).all()) == 1
    engine.dispose()


def test_cutover_preview_cli_blocks_duplicate_job_conflicts(monkeypatch, capsys):
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    run_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(runs.insert(), {"id": run_id, "status": "queued"})
        connection.execute(
            jobs.insert(),
            [
                {"id": uuid.uuid4(), "run_id": run_id},
                {"id": uuid.uuid4(), "run_id": run_id},
            ],
        )

    monkeypatch.setattr(cli, "SessionLocal", lambda: Session(engine))
    assert cli.main(["cutover", "preview", "--cutover-id", str(uuid.uuid4())]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["duplicate_job_groups"] == 1
    assert report["duplicate_job_run_ids"] == [str(run_id)]
    engine.dispose()


def test_preview_bounds_conflicting_job_ids_and_detects_expanded_schema():
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    conflicts = [uuid.uuid4() for _ in range(101)]
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            [{"id": run_id, "status": "queued"} for run_id in conflicts],
        )
        connection.execute(
            jobs.insert(),
            [
                {"id": uuid.uuid4(), "run_id": run_id}
                for run_id in conflicts
                for _ in range(2)
            ],
        )
        connection.execute(text("CREATE TABLE messaging_state (id INTEGER)"))

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())
    assert report.schema_stage == "phase6a"
    assert report.duplicate_job_groups == 101
    assert len(report.duplicate_job_run_ids) == 100
    assert report.duplicate_ids_truncated is True
    assert report.status == "blocked"
    engine.dispose()
