"""Real PostgreSQL migration coverage for task recovery decisions."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.task_recovery_migration_support import check_guards, seed_032

pytestmark = pytest.mark.postgres


@pytest.fixture
def recovery_migration_database(tmp_path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.fail(
            "TEST_POSTGRES_URL is required for the selected PostgreSQL migration lane",
            pytrace=False,
        )

    bounded = bounded_postgres_url(service_url)
    database = f"phase7a_recovery_{uuid.uuid4().hex}"
    lane_url = (
        make_url(bounded).set(database=database).render_as_string(hide_password=False)
    )
    admin = create_engine(bounded).execution_options(isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        engine = create_engine(lane_url)
        yield engine, _alembic_ini(tmp_path, lane_url)
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()


def test_recovery_migration_upgrade_and_lossy_downgrade_guard(
    recovery_migration_database,
):
    engine, ini = recovery_migration_database
    check_guards(engine, ini, _run_alembic)


def test_recovery_downgrade_waits_for_postgres_writer_before_checking(
    recovery_migration_database,
):
    engine, ini = recovery_migration_database
    assert _run_alembic(ini, "upgrade", "phase7a032").returncode == 0
    seed_032(engine)
    assert _run_alembic(ini, "upgrade", "phase7a034").returncode == 0
    api_root = Path(__file__).parents[2]
    with engine.connect() as writer, engine.connect() as observer:
        writer_pid = writer.execute(text("SELECT pg_backend_pid() ")).scalar_one()
        writer.execute(
            text("UPDATE task_records SET skipped_steps_json = :value"),
            {"value": '["inspect"]'},
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(ini),
                "downgrade",
                "phase7a033",
            ],
            cwd=api_root,
            env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                blocked = observer.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :writer = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"writer": writer_pid},
                ).scalar_one()
                if blocked:
                    break
                assert process.poll() is None, (
                    "downgrade exited before blocking on writer"
                )
                time.sleep(0.05)
            else:
                pytest.fail("downgrade did not block on the concurrent writer")
            writer.commit()
            _, stderr = process.communicate(timeout=30)
            assert process.returncode != 0
            assert "cannot downgrade: recovery data would be lost" in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT skipped_steps_json FROM task_records")
            ).scalar_one()
            == '["inspect"]'
        )
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "phase7a034"
        )
