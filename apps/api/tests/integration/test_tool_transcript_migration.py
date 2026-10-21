"""Additive migration for normalized Phase 4B transcripts and reservations."""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def _alembic_ini(tmp_path: Path, db_path: Path) -> Path:
    api_root = Path(__file__).parents[2]
    ini = tmp_path / "alembic.ini"
    ini.write_text(
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
    return ini


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


def _seed_run(engine):
    with engine.begin() as connection:
        ids = {
            "user": "10000000-0000-0000-0000-000000000001",
            "project": "20000000-0000-0000-0000-000000000001",
            "conversation": "30000000-0000-0000-0000-000000000001",
            "run": "40000000-0000-0000-0000-000000000001",
        }
        connection.execute(
            text("INSERT INTO users (id, username, email, password_hash) VALUES (:id, 'transcript-user', 'transcript@example.test', 'hash')"),
            {"id": ids["user"]},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'transcript')"),
            {"id": ids["project"], "owner": ids["user"]},
        )
        connection.execute(
            text("INSERT INTO conversations (id, user_id, project_id, title) VALUES (:id, :user, :project, 'legacy')"),
            {"id": ids["conversation"], "user": ids["user"], "project": ids["project"]},
        )
        connection.execute(
            text("INSERT INTO runs (id, conversation_id, project_id, status) VALUES (:id, :conversation, :project, 'completed')"),
            {"id": ids["run"], "conversation": ids["conversation"], "project": ids["project"]},
        )
    return ids["run"]


def test_upgrade_preserves_legacy_rows_with_unknown_transcript_and_reservations(tmp_path):
    db_path = tmp_path / "tool-transcript.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run(ini, "upgrade", "phase4a006").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    run_id = _seed_run(engine)

    upgraded = _run(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    columns = {col["name"] for col in inspect(engine).get_columns("provider_attempts")}
    assert {
        "model_turn_ordinal",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_total_tokens",
    } <= columns
    assert "submission_digest" in {
        col["name"] for col in inspect(engine).get_columns("runs")
    }
    assert "run_transcript_entries" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT kind, graph_version, submission_digest FROM runs WHERE id=:id"),
            {"id": run_id},
        ).one()
        assert row.kind == "chat"
        assert row.graph_version == 1
        assert row.submission_digest is None
        assert connection.execute(
            text("SELECT COUNT(*) FROM run_transcript_entries")
        ).scalar_one() == 0
    engine.dispose()


def test_downgrade_refuses_to_erase_a_tool_transcript(tmp_path):
    db_path = tmp_path / "tool-transcript-downgrade.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run(ini, "upgrade", "head").returncode == 0
    engine = create_engine(f"sqlite:///{db_path}")
    run_id = _seed_run(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO run_transcript_entries "
                 "(id, run_id, ordinal, model_turn_ordinal, message_json, logical_call_ids_json) "
                 "VALUES ('50000000-0000-0000-0000-000000000001', :run, 0, 0, '{\"role\":\"assistant\",\"content\":\"done\"}', '[]')"),
            {"run": run_id},
        )

    downgraded = _run(ini, "downgrade", "phase4a006")
    assert downgraded.returncode != 0
    assert "refusing to downgrade" in downgraded.stderr.lower()
    assert "transcript" in downgraded.stderr.lower()
    engine.dispose()
