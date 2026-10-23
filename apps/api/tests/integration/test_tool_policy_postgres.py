from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.integration.postgres import bounded_postgres_url

_LANE_DATABASE = "phase5b_tool_policy_lane"


def _alembic_ini(tmp_path: Path, database_url: str) -> Path:
    api_root = Path(__file__).parents[2]
    ini = tmp_path / "tool-policy-alembic.ini"
    ini.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {database_url.replace('%', '%%')}

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers =
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = WARN
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
""",
        encoding="utf-8",
    )
    return ini


def _run_alembic(ini: Path, *arguments: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *arguments],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _fresh_postgres_database(service_url: str) -> str:
    from sqlalchemy import text as sa_text

    parts = urlsplit(service_url)
    lane_url = parts._replace(path=f"/{_LANE_DATABASE}").geturl()
    engine = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(sa_text(f"DROP DATABASE IF EXISTS {_LANE_DATABASE}"))
            connection.execute(sa_text(f"CREATE DATABASE {_LANE_DATABASE}"))
    finally:
        engine.dispose()
    return lane_url


@pytest.fixture(scope="module")
def postgres_lane(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; PostgreSQL tool policy checks are opt-in")

    database_url = bounded_postgres_url(_fresh_postgres_database(service_url))
    engine = create_engine(database_url, poolclass=NullPool)
    ini = _alembic_ini(tmp_path_factory.mktemp("tool-policy-postgres"), database_url)
    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    yield database_url, ini, engine
    engine.dispose()


def test_migration_creates_a_seeded_shared_tool_policy(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'tool-policy-migration.db'}"
    ini = _alembic_ini(tmp_path, database_url)
    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(database_url, poolclass=NullPool)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "tool_policy_state" in tables
        assert "tool_policy_overrides" in tables
        assert "tool_policy_audit_events" in tables
        assert "tool_policy_revision" in {
            column["name"] for column in inspect(engine).get_columns("runs")
        }
        assert "tool_policy_revision" in {
            column["name"] for column in inspect(engine).get_columns("tool_operations")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT revision FROM tool_policy_state WHERE id = 1")
            ).scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_concurrent_policy_writes_use_one_revision_precondition(
    postgres_lane, monkeypatch
):
    from src.openship.auth.models import User
    from src.openship.capabilities.models import (
        ToolPolicyAuditEvent,
        ToolPolicyOverride,
    )
    from src.openship.capabilities.policy import ToolPolicyError, set_tool_policy
    from src.openship.config import settings

    _database_url, _ini, engine = postgres_lane
    actor_id = uuid.uuid4()
    actor = User(
        id=actor_id,
        username=f"policy-{actor_id.hex[:12]}",
        email=f"policy-{actor_id.hex[:12]}@example.test",
        password_hash="test-only",
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(actor)
        db.commit()
    monkeypatch.setattr(settings, "connector_operator_user_id", actor_id)

    gate = threading.Barrier(3)

    def update(capability_id: str):
        with factory() as db:
            gate.wait(timeout=10)
            try:
                snapshot = set_tool_policy(
                    db,
                    actor=actor,
                    capability_id=capability_id,
                    enabled=False,
                    expected_revision=1,
                )
                db.commit()
                return ("updated", snapshot.revision)
            except ToolPolicyError as error:
                db.rollback()
                return (error.code, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, "workspace_list_files")
        second = pool.submit(update, "workspace_read_file")
        gate.wait(timeout=10)
        outcomes = (first.result(timeout=20), second.result(timeout=20))

    assert sum(outcome[0] == "updated" for outcome in outcomes) == 1
    assert sum(outcome[0] == "tool_policy_revision_changed" for outcome in outcomes) == 1
    with factory() as db:
        assert db.query(ToolPolicyOverride).count() == 1
        assert db.query(ToolPolicyAuditEvent).count() == 1
        assert db.execute(
            text("SELECT revision FROM tool_policy_state WHERE id = 1")
        ).scalar_one() == 2
