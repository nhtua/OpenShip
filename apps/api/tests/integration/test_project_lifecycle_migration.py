"""The multi-project migration preserves rows and refuses lossy downgrade."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


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
