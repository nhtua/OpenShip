"""The multi-project migration preserves rows and refuses lossy downgrade."""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _alembic_ini(api_root: Path, path: Path, database_url: str) -> Path:
    path.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {database_url}
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
handlers = console
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


def _alembic(api_root: Path, ini: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_migration_preserves_project_references_and_guards_downgrade(tmp_path):
    api_root = Path(__file__).parents[2]
    db_path = tmp_path / "project-lifecycle.db"
    database_url = f"sqlite:///{db_path}"
    ini = _alembic_ini(api_root, tmp_path / "alembic.ini", database_url)
    baseline = _alembic(api_root, ini, "upgrade", "phase5a009b")
    assert baseline.returncode == 0, baseline.stderr

    user_id = "10000000-0000-0000-0000-000000000001"
    project_id = "20000000-0000-0000-0000-000000000001"
    conversation_id = "30000000-0000-0000-0000-000000000001"
    quota_id = "40000000-0000-0000-0000-000000000001"
    default_id = "50000000-0000-0000-0000-000000000001"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'project-migration', 'project-migration@example.test', 'hash')"
            ),
            {"id": user_id},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'Existing')"),
            {"id": project_id, "owner": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :owner, :project, 'Existing conversation')"
            ),
            {"id": conversation_id, "owner": user_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO artifact_quotas (id, scope, project_id) "
                "VALUES (:id, 'project', :project)"
            ),
            {"id": quota_id, "project": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO project_connector_defaults "
                "(id, project_id, service, updated_by_user_id) "
                "VALUES (:id, :project, 'brave', :owner)"
            ),
            {"id": default_id, "project": project_id, "owner": user_id},
        )

    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT id FROM projects WHERE id=:id"), {"id": project_id}
        ).scalar_one() == project_id
        assert connection.execute(
            text("SELECT project_id FROM conversations WHERE id=:id"),
            {"id": conversation_id},
        ).scalar_one() == project_id
        assert connection.execute(
            text("SELECT project_id FROM artifact_quotas WHERE id=:id"),
            {"id": quota_id},
        ).scalar_one() == project_id
        assert connection.execute(
            text("SELECT project_id FROM project_connector_defaults WHERE id=:id"),
            {"id": default_id},
        ).scalar_one() == project_id
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'Second')"),
            {"id": "60000000-0000-0000-0000-000000000001", "owner": user_id},
        )

    refused = _alembic(api_root, ini, "downgrade", "phase5a009b")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()
    assert "multiple projects" in refused.stderr.lower()
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "phase5a009c"
        connection.execute(
            text("DELETE FROM projects WHERE id='60000000-0000-0000-0000-000000000001'")
        )

    downgraded = _alembic(api_root, ini, "downgrade", "phase5a009b")
    assert downgraded.returncode == 0, downgraded.stderr
    engine.dispose()


def test_search_default_migration_keeps_newest_row_and_limits_each_project(tmp_path):
    api_root = Path(__file__).parents[2]
    db_path = tmp_path / "project-search-default-migration.db"
    database_url = f"sqlite:///{db_path}"
    ini = _alembic_ini(api_root, tmp_path / "search-default-alembic.ini", database_url)
    baseline = _alembic(api_root, ini, "upgrade", "phase5a009c")
    assert baseline.returncode == 0, baseline.stderr

    user_id = "11000000-0000-0000-0000-000000000001"
    latest_project = "22000000-0000-0000-0000-000000000001"
    tie_project = "22000000-0000-0000-0000-000000000002"
    latest_brave = "55000000-0000-0000-0000-000000000001"
    latest_tavily = "55000000-0000-0000-0000-000000000002"
    tie_brave = "55000000-0000-0000-0000-000000000003"
    tie_tavily = "55000000-0000-0000-0000-000000000004"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        users = sa.table(
            "users",
            sa.column("id", sa.Uuid()),
            sa.column("username", sa.String()),
            sa.column("email", sa.String()),
            sa.column("password_hash", sa.String()),
        )
        projects = sa.table(
            "projects",
            sa.column("id", sa.Uuid()),
            sa.column("owner_user_id", sa.Uuid()),
            sa.column("name", sa.String()),
        )
        connection.execute(
            users.insert(),
            [{
                "id": uuid.UUID(user_id),
                "username": "search-default-migration",
                "email": "search-default-migration@example.test",
                "password_hash": "hash",
            }],
        )
        connection.execute(
            projects.insert(),
            [
                {"id": uuid.UUID(project_id), "owner_user_id": uuid.UUID(user_id), "name": name}
                for project_id, name in (
                    (latest_project, "Newest wins"),
                    (tie_project, "Brave wins ties"),
                )
            ],
        )
        defaults = [
            (latest_brave, latest_project, "brave", 8, datetime(2026, 10, 20, 10, tzinfo=UTC)),
            (latest_tavily, latest_project, "tavily", 3, datetime(2026, 10, 21, 10, tzinfo=UTC)),
            (tie_brave, tie_project, "brave", 4, datetime(2026, 10, 21, 10, tzinfo=UTC)),
            (tie_tavily, tie_project, "tavily", 4, datetime(2026, 10, 21, 10, tzinfo=UTC)),
        ]
        default_table = sa.table(
            "project_connector_defaults",
            sa.column("id", sa.Uuid()),
            sa.column("project_id", sa.Uuid()),
            sa.column("service", sa.String(16)),
            sa.column("connector_id", sa.Uuid()),
            sa.column("connector_revision", sa.Integer()),
            sa.column("settings_revision", sa.Integer()),
            sa.column("updated_by_user_id", sa.Uuid()),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        connection.execute(
            default_table.insert(),
            [
                {
                    "id": uuid.UUID(default_id),
                    "project_id": uuid.UUID(project_id),
                    "service": service,
                    "connector_id": None,
                    "connector_revision": None,
                    "settings_revision": revision,
                    "updated_by_user_id": uuid.UUID(user_id),
                    "updated_at": updated_at,
                }
                for default_id, project_id, service, revision, updated_at in defaults
            ],
        )

    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        kept = connection.execute(
            text(
                "SELECT id, service FROM project_connector_defaults "
                "ORDER BY project_id"
            )
        ).all()
        assert [(str(uuid.UUID(row.id)), row.service) for row in kept] == [
            (latest_tavily, "tavily"),
            (tie_brave, "brave"),
        ]

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project_connector_defaults "
                "(id, project_id, service, connector_id, connector_revision, "
                "settings_revision, updated_by_user_id) "
                "VALUES ('55000000000000000000000000000005', :project, "
                "'brave', NULL, NULL, 5, :owner)"
            ),
            {
                "project": uuid.UUID(latest_project).hex,
                "owner": uuid.UUID(user_id).hex,
            },
        )
    engine.dispose()
