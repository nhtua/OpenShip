"""PostgreSQL migration guards for the Phase 6A outbox schema."""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.postgres


@pytest.fixture
def legacy_schema(tmp_path):
    raw_url = os.environ.get("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is required for messaging migration tests")

    base_url = make_url(raw_url)
    admin = create_engine(base_url, pool_pre_ping=True)
    schema = f"messaging_{uuid.uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    query = dict(base_url.query)
    query["options"] = f"-csearch_path={schema}"
    schema_url = base_url.set(query=query).render_as_string(hide_password=False)
    api_root = Path(__file__).parents[2]
    ini = tmp_path / "messaging-alembic.ini"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(api_root / "alembic.ini")
    parser.set("alembic", "script_location", str(api_root / "alembic"))
    parser.set("alembic", "sqlalchemy.url", schema_url.replace("%", "%%"))
    with ini.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ini), *args],
            cwd=api_root,
            env={
                "PYTHONPATH": str(api_root / "src"),
                "ALEMBIC_CONFIG": str(ini),
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    try:
        upgraded = alembic("upgrade", "phase5b014")
        assert upgraded.returncode == 0, upgraded.stderr
        engine = create_engine(schema_url)
        yield schema, engine, alembic
    finally:
        if "engine" in locals():
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def test_upgrade_and_safe_pre_activation_downgrade(legacy_schema):
    _schema, engine, alembic = legacy_schema
    user_id, project_id, conversation_id, outbox_id = [uuid.uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'test-only')"
            ),
            {
                "id": user_id,
                "username": f"msg_{user_id.hex}",
                "email": f"{user_id.hex}@example.test",
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) "
                "VALUES (:id, :owner, 'migration test')"
            ),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title, next_event_sequence) "
                "VALUES (:id, :user, :project, 'migration test', 0)"
            ),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO outbox (id, conversation_id, type, payload) "
                "VALUES (:id, :conversation, 'legacy.event', '{}')"
            ),
            {"id": outbox_id, "conversation": conversation_id},
        )
    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT active FROM messaging_state WHERE id = 1")
        ).scalar_one() is False
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM pg_trigger "
                "WHERE tgrelid = 'outbox'::regclass "
                "AND tgname = 'trg_outbox_notify' AND NOT tgisinternal"
            )
        ).scalar_one() == 1
        migrated = connection.execute(
            text(
                "SELECT destination, schema_version, message_id, publish_attempts "
                "FROM outbox WHERE id = :id"
            ),
            {"id": outbox_id},
        ).mappings().one()
        assert migrated["destination"] == "legacy"
        assert migrated["schema_version"] == 1
        assert migrated["message_id"] is not None
        assert migrated["publish_attempts"] == 0
        columns = {
            row["column_name"]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'run_jobs'"
                )
            ).mappings()
        }
        assert "dispatch_generation" in columns

    downgraded = alembic("downgrade", "phase5b014")
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'messaging_state'"
            )
        ).scalar_one() == 0


def test_duplicate_legacy_jobs_block_upgrade_without_partial_schema(legacy_schema):
    schema, engine, alembic = legacy_schema
    user_id, project_id, conversation_id, run_id = [uuid.uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'test-only')"
            ),
            {"id": user_id, "username": f"msg_{user_id.hex}", "email": f"{user_id.hex}@example.test"},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'migration test')"),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title, next_event_sequence) "
                "VALUES (:id, :user, :project, 'migration test', 0)"
            ),
            {"id": conversation_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO runs (id, conversation_id, project_id, status, attempt, fence, kind, graph_version) "
                "VALUES (:id, :conversation, :project, 'queued', 0, 0, 'chat', 1)"
            ),
            {"id": run_id, "conversation": conversation_id, "project": project_id},
        )
        connection.execute(
            text("INSERT INTO run_jobs (id, run_id, fence) VALUES (:id, :run, 0), (:other, :run, 0)"),
            {"id": uuid.uuid4(), "other": uuid.uuid4(), "run": run_id},
        )

    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode != 0
    assert "duplicate run_jobs" in upgraded.stderr.lower()
    assert schema in str(engine.url)
    with engine.connect() as connection:
        tables = {
            row["table_name"]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                )
            ).mappings()
        }
        assert "messaging_state" not in tables
