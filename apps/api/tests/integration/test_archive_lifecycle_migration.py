"""Archive lifecycle migration preserves existing conversation and resource scope."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text


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
""",
        encoding="utf-8",
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


def _seed_scope(engine):
    ids = {
        key: str(uuid.uuid4())
        for key in (
            "user",
            "project",
            "conversation",
            "message",
            "artifact",
            "quota",
            "workspace",
            "storage",
            "default",
        )
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'archive-migration', 'archive-migration@example.test', 'hash')"
            ),
            {"id": ids["user"]},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'Existing')"),
            {"id": ids["project"], "owner": ids["user"]},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :owner, :project, 'Existing conversation')"
            ),
            {
                "id": ids["conversation"],
                "owner": ids["user"],
                "project": ids["project"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES (:id, :conversation, 'user', 'retained message')"
            ),
            {"id": ids["message"], "conversation": ids["conversation"]},
        )
        connection.execute(
            text(
                "INSERT INTO artifacts "
                "(id, project_id, conversation_id, kind, media_type, display_name, "
                "size_bytes, sha256, relative_storage_key, status, mutability, source) "
                "VALUES (:id, :project, :conversation, 'upload', 'text/plain', 'state.txt', "
                "4, :digest, 'conversations/state.txt', 'ready', 'immutable', 'user_upload')"
            ),
            {
                "id": ids["artifact"],
                "project": ids["project"],
                "conversation": ids["conversation"],
                "digest": "a" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO artifact_quotas (id, scope, project_id, committed_bytes, committed_count) "
                "VALUES (:id, 'project', :project, 4, 1)"
            ),
            {"id": ids["quota"], "project": ids["project"]},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_workspaces "
                "(id, conversation_id, project_id, relative_root, storage_id, status) "
                "VALUES (:id, :conversation, :project, 'workspaces/existing', :storage, 'available')"
            ),
            {
                "id": ids["workspace"],
                "conversation": ids["conversation"],
                "project": ids["project"],
                "storage": ids["storage"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO project_connector_defaults "
                "(id, project_id, service, connector_id, connector_revision, "
                "settings_revision, updated_by_user_id) "
                "VALUES (:id, :project, 'brave', NULL, NULL, 2, :owner)"
            ),
            {"id": ids["default"], "project": ids["project"], "owner": ids["user"]},
        )
    return ids


def test_upgrade_preserves_existing_archive_scope_records(tmp_path):
    api_root = Path(__file__).parents[2]
    database_url = f"sqlite:///{tmp_path / 'archive-lifecycle.db'}"
    ini = _alembic_ini(api_root, tmp_path / "alembic.ini", database_url)
    engine = create_engine(database_url)
    baseline = _alembic(api_root, ini, "upgrade", "phase5a009e")
    assert baseline.returncode == 0, baseline.stderr
    ids = _seed_scope(engine)

    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    columns = {
        table: {column["name"] for column in inspect(engine).get_columns(table)}
        for table in ("projects", "conversations")
    }
    assert "archived_at" in columns["projects"]
    assert "archived_at" in columns["conversations"]
    with engine.connect() as connection:
        project = connection.execute(
            text("SELECT id, owner_user_id, name, archived_at FROM projects WHERE id=:id"),
            {"id": ids["project"]},
        ).one()
        conversation = connection.execute(
            text("SELECT id, project_id, user_id, title, archived_at FROM conversations WHERE id=:id"),
            {"id": ids["conversation"]},
        ).one()
        message = connection.execute(
            text("SELECT id, conversation_id, content FROM messages WHERE id=:id"),
            {"id": ids["message"]},
        ).one()
        artifact = connection.execute(
            text("SELECT id, project_id, conversation_id, sha256, relative_storage_key FROM artifacts WHERE id=:id"),
            {"id": ids["artifact"]},
        ).one()
        quota = connection.execute(
            text("SELECT id, project_id, committed_bytes, committed_count FROM artifact_quotas WHERE id=:id"),
            {"id": ids["quota"]},
        ).one()
        workspace = connection.execute(
            text("SELECT id, project_id, conversation_id, relative_root, storage_id FROM conversation_workspaces WHERE id=:id"),
            {"id": ids["workspace"]},
        ).one()
        default = connection.execute(
            text("SELECT id, project_id, service, connector_id, settings_revision FROM project_connector_defaults WHERE id=:id"),
            {"id": ids["default"]},
        ).one()

    assert project.id == ids["project"] and project.owner_user_id == ids["user"]
    assert project.name == "Existing" and project.archived_at is None
    assert conversation.id == ids["conversation"] and conversation.project_id == ids["project"]
    assert conversation.user_id == ids["user"] and conversation.title == "Existing conversation"
    assert conversation.archived_at is None
    assert message.id == ids["message"] and message.conversation_id == ids["conversation"]
    assert message.content == "retained message"
    assert artifact.id == ids["artifact"] and artifact.project_id == ids["project"]
    assert artifact.conversation_id == ids["conversation"]
    assert artifact.sha256 == "a" * 64 and artifact.relative_storage_key == "conversations/state.txt"
    assert quota.id == ids["quota"] and quota.project_id == ids["project"]
    assert quota.committed_bytes == 4 and quota.committed_count == 1
    assert workspace.id == ids["workspace"] and workspace.project_id == ids["project"]
    assert workspace.conversation_id == ids["conversation"]
    assert workspace.relative_root == "workspaces/existing" and workspace.storage_id == ids["storage"]
    assert default.id == ids["default"] and default.project_id == ids["project"]
    assert default.service == "brave" and default.connector_id is None
    assert default.settings_revision == 2
    engine.dispose()


@pytest.mark.parametrize("archive_table", ["projects", "conversations"])
def test_downgrade_refuses_to_lose_archive_state(tmp_path, archive_table):
    api_root = Path(__file__).parents[2]
    database_url = f"sqlite:///{tmp_path / f'archive-downgrade-{archive_table}.db'}"
    ini = _alembic_ini(api_root, tmp_path / f"{archive_table}-alembic.ini", database_url)
    engine = create_engine(database_url)
    upgraded = _alembic(api_root, ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    ids = _seed_scope(engine)
    with engine.begin() as connection:
        if archive_table == "projects":
            record_id = ids["project"]
        else:
            record_id = ids["conversation"]
        connection.execute(
            text(f"UPDATE {archive_table} SET archived_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": record_id},
        )

    refused = _alembic(api_root, ini, "downgrade", "phase5a009e")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "phase5a009f"
    assert "archived_at" in {column["name"] for column in inspect(engine).get_columns(archive_table)}

    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {archive_table} SET archived_at=NULL WHERE id=:id"),
            {"id": record_id},
        )
    downgraded = _alembic(api_root, ini, "downgrade", "phase5a009e")
    assert downgraded.returncode == 0, downgraded.stderr
    assert "archived_at" not in {column["name"] for column in inspect(engine).get_columns(archive_table)}
    engine.dispose()
