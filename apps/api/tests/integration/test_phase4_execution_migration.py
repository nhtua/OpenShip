"""Migration tests for the additive Phase 4 execution foundation."""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def _alembic_ini(tmp_path: Path, db_path: Path) -> Path:
    api_root = Path(__file__).parents[2]
    path = tmp_path / "alembic.ini"
    path.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = sqlite:///{db_path}

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    )
    return path


def _run(ini: Path, *args: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _seed_legacy_run(engine):
    with engine.begin() as connection:
        user_id = "10000000-0000-0000-0000-000000000001"
        project_id = "20000000-0000-0000-0000-000000000001"
        conversation_id = "30000000-0000-0000-0000-000000000001"
        run_id = "40000000-0000-0000-0000-000000000001"
        connection.execute(
            text("INSERT INTO users (id, username, email, password_hash) VALUES (:id, 'phase4-user', 'phase4@example.test', 'hash')"),
            {"id": user_id},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'phase4')"),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text("INSERT INTO conversations (id, user_id, project_id, title) VALUES (:id, :user, :project, 'legacy')"),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text("INSERT INTO runs (id, conversation_id, project_id, status) VALUES (:id, :conversation, :project, 'completed')"),
            {"id": run_id, "conversation": conversation_id, "project": project_id},
        )
    return run_id


def test_upgrade_preserves_historical_chat_defaults_and_adds_execution_tables(tmp_path):
    db_path = tmp_path / "phase4-migration.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run(ini, "upgrade", "hij012").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    run_id = _seed_legacy_run(engine)

    upgraded = _run(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT kind, graph_version, execution_configuration FROM runs WHERE id=:id"),
            {"id": run_id},
        ).one()
        assert row.kind == "chat"
        assert row.graph_version == 1
        assert row.execution_configuration is None
    tables = set(inspect(engine).get_table_names())
    assert {"tool_operations", "tool_attempts", "execution_slots", "tool_availability", "artifacts", "artifact_quotas"} <= tables
    engine.dispose()


def test_upgrade_refuses_legacy_duplicate_request_ids_with_repair_guidance(tmp_path):
    db_path = tmp_path / "phase4-duplicates.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run(ini, "upgrade", "hij012").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    _seed_legacy_run(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_runs_client_request_id"))
        connection.execute(
            text("INSERT INTO runs (id, conversation_id, project_id, status, client_request_id) "
                 "SELECT '40000000-0000-0000-0000-000000000002', conversation_id, project_id, 'completed', 'duplicate-key' FROM runs LIMIT 1")
        )
        connection.execute(
            text("UPDATE runs SET client_request_id='duplicate-key' WHERE id='40000000-0000-0000-0000-000000000001'")
        )

    upgraded = _run(ini, "upgrade", "head")
    assert upgraded.returncode != 0
    assert "duplicate" in upgraded.stderr.lower()
    assert "repair" in upgraded.stderr.lower()
    engine.dispose()


def test_downgrade_refuses_to_erase_phase4_execution_records(tmp_path):
    db_path = tmp_path / "phase4-downgrade.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run(ini, "upgrade", "head").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO tool_availability "
                 "(id, capability_id, manifest_digest, worker_id, available, reason, observed_at) "
                 "VALUES ('50000000-0000-0000-0000-000000000001', 'brave_web_search', "
                 "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'worker', 1, NULL, CURRENT_TIMESTAMP)")
        )

    downgraded = _run(ini, "downgrade", "hij012")
    assert downgraded.returncode != 0
    assert "refusing to downgrade" in downgraded.stderr.lower()
    engine.dispose()
