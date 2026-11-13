"""Shared real-migration assertions for SQLite and disposable PostgreSQL."""

from sqlalchemy import inspect, text

from tests.test_task_state import _seed_legacy_run


def seed_032(engine):
    run_id = _seed_legacy_run(engine)
    ids = {
        "run": run_id,
        "task": "50000000-0000-0000-0000-000000000001",
        "decision": "60000000-0000-0000-0000-000000000001",
        "request": "70000000-0000-0000-0000-000000000001",
        "command": "80000000-0000-0000-0000-000000000001",
        "client": "90000000-0000-0000-0000-000000000001",
        "project": "20000000-0000-0000-0000-000000000001",
        "conversation": "30000000-0000-0000-0000-000000000001",
        "user": "10000000-0000-0000-0000-000000000001",
        "digest": "a" * 64,
        "payload": '{"legacy":true}',
    }
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO task_records
              (id, run_id, project_id, conversation_id, initiator_id, context_json)
            VALUES (:task, :run, :project, :conversation, :user, :payload)
        """),
            ids,
        )
        conn.execute(
            text("""
            INSERT INTO task_decisions
              (id, task_id, run_id, request_id, kind, plan_revision,
               expected_digest, expires_at, request_json)
            VALUES (:decision, :task, :run, :request, 'clarification', 1,
                    :digest, '2030-01-01 00:00:00', :payload)
        """),
            ids,
        )
        conn.execute(
            text("""
            INSERT INTO task_decision_commands
              (id, task_id, run_id, decision_id, client_request_id, request_id,
               action, plan_revision, expected_digest, result_json)
            VALUES (:command, :task, :run, :decision, :client, :request,
                    'reject', 1, :digest, :payload)
        """),
            ids,
        )


def snapshot(engine):
    with engine.connect() as conn:
        return {
            table: [
                dict(row)
                for row in conn.execute(text(f"SELECT * FROM {table}")).mappings()
            ]
            for table in ("task_records", "task_decisions", "task_decision_commands")
        }


def assert_revision(engine, revision):
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == revision
        )


def set_value(engine, table, column, value):
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE {table} SET {column} = :value"), {"value": value})


def assert_guard_refuses(engine, ini, run_alembic, target, message):
    before = snapshot(engine)
    result = run_alembic(ini, "downgrade", target)
    assert result.returncode != 0, "downgrade unexpectedly succeeded"
    assert message in result.stderr, (
        f"downgrade failed without the intended guard: {result.stderr}"
    )
    assert_revision(engine, "phase7a034" if target == "phase7a033" else "phase7a033")
    assert snapshot(engine) == before


def check_guards(engine, ini, run_alembic):
    """Exercise each independent blocker, and successful, data-preserving downgrades."""
    assert run_alembic(ini, "upgrade", "phase7a032").returncode == 0
    seed_032(engine)
    before_upgrade = snapshot(engine)
    result = run_alembic(ini, "upgrade", "phase7a034")
    assert result.returncode == 0, result.stderr
    upgraded = snapshot(engine)
    assert upgraded["task_records"][0].pop("skipped_steps_json") == "[]"
    assert upgraded["task_records"][0].pop("call_bindings_json") == "[]"
    assert upgraded == before_upgrade

    cases_034 = [
        ("task_records", "skipped_steps_json", '["inspect"]', "[]"),
        (
            "task_records",
            "call_bindings_json",
            '[{"logical_call_id":"model:1:call:0"}]',
            "[]",
        ),
        ("task_decisions", "kind", "recovery", "clarification"),
        *(
            ("task_decision_commands", "action", action, "reject")
            for action in ("abort", "repeat_step", "continue_next")
        ),
    ]
    for table, column, value, original in cases_034:
        set_value(engine, table, column, value)
        assert_guard_refuses(
            engine,
            ini,
            run_alembic,
            "phase7a033",
            "cannot downgrade: recovery data would be lost",
        )
        set_value(engine, table, column, original)

    result = run_alembic(ini, "downgrade", "phase7a033")
    assert result.returncode == 0, result.stderr
    assert_revision(engine, "phase7a033")
    assert snapshot(engine) == before_upgrade
    assert "skipped_steps_json" not in {
        column["name"] for column in inspect(engine).get_columns("task_records")
    }

    for table, column in (
        ("task_records", "current_revision"),
        ("task_decisions", "plan_revision"),
        ("task_decision_commands", "plan_revision"),
    ):
        set_value(engine, table, column, 0)
        assert_guard_refuses(
            engine,
            ini,
            run_alembic,
            "phase7a032",
            "cannot downgrade: revision-zero task data exists",
        )
        set_value(engine, table, column, 1)

    result = run_alembic(ini, "downgrade", "phase7a032")
    assert result.returncode == 0, result.stderr
    assert_revision(engine, "phase7a032")
    assert snapshot(engine) == before_upgrade
