"""Phase 5 resource scope, retention and migration checks."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.openship.database.models import Base
from src.openship.resources.service import list_resource_observations
from tests.integration.postgres import bounded_postgres_url


@pytest.fixture(params=[
    pytest.param("sqlite", id="sqlite"),
    pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
])
def database_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'phase5-resources.db'}"
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; Phase 5 PostgreSQL checks are opt-in")
    return bounded_postgres_url(service_url)


def _engine(url: str):
    engine = create_engine(url, poolclass=NullPool)
    if engine.dialect.name == "sqlite":
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    return engine


def _uuid_text(connection, value: uuid.UUID) -> str:
    return value.hex if connection.dialect.name == "sqlite" else str(value)


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


def _insert_operation(connection, *, operation_id, run_id, project_id, conversation_id, user_id):
    connection.execute(
        text(
            "INSERT INTO tool_operations "
            "(id, run_id, project_id, conversation_id, principal_id, logical_call_id, "
            "capability_id, capability_version, manifest_digest, arguments_json, "
            "arguments_digest, request_digest, configuration_revision, execution_bundle_digest) "
            "VALUES (:id, :run, :project, :conversation, :user, :logical, 'test', '1', "
            ":digest, '{}', :digest, :digest, 'r1', :digest)"
        ),
        {
            "id": _uuid_text(connection, operation_id),
            "run": _uuid_text(connection, run_id),
            "project": _uuid_text(connection, project_id),
            "conversation": _uuid_text(connection, conversation_id),
            "user": _uuid_text(connection, user_id),
            "logical": f"call-{operation_id.hex}",
            "digest": "a" * 64,
        },
    )


def _insert_binding(
    connection,
    *,
    binding_id,
    project_id,
    resource_id,
    environment_id,
    connector_id,
    user_id,
    bucket_name,
):
    connection.execute(
        text(
            "INSERT INTO resource_bindings "
            "(id, project_id, resource_object_id, environment_id, connector_id, "
            "connector_revision, account_id, region, bucket_name, bucket_arn, expected_owner, "
            "created_by_user_id) VALUES (:id, :project, :resource, :environment, :connector, "
            "1, '123456789012', 'us-west-2', :bucket, :arn, '123456789012', :user)"
        ),
        {
            "id": _uuid_text(connection, binding_id),
            "project": _uuid_text(connection, project_id),
            "resource": _uuid_text(connection, resource_id),
            "environment": _uuid_text(connection, environment_id),
            "connector": _uuid_text(connection, connector_id),
            "bucket": bucket_name,
            "arn": f"arn:aws:s3:::{bucket_name}",
            "user": _uuid_text(connection, user_id),
        },
    )


def test_resource_migration_enforces_scope_immutability_and_retention(database_url, tmp_path):
    ini = _alembic_ini(tmp_path, database_url)
    engine = _engine(database_url)
    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    resource_tables = {
        "environments",
        "resource_objects",
        "resource_bindings",
        "resource_observations",
        "connector_blockers",
    }
    assert resource_tables <= set(inspect(engine).get_table_names())
    for table_name in resource_tables:
        migrated_columns = {
            column["name"] for column in inspect(engine).get_columns(table_name)
        }
        assert migrated_columns == set(Base.metadata.tables[table_name].columns.keys()), table_name

    user_id, project_id, other_project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conversation_id, other_conversation_id = uuid.uuid4(), uuid.uuid4()
    run_id, other_run_id = uuid.uuid4(), uuid.uuid4()
    operation_id, other_operation_id = uuid.uuid4(), uuid.uuid4()
    connector_id, credential_id, revision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_connector_id, other_credential_id, other_revision_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )
    environment_id, other_environment_id = uuid.uuid4(), uuid.uuid4()
    resource_id, second_resource_id, other_resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    binding_id = uuid.uuid4()
    artifact_id, observation_id, blocker_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'test-hash')"
            ),
            {
                "id": _uuid_text(connection, user_id),
                "username": f"phase5-resource-{user_id.hex}",
                "email": f"phase5-resource-{user_id.hex}@example.test",
            },
        )
        for project in (project_id, other_project_id):
            connection.execute(
                text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, :name)"),
                {
                    "id": _uuid_text(connection, project),
                    "owner": _uuid_text(connection, user_id),
                    "name": f"p-{project.hex}",
                },
            )
        for conversation, project in (
            (conversation_id, project_id),
            (other_conversation_id, project_id),
        ):
            connection.execute(
                text(
                    "INSERT INTO conversations (id, user_id, project_id, title) "
                    "VALUES (:id, :user, :project, 'resource test')"
                ),
                {
                    "id": _uuid_text(connection, conversation),
                    "user": _uuid_text(connection, user_id),
                    "project": _uuid_text(connection, project),
                },
            )
        for run, conversation, project, status in (
            (run_id, conversation_id, project_id, "waiting_connector"),
            (other_run_id, other_conversation_id, project_id, "completed"),
        ):
            connection.execute(
                text(
                    "INSERT INTO runs (id, conversation_id, project_id, status) "
                    "VALUES (:id, :conversation, :project, :status)"
                ),
                {
                    "id": _uuid_text(connection, run),
                    "conversation": _uuid_text(connection, conversation),
                    "project": _uuid_text(connection, project),
                    "status": status,
                },
            )
        _insert_operation(
            connection,
            operation_id=operation_id,
            run_id=run_id,
            project_id=project_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        _insert_operation(
            connection,
            operation_id=other_operation_id,
            run_id=other_run_id,
            project_id=project_id,
            conversation_id=other_conversation_id,
            user_id=user_id,
        )
        for connector, credential, revision, project in (
            (connector_id, credential_id, revision_id, project_id),
            (other_connector_id, other_credential_id, other_revision_id, other_project_id),
        ):
            connection.execute(
                text(
                    "INSERT INTO connectors "
                    "(id, kind, visibility, project_id, name, current_revision, enabled, "
                    "managed_by_user_id, created_by_user_id) "
                    "VALUES (:id, 'aws', 'project', :project, 'AWS', 1, TRUE, :user, :user)"
                ),
                {
                    "id": _uuid_text(connection, connector),
                    "project": _uuid_text(connection, project),
                    "user": _uuid_text(connection, user_id),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO connector_credentials "
                    "(id, connector_id, visibility, project_id, kind, schema_version, key_id, "
                    "nonce, ciphertext) VALUES (:id, :connector, 'project', :project, "
                    "'aws_bootstrap', 1, 'test', :nonce, :ciphertext)"
                ),
                {
                    "id": _uuid_text(connection, credential),
                    "connector": _uuid_text(connection, connector),
                    "project": _uuid_text(connection, project),
                    "nonce": b"n" * 12,
                    "ciphertext": b"c" * 16,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO connector_revisions "
                    "(id, connector_id, revision, schema_version, visibility, project_id, "
                    "configuration_json, configuration_digest, credential_version_id, "
                    "created_by_user_id) VALUES (:id, :connector, 1, 1, 'project', :project, "
                    "'{}', :digest, :credential, :user)"
                ),
                {
                    "id": _uuid_text(connection, revision),
                    "connector": _uuid_text(connection, connector),
                    "project": _uuid_text(connection, project),
                    "digest": "b" * 64,
                    "credential": _uuid_text(connection, credential),
                    "user": _uuid_text(connection, user_id),
                },
            )
        for environment, project in (
            (environment_id, project_id),
            (other_environment_id, other_project_id),
        ):
            connection.execute(
                text(
                    "INSERT INTO environments (id, project_id, name, display_name, created_by_user_id) "
                    "VALUES (:id, :project, 'production', 'Production', :user)"
                ),
                {
                    "id": _uuid_text(connection, environment),
                    "project": _uuid_text(connection, project),
                    "user": _uuid_text(connection, user_id),
                },
            )
        for resource, project, suffix in (
            (resource_id, project_id, "a"),
            (second_resource_id, project_id, "b"),
            (other_resource_id, other_project_id, "other"),
        ):
            connection.execute(
                text(
                    "INSERT INTO resource_objects "
                    "(id, project_id, kind, uri, display_name, created_by_user_id) "
                    "VALUES (:id, :project, 'aws_s3_bucket', :uri, 'Bucket', :user)"
                ),
                {
                    "id": _uuid_text(connection, resource),
                    "project": _uuid_text(connection, project),
                    "uri": f"openship://{project}/resource/{suffix}",
                    "user": _uuid_text(connection, user_id),
                },
            )
        _insert_binding(
            connection,
            binding_id=binding_id,
            project_id=project_id,
            resource_id=resource_id,
            environment_id=environment_id,
            connector_id=connector_id,
            user_id=user_id,
            bucket_name="phase5-resource-test-a",
        )

        # The same external bucket cannot be mapped twice within an environment.
        with pytest.raises(DBAPIError), connection.begin_nested():
            _insert_binding(
                connection,
                binding_id=uuid.uuid4(),
                project_id=project_id,
                resource_id=second_resource_id,
                environment_id=environment_id,
                connector_id=connector_id,
                user_id=user_id,
                bucket_name="phase5-resource-test-a",
            )

        # A binding cannot combine a project's resource with another project's connector.
        with pytest.raises(DBAPIError), connection.begin_nested():
            _insert_binding(
                connection,
                binding_id=uuid.uuid4(),
                project_id=other_project_id,
                resource_id=other_resource_id,
                environment_id=other_environment_id,
                connector_id=connector_id,
                user_id=user_id,
                bucket_name="phase5-resource-test-other",
            )

        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                text("UPDATE resource_bindings SET region='eu-west-1' WHERE id=:id"),
                    {"id": _uuid_text(connection, binding_id)},
            )
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                text("DELETE FROM resource_bindings WHERE id=:id"),
                    {"id": _uuid_text(connection, binding_id)},
            )

        connection.execute(
            text(
                "INSERT INTO artifacts "
                "(id, project_id, conversation_id, kind, media_type, display_name, size_bytes, "
                "sha256, relative_storage_key, source) "
                "VALUES (:id, :project, :conversation, 'cloud_observation', 'application/json', "
                "'observation.json', 0, :digest, :storage_key, 'aws_s3_bucket_storage')"
            ),
            {
                "id": _uuid_text(connection, artifact_id),
                "project": _uuid_text(connection, project_id),
                "conversation": _uuid_text(connection, conversation_id),
                "digest": "c" * 64,
                "storage_key": f"test/{artifact_id.hex}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO resource_observations "
                "(id, project_id, conversation_id, run_id, operation_id, resource_object_id, "
                "resource_binding_id, binding_revision, source, retrieved_at, coverage_json, "
                "outcome, evidence_artifact_id, evidence_sha256) "
                "VALUES (:id, :project, :conversation, :run, :operation, :resource, :binding, "
                "1, 'aws_s3_bucket_storage', CURRENT_TIMESTAMP, '{}', 'measured', :artifact, :digest)"
            ),
            {
                "id": _uuid_text(connection, observation_id),
                "project": _uuid_text(connection, project_id),
                "conversation": _uuid_text(connection, conversation_id),
                "run": _uuid_text(connection, run_id),
                "operation": _uuid_text(connection, operation_id),
                "resource": _uuid_text(connection, resource_id),
                "binding": _uuid_text(connection, binding_id),
                "artifact": _uuid_text(connection, artifact_id),
                "digest": "c" * 64,
            },
        )
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                text("UPDATE resource_observations SET outcome='failed' WHERE id=:id"),
                    {"id": _uuid_text(connection, observation_id)},
            )

        # Evidence references remain bound to the original conversation.
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO resource_observations "
                    "(id, project_id, conversation_id, run_id, operation_id, resource_object_id, "
                    "resource_binding_id, binding_revision, source, retrieved_at, coverage_json, "
                    "outcome, evidence_artifact_id, evidence_sha256) VALUES (:id, :project, "
                    ":conversation, :run, :operation, :resource, :binding, 1, 'test', "
                    "CURRENT_TIMESTAMP, '{}', 'measured', :artifact, :digest)"
                ),
                {
                    "id": _uuid_text(connection, uuid.uuid4()),
                    "project": _uuid_text(connection, project_id),
                    "conversation": _uuid_text(connection, other_conversation_id),
                    "run": _uuid_text(connection, other_run_id),
                    "operation": _uuid_text(connection, other_operation_id),
                    "resource": _uuid_text(connection, resource_id),
                    "binding": _uuid_text(connection, binding_id),
                    "artifact": _uuid_text(connection, artifact_id),
                    "digest": "c" * 64,
                },
            )

        connection.execute(
            text(
                "INSERT INTO connector_blockers "
                "(id, project_id, conversation_id, run_id, connector_id, connector_kind, "
                "connector_visibility, connector_project_id, connector_revision, "
                "credential_version_id, configuration_digest, request_id, reason, expires_at) "
                "VALUES (:id, :project, :conversation, :run, :connector, 'aws', 'project', "
                ":project, 1, :credential, :digest, :request, 'connector_required', CURRENT_TIMESTAMP)"
            ),
            {
                "id": _uuid_text(connection, blocker_id),
                "project": _uuid_text(connection, project_id),
                "conversation": _uuid_text(connection, conversation_id),
                "run": _uuid_text(connection, run_id),
                "connector": _uuid_text(connection, connector_id),
                "credential": _uuid_text(connection, credential_id),
                "digest": "d" * 64,
                "request": _uuid_text(connection, uuid.uuid4()),
            },
        )

    with Session(engine) as db:
        observations, next_cursor = list_resource_observations(
            db,
            owner_id=user_id,
            project_id=project_id,
            resource_id=resource_id,
        )
        assert next_cursor is None
        assert len(observations) == 1
        assert observations[0].evidence is not None
        assert observations[0].evidence.id == artifact_id
        assert observations[0].evidence_status == "staging"

    refused = _run_alembic(ini, "downgrade", "phase4c008")
    assert refused.returncode != 0
    assert "refusing to downgrade" in refused.stderr.lower()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM runs WHERE id=:id"), {"id": _uuid_text(connection, run_id)}
        ).scalar_one() == "waiting_connector"
        assert connection.execute(
            text("SELECT COUNT(*) FROM resource_observations WHERE id=:id"),
            {"id": _uuid_text(connection, observation_id)},
        ).scalar_one() == 1
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        # PostgreSQL rolls back the refused downgrade to the head. SQLite keeps
        # the last successfully applied revision before the refusal.
        expected_head = (
            "phase6a017" if database_url.startswith("postgresql") else "phase5a010"
        )
        assert current == expected_head
    engine.dispose()


def test_purged_connector_credential_prevents_irreversible_downgrade(database_url, tmp_path):
    ini = _alembic_ini(tmp_path, database_url)
    engine = _engine(database_url)
    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    user_id, project_id, connector_id, credential_id = (uuid.uuid4() for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'test-hash')"
            ),
            {
                "id": _uuid_text(connection, user_id),
                "username": f"phase5-purge-{user_id.hex}",
                "email": f"phase5-purge-{user_id.hex}@example.test",
            },
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, 'purge')"),
            {"id": _uuid_text(connection, project_id), "owner": _uuid_text(connection, user_id)},
        )
        connection.execute(
            text(
                "INSERT INTO connectors "
                "(id, kind, visibility, project_id, name, managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'aws', 'project', :project, 'AWS', :user, :user)"
            ),
            {
                "id": _uuid_text(connection, connector_id),
                "project": _uuid_text(connection, project_id),
                "user": _uuid_text(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO connector_credentials "
                "(id, connector_id, visibility, project_id, kind, schema_version, key_id, "
                "nonce, ciphertext, state, revoked_at, purged_at) "
                "VALUES (:id, :connector, 'project', :project, 'aws_bootstrap', 1, 'purged', "
                ":nonce, :ciphertext, 'revoked', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": _uuid_text(connection, credential_id),
                "connector": _uuid_text(connection, connector_id),
                "project": _uuid_text(connection, project_id),
                "nonce": b"\x00" * 12,
                "ciphertext": b"\x00" * 16,
            },
        )

    refused = _run_alembic(ini, "downgrade", "phase5a011")
    assert refused.returncode != 0
    assert "purged credential bytes cannot be restored" in refused.stderr.lower()
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        # PostgreSQL rolls back to the head; SQLite retains the last successful
        # revision before the secret-purge downgrade refusal.
        expected_head = (
            "phase6a017" if database_url.startswith("postgresql") else "phase5a012"
        )
        assert current == expected_head
    engine.dispose()
