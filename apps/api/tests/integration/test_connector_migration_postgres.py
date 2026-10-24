"""Phase 5A.1 connector schema upgrade, scope constraints and data retention."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from src.openship.database.models import Base
from tests.integration.postgres import bounded_postgres_url


@pytest.fixture(params=[
    pytest.param("sqlite", id="sqlite"),
    pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
])
def database_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'phase5-connectors.db'}"
        return
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; connector migration PostgreSQL checks are opt-in")

    parsed_url = make_url(bounded_postgres_url(service_url))
    schema = f"phase5_connector_migration_{uuid.uuid4().hex}"
    admin_engine = create_engine(parsed_url, poolclass=NullPool)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except SQLAlchemyError:
        admin_engine.dispose()
        pytest.skip("configured PostgreSQL service is unavailable")

    options = parsed_url.query.get("options", "")
    scoped_url = parsed_url.update_query_dict(
        {"options": f"{options} -csearch_path={schema}".strip()}
    ).render_as_string(hide_password=False)
    try:
        yield scoped_url
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _engine(url: str):
    return create_engine(url, poolclass=NullPool)


def _alembic_ini(tmp_path: Path, database_url: str) -> Path:
    api_root = Path(__file__).parents[2]
    ini = tmp_path / "alembic.ini"
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
""",
        encoding="utf-8",
    )
    return ini


def _run_alembic(ini: Path, *arguments: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *arguments],
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


def test_phase5_connector_migration_preserves_history_and_refuses_data_loss(
    database_url, tmp_path
):
    ini = _alembic_ini(tmp_path, database_url)
    engine = _engine(database_url)
    head = _run_alembic(ini, "upgrade", "head")
    assert head.returncode == 0, head.stderr
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE connector_availability, connector_audit_events, "
                    "connector_request_ledger, project_connector_defaults, "
                    "connector_revisions, connector_credentials, connectors CASCADE"
                )
            )
    baseline = _run_alembic(ini, "downgrade", "phase4c008")
    assert baseline.returncode == 0, baseline.stderr
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    other_connector_id = uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'hash')"
            ),
            {
                "id": str(user_id),
                "username": f"phase5-{user_id.hex}",
                "email": f"phase5-{user_id.hex}@example.test",
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) "
                "VALUES (:id, :owner, 'phase5-project')"
            ),
            {"id": str(project_id), "owner": str(user_id)},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :user, :project, 'legacy conversation')"
            ),
            {"id": str(conversation_id), "user": str(user_id), "project": str(project_id)},
        )
        connection.execute(
            text(
                "INSERT INTO runs (id, conversation_id, project_id, status) "
                "VALUES (:id, :conversation, :project, 'completed')"
            ),
            {"id": str(run_id), "conversation": str(conversation_id), "project": str(project_id)},
        )

    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    connector_tables = {
        "connectors",
        "connector_revisions",
        "connector_credentials",
        "project_connector_defaults",
        "connector_request_ledger",
        "connector_audit_events",
        "connector_availability",
    }
    assert connector_tables <= set(inspect(engine).get_table_names())
    for table_name in connector_tables:
        migrated_columns = {
            column["name"] for column in inspect(engine).get_columns(table_name)
        }
        model_columns = set(Base.metadata.tables[table_name].columns.keys())
        assert migrated_columns == model_columns, table_name

    with engine.connect() as connection:
        project = connection.execute(
            text("SELECT name FROM projects WHERE id=:id"), {"id": str(project_id)}
        ).scalar_one()
        historical_run = connection.execute(
            text("SELECT status, execution_configuration FROM runs WHERE id=:id"),
            {"id": str(run_id)},
        ).one()
    assert project == "phase5-project"
    assert historical_run.status == "completed"
    assert historical_run.execution_configuration is None

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO connectors "
                "(id, kind, visibility, project_id, name, current_revision, enabled, "
                "managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'aws', 'global', NULL, 'bad-aws', 1, TRUE, :actor, :actor)"
            ),
            {"id": str(uuid.uuid4()), "actor": str(user_id)},
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO connectors "
                "(id, kind, visibility, project_id, name, current_revision, enabled, "
                "managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'brave', 'project', :project, 'project-brave', 1, TRUE, :actor, :actor)"
            ),
            {
                "id": str(connector_id),
                "project": str(project_id),
                "actor": str(user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO connectors "
                "(id, kind, visibility, project_id, name, current_revision, enabled, "
                "managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'tavily', 'project', :project, 'project-tavily', 1, TRUE, :actor, :actor)"
            ),
            {
                "id": str(other_connector_id),
                "project": str(project_id),
                "actor": str(user_id),
            },
        )
        credential_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO connector_credentials "
                "(id, connector_id, visibility, project_id, kind, schema_version, key_id, nonce, ciphertext) "
                "VALUES (:id, :connector, 'project', :project, 'brave_api_key', 1, 'synthetic', :nonce, :ciphertext)"
            ),
            {
                "id": str(credential_id),
                "connector": str(connector_id),
                "project": str(project_id),
                "nonce": b"n" * 12,
                "ciphertext": b"c" * 16,
            },
        )

    revision_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO connector_revisions "
                "(id, connector_id, revision, schema_version, visibility, project_id, configuration_json, "
                "configuration_digest, credential_version_id, created_by_user_id) "
                "VALUES (:id, :connector, 1, 1, 'project', :project, '{}', :digest, :credential, :actor)"
            ),
            {
                "id": str(revision_id),
                "connector": str(connector_id),
                "project": str(project_id),
                "digest": "a" * 64,
                "credential": str(credential_id),
                "actor": str(user_id),
            },
        )

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO connector_revisions "
                "(id, connector_id, revision, schema_version, visibility, project_id, configuration_json, "
                "configuration_digest, credential_version_id, created_by_user_id) "
                "VALUES (:id, :connector, 1, 1, 'project', :project, '{}', :digest, :credential, :actor)"
            ),
            {
                "id": str(uuid.uuid4()),
                "connector": str(other_connector_id),
                "project": str(project_id),
                "digest": "b" * 64,
                "credential": str(credential_id),
                "actor": str(user_id),
            },
        )

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE connector_revisions SET configuration_json='{} ' WHERE id=:id"),
            {"id": str(revision_id)},
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE connectors SET visibility='global', project_id=NULL WHERE id=:id"),
            {"id": str(connector_id)},
        )

    refused = _run_alembic(ini, "downgrade", "phase4c008")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM connector_revisions WHERE id=:id"), {"id": str(revision_id)}
        )
        connection.execute(
            text("DELETE FROM connector_credentials WHERE id=:id"),
            {"id": str(credential_id)},
        )
        connection.execute(
            text("DELETE FROM connectors WHERE id=:id"),
            {"id": str(other_connector_id)},
        )
        connection.execute(text("DELETE FROM connectors WHERE id=:id"), {"id": str(connector_id)})
        connection.execute(text("DELETE FROM runs WHERE id=:id"), {"id": str(run_id)})
        connection.execute(
            text("DELETE FROM conversations WHERE id=:id"),
            {"id": str(conversation_id)},
        )
        connection.execute(text("DELETE FROM projects WHERE id=:id"), {"id": str(project_id)})
        connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})
    downgraded = _run_alembic(ini, "downgrade", "phase4c008")
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded_again = _run_alembic(ini, "upgrade", "head")
    assert upgraded_again.returncode == 0, upgraded_again.stderr
    engine.dispose()


def test_connector_delete_clears_audit_reference_without_mutating_history(
    database_url, tmp_path
):
    """The FK unlink on connector deletion is the only permitted audit update."""
    ini = _alembic_ini(tmp_path, database_url)
    engine = _engine(database_url)
    if engine.dialect.name == "sqlite":
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    user_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'hash')"
            ),
            {
                "id": str(user_id),
                "username": f"audit-unlink-{user_id.hex}",
                "email": f"audit-unlink-{user_id.hex}@example.test",
            },
        )
        connection.execute(
            text(
                "INSERT INTO connectors "
                "(id, kind, visibility, project_id, name, current_revision, enabled, "
                "managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'brave', 'global', NULL, 'audit-unlink', 1, TRUE, :actor, :actor)"
            ),
            {"id": str(connector_id), "actor": str(user_id)},
        )
        connection.execute(
            text(
                "INSERT INTO connector_credentials "
                "(id, connector_id, visibility, project_id, kind, schema_version, key_id, nonce, ciphertext) "
                "VALUES (:id, :connector, 'global', NULL, 'brave_api_key', 1, 'synthetic', :nonce, :ciphertext)"
            ),
            {
                "id": str(credential_id),
                "connector": str(connector_id),
                "nonce": b"n" * 12,
                "ciphertext": b"c" * 16,
            },
        )
        connection.execute(
            text(
                "INSERT INTO connector_revisions "
                "(id, connector_id, revision, schema_version, visibility, project_id, configuration_json, "
                "configuration_digest, credential_version_id, created_by_user_id) "
                "VALUES (:id, :connector, 1, 1, 'global', NULL, '{}', :digest, :credential, :actor)"
            ),
            {
                "id": str(revision_id),
                "connector": str(connector_id),
                "digest": "a" * 64,
                "credential": str(credential_id),
                "actor": str(user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO connector_audit_events "
                "(id, visibility, project_id, connector_id, connector_revision, actor_user_id, action, outcome) "
                "VALUES (:id, 'global', NULL, :connector, 1, NULL, 'created', 'accepted')"
            ),
            {"id": str(audit_id), "connector": str(connector_id)},
        )

        connection.execute(
            text("DELETE FROM connector_revisions WHERE id=:id"),
            {"id": str(revision_id)},
        )
        connection.execute(
            text("DELETE FROM connector_credentials WHERE id=:id"),
            {"id": str(credential_id)},
        )
        connection.execute(
            text("DELETE FROM connectors WHERE id=:id"),
            {"id": str(connector_id)},
        )
        audit = connection.execute(
            text(
                "SELECT connector_id, action FROM connector_audit_events WHERE id=:id"
            ),
            {"id": str(audit_id)},
        ).one()
        assert audit.connector_id is None
        assert audit.action == "created"

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE connector_audit_events SET action='changed' WHERE id=:id"),
            {"id": str(audit_id)},
        )

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})
    engine.dispose()
