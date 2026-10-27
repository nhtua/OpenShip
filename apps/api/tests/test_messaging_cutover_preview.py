"""Read-only inventory used before the Phase 6A queue cutover."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    text,
)
from sqlalchemy.orm import Session

from src.openship.database.models import Base
from src.openship.messaging import cli, cutover
from src.openship.messaging.cutover import (
    apply_cutover,
    apply_rollback,
    open_admission_gate,
    open_execution_gate,
    plan_rollback,
    verify_cutover,
)
from src.openship.messaging.migration import plan_cutover
from src.openship.messaging.models import MessageTransportState, MessagingCutover
from src.openship.messaging.relay import _record_envelope, claim_outbox_batch
from src.openship.runs.models import Event, Outbox, Run, RunJob


def _phase5_engine():
    metadata = MetaData()
    runs = Table(
        "runs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String(20), nullable=False),
        Column("conversation_id", Uuid),
        Column("project_id", Uuid),
        Column("fence", Integer, nullable=False, default=0),
        Column("kind", String(20), nullable=False, default="chat"),
        Column("graph_version", Integer, nullable=False, default=1),
        Column("provider_started_at", DateTime(timezone=True)),
    )
    jobs = Table(
        "run_jobs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("run_id", Uuid, nullable=False),
        Column("fence", Integer, nullable=False, default=0),
        Column("owner_id", String(50)),
        Column("lease_until", DateTime(timezone=True)),
    )
    events = Table(
        "events",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("conversation_id", Uuid, nullable=False),
        Column("run_id", Uuid),
        Column("sequence", Integer, nullable=False),
        Column("type", String(100), nullable=False, server_default="event.created"),
    )
    outbox = Table(
        "outbox",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("conversation_id", Uuid),
        Column("event_id", Uuid),
        Column("type", String(100), nullable=False, server_default="legacy"),
        Column("payload", String, nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        Column("processed_at", DateTime(timezone=True)),
    )
    Table(
        "provider_attempts",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("run_id", Uuid, nullable=False),
    )
    Table(
        "tool_operations",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("run_id", Uuid, nullable=False),
        Column("status", String(24), nullable=False),
    )
    Table(
        "tool_attempts",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("operation_id", Uuid, nullable=False),
        Column("outcome", String(24), nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return engine, runs, jobs, events, outbox


def _enable_phase6a(engine, jobs, outbox):
    installation_id, transport_epoch = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        jobs.append_column(
            Column("dispatch_generation", Integer, nullable=False, server_default="1")
        )
        connection.execute(
            text(
                "ALTER TABLE run_jobs ADD COLUMN dispatch_generation "
                "INTEGER NOT NULL DEFAULT 1"
            )
        )
        phase6a_columns = (
            ("destination", String(20), "VARCHAR(20)"),
            ("schema_version", Integer(), "INTEGER"),
            ("message_id", Uuid(), "CHAR(32)"),
            ("subject", String(255), "VARCHAR(255)"),
            ("run_id", Uuid(), "CHAR(32)"),
            ("dispatch_generation", Integer(), "INTEGER"),
            ("transport_epoch", Uuid(), "CHAR(32)"),
            ("failure_id", Uuid(), "CHAR(32)"),
            ("maintenance_task_id", Uuid(), "CHAR(32)"),
        )
        for name, column_type, sql_type in phase6a_columns:
            outbox.append_column(Column(name, column_type))
            connection.execute(text(f"ALTER TABLE outbox ADD COLUMN {name} {sql_type}"))
        connection.execute(
            text(
                "CREATE TABLE messaging_state (id INTEGER, installation_id CHAR(32), "
                "transport_epoch CHAR(32))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE message_dispositions (message_id CHAR(32), "
                "disposition VARCHAR(32), error_code VARCHAR(80), "
                "payload_digest VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO messaging_state (id, installation_id, transport_epoch) "
                "VALUES (1, :installation_id, :transport_epoch)"
            ),
            {
                "installation_id": installation_id.hex,
                "transport_epoch": transport_epoch.hex,
            },
        )
    return installation_id, transport_epoch


def _full_phase6a_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            MessageTransportState(
                id=1,
                installation_id=uuid.uuid4(),
                transport_epoch=uuid.uuid4(),
                active=False,
                admission_open=True,
                execution_open=True,
            )
        )
        db.commit()
    return engine


def _seed_queued_run(
    engine,
    runs,
    jobs,
    outbox,
    *,
    message_id,
    disposition=None,
    include_command=True,
):
    run_id, conversation_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    generation = 4
    installation_id, transport_epoch = _enable_phase6a(engine, jobs, outbox)
    expected_message_id = uuid.uuid5(
        installation_id,
        f"run.execute:{run_id}:{generation}:{transport_epoch}",
    )
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            {
                "id": run_id,
                "status": "queued",
                "conversation_id": conversation_id,
                "project_id": project_id,
            },
        )
        connection.execute(
            jobs.insert(),
            {
                "id": uuid.uuid4(),
                "run_id": run_id,
                "dispatch_generation": generation,
            },
        )
        if include_command:
            connection.execute(
                outbox.insert(),
                {
                    "id": uuid.uuid4(),
                    "conversation_id": conversation_id,
                    "type": "run.execute",
                    "payload": json.dumps(
                        {
                            "run_id": str(run_id),
                            "dispatch_generation": generation,
                            "transport_epoch": str(transport_epoch),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "created_at": datetime(2026, 10, 27, tzinfo=UTC),
                    "destination": "command",
                    "schema_version": 1,
                    "message_id": message_id or expected_message_id,
                    "subject": f"openship.{installation_id.hex}.v1.command.run.execute",
                    "run_id": run_id,
                    "dispatch_generation": generation,
                    "transport_epoch": transport_epoch,
                },
            )
        if disposition is not None:
            connection.execute(
                text(
                    "INSERT INTO message_dispositions "
                    "(message_id, disposition, error_code, payload_digest) "
                    "VALUES (:message_id, :disposition, 'active_claim', :digest)"
                ),
                {
                    "message_id": expected_message_id.hex,
                    "disposition": disposition,
                    "digest": "0" * 64,
                },
            )
    return (
        run_id,
        expected_message_id,
        installation_id,
        transport_epoch,
        conversation_id,
        project_id,
    )


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
        assert report.event_notification_rows == 0
        assert report.missing_event_notification_rows == 1
        assert report.event_identity_conflicts == 0
        assert report.orphan_event_references == 0
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


def test_cutover_apply_cli_requires_operator_and_writer_shutdown_attestation():
    cutover_id = str(uuid.uuid4())
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            ["cutover", "apply", "--cutover-id", cutover_id, "--writers-stopped"]
        )

    args = cli._parser().parse_args(
        [
            "cutover",
            "apply",
            "--cutover-id",
            cutover_id,
            "--operator",
            "operator@example.test",
            "--writers-stopped",
        ]
    )
    assert args.operator == "operator@example.test"
    assert args.writers_stopped is True


def test_preview_bounds_conflicting_job_ids_and_detects_expanded_schema():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    conflicts = [uuid.uuid4() for _ in range(101)]
    _installation_id, _transport_epoch = _enable_phase6a(engine, jobs, outbox)
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

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())
    assert report.schema_stage == "phase6a"
    assert report.duplicate_job_groups == 101
    assert len(report.duplicate_job_run_ids) == 100
    assert report.duplicate_ids_truncated is True
    assert report.status == "blocked"
    engine.dispose()


def test_preview_reuses_an_exact_current_command_identity():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    run_id, expected_message_id, *_ = _seed_queued_run(
        engine, runs, jobs, outbox, message_id=None
    )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"queued_existing_command": 1}
    assert report.blocked_run_groups == 0
    assert report.status == "preview"
    assert str(expected_message_id) not in report.to_json()
    assert str(run_id) not in report.to_json()
    engine.dispose()


def test_preview_blocks_a_current_command_with_a_mismatched_message_id():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    run_id, expected_message_id, *_ = _seed_queued_run(
        engine, runs, jobs, outbox, message_id=uuid.uuid4()
    )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"blocked_command_identity": 1}
    assert report.blocked_run_groups == 1
    assert str(expected_message_id) not in report.to_json()
    assert str(run_id) in report.blocked_run_ids
    engine.dispose()


def test_preview_blocks_an_exact_command_with_an_unsettled_disposition():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    run_id, _expected_message_id, *_ = _seed_queued_run(
        engine,
        runs,
        jobs,
        outbox,
        message_id=None,
        disposition="claimed",
    )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"blocked_command_identity": 1}
    assert report.blocked_run_groups == 1
    assert str(run_id) in report.blocked_run_ids
    engine.dispose()


def test_preview_blocks_an_orphaned_disposition_for_the_current_identity():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    run_id, _expected_message_id, *_ = _seed_queued_run(
        engine,
        runs,
        jobs,
        outbox,
        message_id=None,
        disposition="claimed",
        include_command=False,
    )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"blocked_command_identity": 1}
    assert report.blocked_run_groups == 1
    assert str(run_id) in report.blocked_run_ids
    engine.dispose()


def test_preview_accepts_an_exact_closed_gate_hold_for_the_current_command():
    engine, runs, jobs, _events, outbox = _phase5_engine()
    (
        run_id,
        expected_message_id,
        installation_id,
        transport_epoch,
        conversation_id,
        project_id,
    ) = _seed_queued_run(engine, runs, jobs, outbox, message_id=None)
    with engine.connect() as connection:
        outbox_row = SimpleNamespace(
            **connection.execute(outbox.select().where(outbox.c.run_id == run_id))
            .mappings()
            .one()
        )
    envelope = _record_envelope(
        outbox_row,
        SimpleNamespace(
            installation_id=installation_id,
            transport_epoch=transport_epoch,
        ),
        uuid.UUID(int=0),
        event=None,
        run=SimpleNamespace(
            id=run_id,
            conversation_id=conversation_id,
            project_id=project_id,
        ),
        job=SimpleNamespace(dispatch_generation=4),
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO message_dispositions "
                "(message_id, disposition, error_code, payload_digest) "
                "VALUES (:message_id, 'deferred', 'execution_closed', :digest)"
            ),
            {
                "message_id": expected_message_id.hex,
                "digest": hashlib.sha256(envelope.body).hexdigest(),
            },
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"queued_existing_command": 1}
    assert report.blocked_run_groups == 0
    engine.dispose()


def test_preview_classifies_runs_across_bounded_scan_batches():
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    run_ids = [uuid.uuid4() for _ in range(501)]
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            [{"id": run_id, "status": "queued"} for run_id in run_ids],
        )
        connection.execute(
            jobs.insert(),
            [{"id": uuid.uuid4(), "run_id": run_id} for run_id in run_ids],
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_counts == {"queued": 501}
    assert report.run_disposition_counts == {"queued_eligible": 501}
    assert report.blocked_run_groups == 0
    engine.dispose()


def test_preview_classifies_run_dispositions_with_safety_checks_before_status():
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    eligible_id, waiting_id, terminal_id, created_id, running_id = (
        uuid.uuid4() for _ in range(5)
    )
    (
        owned_terminal_id,
        expired_lease_id,
        fence_mismatch_id,
        dispatch_history_id,
        tool_history_id,
    ) = (uuid.uuid4() for _ in range(5))
    provider_marker_id, unsupported_id = uuid.uuid4(), uuid.uuid4()
    duplicate_id, missing_job_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            [
                {"id": eligible_id, "status": "queued"},
                {"id": waiting_id, "status": "waiting_connector"},
                {"id": terminal_id, "status": "completed"},
                {"id": created_id, "status": "created"},
                {"id": running_id, "status": "running"},
                {"id": owned_terminal_id, "status": "completed"},
                {"id": expired_lease_id, "status": "waiting_connector"},
                {"id": fence_mismatch_id, "status": "queued", "fence": 3},
                {"id": dispatch_history_id, "status": "queued"},
                {"id": tool_history_id, "status": "queued"},
                {"id": provider_marker_id, "status": "queued"},
                {"id": unsupported_id, "status": "legacy_unknown"},
                {"id": duplicate_id, "status": "queued"},
                {"id": missing_job_id, "status": "queued"},
            ],
        )
        connection.execute(
            jobs.insert(),
            [
                {"id": uuid.uuid4(), "run_id": run_id, "fence": fence}
                for run_id, fence in (
                    (eligible_id, 0),
                    (waiting_id, 0),
                    (terminal_id, 0),
                    (created_id, 0),
                    (running_id, 0),
                    (owned_terminal_id, 0),
                    (expired_lease_id, 0),
                    (fence_mismatch_id, 2),
                    (dispatch_history_id, 0),
                    (tool_history_id, 0),
                    (provider_marker_id, 0),
                    (unsupported_id, 0),
                    (duplicate_id, 0),
                    (duplicate_id, 0),
                )
            ],
        )
        connection.execute(
            jobs.update()
            .where(jobs.c.run_id == owned_terminal_id)
            .values(
                owner_id="old-worker", lease_until=datetime(2099, 1, 1, tzinfo=UTC)
            ),
        )
        connection.execute(
            jobs.update()
            .where(jobs.c.run_id == expired_lease_id)
            .values(lease_until=datetime(2020, 1, 1, tzinfo=UTC)),
        )
        connection.execute(
            runs.update()
            .where(runs.c.id == provider_marker_id)
            .values(provider_started_at=datetime(2026, 10, 27, tzinfo=UTC)),
        )
        connection.execute(
            text("INSERT INTO provider_attempts (id, run_id) VALUES (:id, :run_id)"),
            {"id": uuid.uuid4().hex, "run_id": dispatch_history_id.hex},
        )
        operation_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO tool_operations (id, run_id, status) "
                "VALUES (:id, :run_id, 'interrupted')"
            ),
            {"id": operation_id.hex, "run_id": tool_history_id.hex},
        )
        connection.execute(
            text(
                "INSERT INTO tool_attempts (id, operation_id, outcome) "
                "VALUES (:id, :operation_id, 'interrupted')"
            ),
            {"id": uuid.uuid4().hex, "operation_id": operation_id.hex},
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {
        "queued_eligible": 1,
        "waiting_preserved": 1,
        "terminal_preserved": 1,
        "requires_recovery": 2,
        "blocked_owned": 2,
        "blocked_fence_mismatch": 1,
        "blocked_dispatch_history": 3,
        "blocked_duplicate_job": 1,
        "blocked_missing_job": 1,
        "unsupported_status": 1,
    }
    assert report.status == "blocked"
    assert report.blocked_run_groups == 11
    assert {eligible_id, waiting_id, terminal_id}.isdisjoint(
        uuid.UUID(run_id) for run_id in report.blocked_run_ids
    )
    engine.dispose()


def test_preview_does_not_skip_waiting_run_with_active_execution_record():
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    run_id, operation_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            {"id": run_id, "status": "waiting_connector"},
        )
        connection.execute(jobs.insert(), {"id": uuid.uuid4(), "run_id": run_id})
        connection.execute(
            text(
                "INSERT INTO tool_operations (id, run_id, status) "
                "VALUES (:id, :run_id, 'admitted')"
            ),
            {"id": operation_id.hex, "run_id": run_id.hex},
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"blocked_active_execution": 1}
    assert report.blocked_run_groups == 1
    engine.dispose()


def test_preview_blocks_queued_run_with_unsupported_graph_version():
    engine, runs, jobs, _events, _outbox = _phase5_engine()
    run_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            runs.insert(),
            {"id": run_id, "status": "queued", "graph_version": 99},
        )
        connection.execute(jobs.insert(), {"id": uuid.uuid4(), "run_id": run_id})

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.run_disposition_counts == {"blocked_unsupported_graph": 1}
    assert report.blocked_run_groups == 1
    assert str(run_id) in report.blocked_run_ids
    engine.dispose()


def test_preview_reports_missing_phase5_event_notifications_for_backfill():
    engine, _runs, _jobs, events, outbox = _phase5_engine()
    event_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            events.insert(),
            {
                "id": event_id,
                "conversation_id": conversation_id,
                "sequence": 7,
                "type": "run.completed",
            },
        )
        connection.execute(
            outbox.insert(),
            {
                "id": uuid.uuid4(),
                "event_id": event_id,
                "conversation_id": conversation_id,
                "type": "run.completed",
                "payload": "legacy event payload",
            },
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.event_rows == 1
    assert report.event_notification_rows == 0
    assert report.missing_event_notification_rows == 1
    assert report.missing_event_ids == (str(event_id),)
    assert report.status == "preview"
    assert "legacy event payload" not in report.to_json()
    engine.dispose()


def test_preview_accepts_a_current_phase6a_event_notice():
    engine, _runs, jobs, events, outbox = _phase5_engine()
    installation_id, transport_epoch = _enable_phase6a(engine, jobs, outbox)
    event_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    event_type = "run.completed"
    with engine.begin() as connection:
        connection.execute(
            events.insert(),
            {
                "id": event_id,
                "conversation_id": conversation_id,
                "sequence": 7,
                "type": event_type,
            },
        )
        connection.execute(
            outbox.insert(),
            {
                "id": uuid.uuid4(),
                "conversation_id": conversation_id,
                "event_id": event_id,
                "type": event_type,
                "payload": json.dumps(
                    {"event_id": str(event_id)},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "destination": "event",
                "schema_version": 1,
                "message_id": uuid.uuid5(installation_id, f"event:{event_id}"),
                "subject": (
                    f"openship.{installation_id.hex}.v1.event.{conversation_id.hex}"
                ),
                "transport_epoch": transport_epoch,
            },
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.event_rows == 1
    assert report.event_notification_rows == 1
    assert report.missing_event_notification_rows == 0
    assert report.event_identity_conflicts == 0
    assert report.orphan_event_references == 0
    assert report.status == "preview"
    engine.dispose()


def test_preview_blocks_mismatched_and_orphaned_event_notice_references():
    engine, _runs, jobs, events, outbox = _phase5_engine()
    installation_id, transport_epoch = _enable_phase6a(engine, jobs, outbox)
    event_id, conversation_id, orphan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            events.insert(),
            {
                "id": event_id,
                "conversation_id": conversation_id,
                "sequence": 1,
                "type": "run.failed",
            },
        )
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "conversation_id": conversation_id,
                    "event_id": event_id,
                    "type": "run.failed",
                    "payload": json.dumps({"event_id": str(event_id)}),
                    "destination": "event",
                    "schema_version": 1,
                    "message_id": uuid.uuid5(installation_id, f"event:{event_id}"),
                    "subject": "openship.wrong.v1.event.wrong",
                    "transport_epoch": transport_epoch,
                },
                {
                    "id": uuid.uuid4(),
                    "conversation_id": conversation_id,
                    "event_id": orphan_id,
                    "type": "run.failed",
                    "payload": json.dumps({"event_id": str(orphan_id)}),
                    "destination": "event",
                    "schema_version": 1,
                    "message_id": uuid.uuid4(),
                    "subject": "openship.wrong.v1.event.wrong",
                    "transport_epoch": transport_epoch,
                },
            ],
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.event_identity_conflicts == 1
    assert report.orphan_event_references == 1
    assert set(report.event_issue_ids) == {str(event_id), str(orphan_id)}
    assert report.status == "blocked"
    engine.dispose()


def test_preview_bounds_event_issue_ids_and_scans_event_batches():
    engine, _runs, jobs, events, outbox = _phase5_engine()
    _installation_id, transport_epoch = _enable_phase6a(engine, jobs, outbox)
    event_ids = [uuid.uuid4() for _ in range(501)]
    conversation_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            events.insert(),
            [
                {
                    "id": event_id,
                    "conversation_id": conversation_id,
                    "sequence": sequence,
                    "type": "run.completed",
                }
                for sequence, event_id in enumerate(event_ids)
            ],
        )
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "conversation_id": conversation_id,
                    "event_id": event_id,
                    "type": "run.completed",
                    "payload": "{}",
                    "destination": "event",
                    "schema_version": 1,
                    "message_id": uuid.uuid4(),
                    "subject": "openship.invalid.v1.event.invalid",
                    "transport_epoch": transport_epoch,
                }
                for event_id in event_ids
            ],
        )

    with Session(engine) as db:
        report = plan_cutover(db, cutover_id=uuid.uuid4())

    assert report.event_rows == 501
    assert report.event_notification_rows == 501
    assert report.missing_event_notification_rows == 0
    assert report.event_identity_conflicts == 501
    assert len(report.event_issue_ids) == 100
    assert report.event_issue_ids_truncated is True
    assert report.status == "blocked"
    engine.dispose()


def test_apply_resumes_cutover_idempotently_and_verify_waits_for_publication(
    monkeypatch,
):
    engine = _full_phase6a_engine()
    cutover_id = uuid.uuid4()
    conversation_id, project_id = uuid.uuid4(), uuid.uuid4()
    run_ids = sorted((uuid.uuid4(), uuid.uuid4()))
    event_id = uuid.uuid4()
    with Session(engine) as db:
        db.add_all(
            [
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    status="queued",
                    fence=0,
                )
                for run_id in run_ids
            ]
        )
        db.flush()
        db.add_all([RunJob(run_id=run_id, fence=0) for run_id in run_ids])
        db.add(
            Event(
                id=event_id,
                conversation_id=conversation_id,
                project_id=project_id,
                run_id=run_ids[0],
                sequence=1,
                type="run.completed",
                payload="{}",
            )
        )
        db.commit()

    with (
        Session(engine) as db,
        pytest.raises(ValueError, match="legacy writer shutdown must be attested"),
    ):
        apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=False,
        )

    original_enqueue_run = cutover.enqueue_run
    enqueued = 0

    def fail_after_one_batch(db, run_id, generation, *, epoch):
        nonlocal enqueued
        enqueued += 1
        if enqueued == 2:
            raise RuntimeError("simulated process interruption")
        return original_enqueue_run(db, run_id, generation, epoch=epoch)

    monkeypatch.setattr(cutover, "enqueue_run", fail_after_one_batch)
    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="simulated process interruption"),
    ):
        apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )

    monkeypatch.setattr(cutover, "enqueue_run", original_enqueue_run)
    with Session(engine) as db:
        progress = db.get(MessagingCutover, cutover_id)
        state = db.get(MessageTransportState, 1)
        assert progress.phase == "runs"
        assert progress.last_run_id == run_ids[0]
        assert progress.requested_by == "test-operator"
        assert progress.last_operator == "test-operator"
        assert state.active is True
        assert state.admission_open is False
        assert state.execution_open is False
        result = apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )
        assert result.status == "applied"
        assert result.events_backfilled == 1
        assert result.commands_created == 2

    with Session(engine) as db:
        repeated = apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )
        assert repeated.events_scanned == 1
        assert repeated.events_backfilled == 1
        assert repeated.runs_scanned == 2
        assert repeated.commands_created == 2

    with Session(engine) as db:
        assert (
            db.query(Outbox)
            .filter(Outbox.destination == "event", Outbox.event_id == event_id)
            .count()
            == 1
        )
        assert db.query(Outbox).filter(Outbox.destination == "command").count() == 2
        assert verify_cutover(db, cutover_id=cutover_id).status == "not_ready"
        with pytest.raises(RuntimeError, match="not verified"):
            open_execution_gate(db, cutover_id=cutover_id)
        db.query(Outbox).update({Outbox.processed_at: datetime.now(UTC)})
        db.commit()

    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="execution gate has not been"),
    ):
        open_admission_gate(db, cutover_id=cutover_id)

    with Session(engine) as db:
        verification = verify_cutover(db, cutover_id=cutover_id)
        assert verification.status == "verified"
        assert verification.ready is True
        gate_result = open_execution_gate(db, cutover_id=cutover_id)
        assert gate_result.status == "opened"

    with Session(engine) as db:
        gate_result = open_admission_gate(db, cutover_id=cutover_id)
        assert gate_result.status == "opened"
        state = db.get(MessageTransportState, 1)
        assert state.execution_open is True
        assert state.admission_open is True

    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="close admission before execution"):
            cutover.close_execution_gate(db, cutover_id=cutover_id)
        admission_result = cutover.close_admission_gate(db, cutover_id=cutover_id)
        execution_result = cutover.close_execution_gate(db, cutover_id=cutover_id)
        assert admission_result.status == "closed"
        assert execution_result.status == "closed"
        state = db.get(MessageTransportState, 1)
        progress = db.get(MessagingCutover, cutover_id)
        assert state.execution_open is False
        assert state.admission_open is False
        assert progress.admission_closed_at is not None
        assert progress.execution_closed_at is not None
        db.commit()
        assert (
            cutover.close_admission_gate(db, cutover_id=cutover_id).status
            == "already_closed"
        )
        assert (
            cutover.close_execution_gate(db, cutover_id=cutover_id).status
            == "already_closed"
        )

    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="refusing to reapply"),
    ):
        apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )
    engine.dispose()


def test_gate_cli_accepts_close_actions():
    cutover_id = uuid.uuid4()

    execution = cli._parser().parse_args(
        ["execution", "close", "--cutover-id", str(cutover_id)]
    )
    admission = cli._parser().parse_args(
        ["admission", "close", "--cutover-id", str(cutover_id)]
    )

    assert execution.gate_action == "close"
    assert execution.cutover_id == cutover_id
    assert admission.gate_action == "close"
    assert admission.cutover_id == cutover_id


def test_gate_close_cli_does_not_require_jetstream(monkeypatch, capsys):
    cutover_id = uuid.uuid4()

    class EmptySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli, "SessionLocal", EmptySession)
    monkeypatch.setattr(
        cli,
        "close_admission_gate",
        lambda db, *, cutover_id: cutover.CutoverGateReport(
            cutover_id, "admission", "closed"
        ),
    )
    monkeypatch.setattr(
        cli,
        "ensure_topology",
        lambda **_kwargs: pytest.fail("closing a gate must work during broker outage"),
    )

    exit_code = cli.main(["admission", "close", "--cutover-id", str(cutover_id)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "cutover_id": str(cutover_id),
        "gate": "admission",
        "read_only": False,
        "status": "closed",
    }


def _completed_cutover_for_rollback(*, event_count=1):
    engine = _full_phase6a_engine()
    cutover_id, conversation_id, project_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    run_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(
            Run(
                id=run_id,
                conversation_id=conversation_id,
                project_id=project_id,
                status="queued",
            )
        )
        db.flush()
        db.add(RunJob(run_id=run_id))
        db.add_all(
            [
                Event(
                    id=uuid.uuid4(),
                    conversation_id=conversation_id,
                    project_id=project_id,
                    run_id=run_id,
                    sequence=sequence,
                    type="run.progress",
                    payload="{}",
                )
                for sequence in range(1, event_count + 1)
            ]
        )
        db.commit()

    with Session(engine) as db:
        apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="cutover-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )
        state = db.get(MessageTransportState, 1)
        original_epoch = state.transport_epoch
        event_ids = [event_id for (event_id,) in db.query(Event.id).all()]
        command_id = (
            db.query(Outbox.message_id).filter(Outbox.destination == "command").scalar()
        )
        db.query(Outbox).update({Outbox.processed_at: datetime.now(UTC)})
        db.commit()
    return engine, cutover_id, run_id, event_ids, command_id, original_epoch


def test_rollback_rotates_epoch_and_forward_cutover_uses_new_generation():
    engine, cutover_id, run_id, event_ids, command_id, original_epoch = (
        _completed_cutover_for_rollback()
    )

    with Session(engine) as db:
        preview = plan_rollback(db, cutover_id=cutover_id)
        assert preview.ready is True
        assert preview.status == "preview"
        assert preview.run_disposition_counts == {"queued_existing_command": 1}

    with Session(engine) as db:
        result = apply_rollback(
            db,
            cutover_id=cutover_id,
            operator="rollback-operator",
            batch_size=1,
            processes_stopped=True,
            consumers_drained=True,
        )
        assert result.status == "rolled_back"
        assert result.read_only is False
        assert result.rollback_epoch != original_epoch
        rollback_epoch = result.rollback_epoch

    with Session(engine) as db:
        state = db.get(MessageTransportState, 1)
        progress = db.get(MessagingCutover, cutover_id)
        job = db.query(RunJob).filter(RunJob.run_id == run_id).one()
        old_command = db.query(Outbox).filter(Outbox.destination == "command").one()
        event_notice = (
            db.query(Outbox)
            .filter(Outbox.destination == "event", Outbox.event_id == event_ids[0])
            .one()
        )
        assert state.active is False
        assert state.admission_open is False
        assert state.execution_open is False
        assert state.transport_epoch == rollback_epoch
        assert progress.status == "rolled_back"
        assert progress.rollback_epoch == rollback_epoch
        assert job.dispatch_generation == 2
        assert old_command.message_id == command_id
        assert old_command.transport_epoch == original_epoch
        assert event_notice.transport_epoch == rollback_epoch
        assert event_notice.processed_at is None

    forward_cutover_id = uuid.uuid4()
    with Session(engine) as db:
        forward = apply_cutover(
            db,
            cutover_id=forward_cutover_id,
            operator="forward-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )
        assert forward.status == "applied"
        state = db.get(MessageTransportState, 1)
        assert state.transport_epoch == rollback_epoch
        assert state.cutover_id == forward_cutover_id
        new_command = (
            db.query(Outbox)
            .filter(Outbox.destination == "command", Outbox.run_id == run_id)
            .order_by(Outbox.dispatch_generation.desc())
            .first()
        )
        assert new_command.dispatch_generation == 2
        assert new_command.message_id != command_id
        assert new_command.transport_epoch == rollback_epoch
        stale_rollback = plan_rollback(db, cutover_id=cutover_id)
        assert stale_rollback.ready is False
        assert "rollback_no_longer_current" in stale_rollback.errors
    engine.dispose()


def test_rollback_preview_blocks_unpublished_command_messages():
    engine, cutover_id, _run_id, _event_ids, _command_id, _epoch = (
        _completed_cutover_for_rollback()
    )
    with Session(engine) as db:
        db.query(Outbox).filter(Outbox.destination == "command").update(
            {Outbox.processed_at: None}
        )
        db.commit()

    with Session(engine) as db:
        report = plan_rollback(db, cutover_id=cutover_id)

    assert report.ready is False
    assert "command_publications_pending" in report.errors
    engine.dispose()


def test_rollback_preview_blocks_dispatch_generation_exhaustion_before_mutation():
    engine, cutover_id, run_id, _event_ids, _command_id, original_epoch = (
        _completed_cutover_for_rollback()
    )
    with Session(engine) as db:
        db.query(RunJob).filter(RunJob.run_id == run_id).update(
            {RunJob.dispatch_generation: 2_147_483_647}
        )
        db.commit()

    with Session(engine) as db:
        report = plan_rollback(db, cutover_id=cutover_id)

    assert report.ready is False
    assert "dispatch_generation_exhausted" in report.errors
    assert report.generation_exhausted_runs == 1

    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="preview is blocked"),
    ):
        apply_rollback(
            db,
            cutover_id=cutover_id,
            operator="rollback-operator",
            batch_size=1,
            processes_stopped=True,
            consumers_drained=True,
        )

    with Session(engine) as db:
        state = db.get(MessageTransportState, 1)
        progress = db.get(MessagingCutover, cutover_id)
        assert state.transport_epoch == original_epoch
        assert state.active is True
        assert progress.status == "complete"
        assert progress.rollback_epoch is None
    engine.dispose()


def test_rollback_resume_reuses_epoch_and_event_progress(monkeypatch):
    engine, cutover_id, run_id, event_ids, _command_id, original_epoch = (
        _completed_cutover_for_rollback(event_count=2)
    )
    original_assert = cutover._assert_rollback_identity
    checked_batches = 0

    def interrupt_after_one_event_batch(state, progress, selected_cutover_id):
        nonlocal checked_batches
        checked_batches += 1
        if checked_batches == 2:
            raise RuntimeError("simulated rollback interruption")
        return original_assert(state, progress, selected_cutover_id)

    monkeypatch.setattr(
        cutover, "_assert_rollback_identity", interrupt_after_one_event_batch
    )
    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="simulated rollback interruption"),
    ):
        apply_rollback(
            db,
            cutover_id=cutover_id,
            operator="rollback-operator",
            batch_size=1,
            processes_stopped=True,
            consumers_drained=True,
        )

    monkeypatch.setattr(cutover, "_assert_rollback_identity", original_assert)
    with Session(engine) as db:
        progress = db.get(MessagingCutover, cutover_id)
        state = db.get(MessageTransportState, 1)
        assert progress.status == "rolling_back"
        assert progress.phase == "rollback_events"
        assert progress.rollback_events_scanned == 1
        assert progress.rollback_epoch != original_epoch
        assert state.transport_epoch == progress.rollback_epoch
        rollback_epoch = progress.rollback_epoch
        result = apply_rollback(
            db,
            cutover_id=cutover_id,
            operator="resume-operator",
            batch_size=1,
            processes_stopped=True,
            consumers_drained=True,
        )
        assert result.status == "rolled_back"
        assert result.events_scanned == 2
        assert result.events_requeued == 2
        assert result.generations_bumped == 1
        assert result.rollback_epoch == rollback_epoch

    with Session(engine) as db:
        notices = (
            db.query(Outbox)
            .filter(Outbox.destination == "event", Outbox.event_id.in_(event_ids))
            .all()
        )
        job = db.query(RunJob).filter(RunJob.run_id == run_id).one()
        assert len(notices) == 2
        assert all(notice.transport_epoch == rollback_epoch for notice in notices)
        assert all(notice.processed_at is None for notice in notices)
        assert job.dispatch_generation == 2
    engine.dispose()


def test_rollback_cli_requires_process_and_consumer_attestations():
    cutover_id = str(uuid.uuid4())
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "rollback",
                "apply",
                "--cutover-id",
                cutover_id,
                "--operator",
                "operator",
            ]
        )

    args = cli._parser().parse_args(
        [
            "rollback",
            "apply",
            "--cutover-id",
            cutover_id,
            "--operator",
            "operator",
            "--processes-stopped",
            "--consumers-drained",
        ]
    )

    assert args.rollback_action == "apply"
    assert args.cutover_id == uuid.UUID(cutover_id)
    assert args.processes_stopped is True
    assert args.consumers_drained is True


def test_relay_does_not_claim_messages_during_rollback():
    engine, cutover_id, _run_id, event_ids, _command_id, _old_epoch = (
        _completed_cutover_for_rollback()
    )
    rollback_epoch = uuid.uuid4()
    with Session(engine) as db:
        state = db.get(MessageTransportState, 1)
        state.transport_epoch = rollback_epoch
        state.active = False
        state.admission_open = False
        state.execution_open = False
        progress = db.get(MessagingCutover, cutover_id)
        progress.status = "rolling_back"
        progress.phase = "rollback_events"
        progress.rollback_epoch = rollback_epoch
        notice = (
            db.query(Outbox)
            .filter(Outbox.destination == "event", Outbox.event_id == event_ids[0])
            .one()
        )
        notice.transport_epoch = rollback_epoch
        notice.processed_at = None
        db.commit()

    records = claim_outbox_batch(
        lambda: Session(engine),
        now=datetime.now(UTC) + timedelta(seconds=1),
        limit=1,
    )

    assert records == []
    engine.dispose()


def test_apply_refuses_blocked_preview_before_closing_transport_gates():
    engine = _full_phase6a_engine()
    run_id, conversation_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add(
            Run(
                id=run_id,
                conversation_id=conversation_id,
                project_id=project_id,
                status="running",
            )
        )
        db.add(RunJob(run_id=run_id))
        db.commit()

    with (
        Session(engine) as db,
        pytest.raises(RuntimeError, match="preview is blocked"),
    ):
        apply_cutover(
            db,
            cutover_id=uuid.uuid4(),
            operator="test-operator",
            batch_size=1,
            legacy_writers_stopped=True,
        )

    with Session(engine) as db:
        state = db.get(MessageTransportState, 1)
        assert state.active is False
        assert state.admission_open is True
        assert state.execution_open is True
        assert db.query(MessagingCutover).count() == 0
    engine.dispose()
