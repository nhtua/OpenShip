"""Real PostgreSQL migration coverage for task recovery decisions."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.integration.postgres import bounded_postgres_url
from tests.integration.test_phase7a_task_state_postgres import (
    _alembic_ini,
    _run_alembic,
)
from tests.task_recovery_migration_support import check_guards

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
