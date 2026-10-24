"""Forward migration proof for binding-free AWS read observations."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import AuthorizationContext, CapabilityRef
from src.openship.database.models import Base
from src.openship.execution.models import ToolOperation
from src.openship.resources.service import record_aws_read_observation
from src.openship.tools.aws.contracts import AwsReadObservation
from tests.integration.postgres import bounded_postgres_url


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]
)
def database_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'aws-discovery-migration.db'}"
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; AWS discovery PostgreSQL proof is opt-in")
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


def _uuid(connection, value: uuid.UUID) -> str:
    return value.hex if connection.dialect.name == "sqlite" else str(value)


def _alembic_ini(tmp_path: Path, database_url: str) -> Path:
    api_root = Path(__file__).parents[2]
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        f"""[alembic]
script_location = {api_root / "alembic"}
sqlalchemy.url = {database_url.replace("%", "%%")}

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
datefmt = %H:%M:%S
""",
        encoding="utf-8",
    )
    return ini


def _alembic(ini: Path, *args: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_migration_preserves_legacy_observation_and_accepts_unbound_read(
    database_url, tmp_path
):
    ini = _alembic_ini(tmp_path, database_url)
    engine = _engine(database_url)
    before = _alembic(ini, "upgrade", "phase5b013")
    assert before.returncode == 0, before.stderr

    user_id, project_id, conversation_id, run_id, operation_id, read_operation_id = [
        uuid.uuid4() for _ in range(6)
    ]
    connector_id, credential_id, revision_id = [uuid.uuid4() for _ in range(3)]
    environment_id, resource_id, binding_id = [uuid.uuid4() for _ in range(3)]
    artifact_id, observation_id = uuid.uuid4(), uuid.uuid4()
    retrieved_at = datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC)
    digest = "a" * 64

    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, 'hash')"
            ),
            {
                "id": _uuid(connection, user_id),
                "username": f"aws-{user_id.hex}",
                "email": f"{user_id.hex}@example.test",
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :user, 'AWS read migration')"
            ),
            {"id": _uuid(connection, project_id), "user": _uuid(connection, user_id)},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id, project_id, title) "
                "VALUES (:id, :user, :project, 'AWS migration')"
            ),
            {
                "id": _uuid(connection, conversation_id),
                "user": _uuid(connection, user_id),
                "project": _uuid(connection, project_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO runs (id, conversation_id, project_id, status) VALUES (:id, :conversation, :project, 'completed')"
            ),
            {
                "id": _uuid(connection, run_id),
                "conversation": _uuid(connection, conversation_id),
                "project": _uuid(connection, project_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO tool_operations "
                "(id, run_id, project_id, conversation_id, principal_id, logical_call_id, "
                "capability_id, capability_version, manifest_digest, arguments_json, "
                "arguments_digest, request_digest, configuration_revision, execution_bundle_digest) "
                "VALUES (:id, :run, :project, :conversation, :user, :logical, 'aws_s3_bucket_storage', "
                "'1.0.0', :digest, '{}', :digest, :digest, 'r1', :digest)"
            ),
            {
                "id": _uuid(connection, operation_id),
                "run": _uuid(connection, run_id),
                "project": _uuid(connection, project_id),
                "conversation": _uuid(connection, conversation_id),
                "user": _uuid(connection, user_id),
                "logical": f"aws-{operation_id.hex}",
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tool_operations "
                "(id, run_id, project_id, conversation_id, principal_id, logical_call_id, "
                "capability_id, capability_version, manifest_digest, arguments_json, "
                "arguments_digest, request_digest, configuration_revision, execution_bundle_digest) "
                "VALUES (:id, :run, :project, :conversation, :user, :logical, 'aws_read', "
                "'1.0.0', :manifest, '{}', :digest, :digest, 'r1', :digest)"
            ),
            {
                "id": _uuid(connection, read_operation_id),
                "run": _uuid(connection, run_id),
                "project": _uuid(connection, project_id),
                "conversation": _uuid(connection, conversation_id),
                "user": _uuid(connection, user_id),
                "logical": f"aws-read-{read_operation_id.hex}",
                "digest": digest,
                "manifest": "b" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO connectors (id, kind, visibility, project_id, name, current_revision, enabled, managed_by_user_id, created_by_user_id) "
                "VALUES (:id, 'aws', 'project', :project, 'AWS', 1, TRUE, :user, :user)"
            ),
            {
                "id": _uuid(connection, connector_id),
                "project": _uuid(connection, project_id),
                "user": _uuid(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO connector_credentials (id, connector_id, visibility, project_id, kind, schema_version, key_id, nonce, ciphertext) "
                "VALUES (:id, :connector, 'project', :project, 'aws_bootstrap', 1, 'test', :nonce, :ciphertext)"
            ),
            {
                "id": _uuid(connection, credential_id),
                "connector": _uuid(connection, connector_id),
                "project": _uuid(connection, project_id),
                "nonce": b"n" * 12,
                "ciphertext": b"c" * 16,
            },
        )
        connection.execute(
            text(
                "INSERT INTO connector_revisions (id, connector_id, revision, schema_version, visibility, project_id, configuration_json, configuration_digest, credential_version_id, created_by_user_id) "
                "VALUES (:id, :connector, 1, 1, 'project', :project, '{}', :digest, :credential, :user)"
            ),
            {
                "id": _uuid(connection, revision_id),
                "connector": _uuid(connection, connector_id),
                "project": _uuid(connection, project_id),
                "digest": digest,
                "credential": _uuid(connection, credential_id),
                "user": _uuid(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO environments (id, project_id, name, display_name, created_by_user_id) VALUES (:id, :project, 'production', 'Production', :user)"
            ),
            {
                "id": _uuid(connection, environment_id),
                "project": _uuid(connection, project_id),
                "user": _uuid(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO resource_objects (id, project_id, kind, uri, display_name, created_by_user_id) VALUES (:id, :project, 'aws_s3_bucket', :uri, 'Legacy bucket', :user)"
            ),
            {
                "id": _uuid(connection, resource_id),
                "project": _uuid(connection, project_id),
                "uri": f"openship://{resource_id}",
                "user": _uuid(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO resource_bindings (id, project_id, resource_object_id, environment_id, connector_id, connector_revision, account_id, region, bucket_name, bucket_arn, expected_owner, created_by_user_id) "
                "VALUES (:id, :project, :resource, :environment, :connector, 1, '123456789012', 'us-east-1', 'legacy-bucket-123', 'arn:aws:s3:::legacy-bucket-123', '123456789012', :user)"
            ),
            {
                "id": _uuid(connection, binding_id),
                "project": _uuid(connection, project_id),
                "resource": _uuid(connection, resource_id),
                "environment": _uuid(connection, environment_id),
                "connector": _uuid(connection, connector_id),
                "user": _uuid(connection, user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO artifacts (id, project_id, conversation_id, kind, media_type, display_name, size_bytes, sha256, relative_storage_key, source) "
                "VALUES (:id, :project, :conversation, 'cloud_observation', 'application/json', 'legacy.json', 42, :digest, :key, 'aws_s3_bucket_storage')"
            ),
            {
                "id": _uuid(connection, artifact_id),
                "project": _uuid(connection, project_id),
                "conversation": _uuid(connection, conversation_id),
                "digest": digest,
                "key": f"test/{artifact_id.hex}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO resource_observations (id, project_id, conversation_id, run_id, operation_id, resource_object_id, resource_binding_id, binding_revision, source, retrieved_at, coverage_json, outcome, evidence_artifact_id, evidence_sha256) "
                "VALUES (:id, :project, :conversation, :run, :operation, :resource, :binding, 1, 'aws_s3_bucket_storage', :retrieved, :coverage, 'measured', :artifact, :digest)"
            ),
            {
                "id": _uuid(connection, observation_id),
                "project": _uuid(connection, project_id),
                "conversation": _uuid(connection, conversation_id),
                "run": _uuid(connection, run_id),
                "operation": _uuid(connection, operation_id),
                "resource": _uuid(connection, resource_id),
                "binding": _uuid(connection, binding_id),
                "retrieved": retrieved_at,
                "coverage": '{"kept":true}',
                "artifact": _uuid(connection, artifact_id),
                "digest": digest,
            },
        )

    migrated = _alembic(ini, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("resource_observations")
    }
    assert {
        "schema_version",
        "connector_id",
        "connector_revision",
        "account_id",
        "service",
        "operation",
        "region",
        "target_json",
        "catalog_digest",
    } <= columns
    assert columns == set(Base.metadata.tables["resource_observations"].columns.keys())

    with engine.begin() as connection:
        legacy = (
            connection.execute(
                text("SELECT * FROM resource_observations WHERE id=:id"),
                {"id": _uuid(connection, observation_id)},
            )
            .mappings()
            .one()
        )
        assert legacy["schema_version"] == 1
        assert str(legacy["resource_object_id"]).replace("-", "") == resource_id.hex
        assert str(legacy["resource_binding_id"]).replace("-", "") == binding_id.hex
        assert legacy["evidence_sha256"] == digest
        assert legacy["coverage_json"] == '{"kept":true}'
        stored_retrieved_at = legacy["retrieved_at"]
        if isinstance(stored_retrieved_at, datetime):
            assert stored_retrieved_at.replace(tzinfo=UTC) == retrieved_at
        else:
            assert str(stored_retrieved_at).startswith("2026-10-23 12:34:56")

        generic_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO resource_observations (id, project_id, conversation_id, run_id, operation_id, schema_version, connector_id, connector_revision, account_id, service, operation, region, target_json, catalog_digest, source, retrieved_at, coverage_json, outcome, evidence_artifact_id, evidence_sha256) "
                "VALUES (:id, :project, :conversation, :run, :operation_id, 2, :connector, 1, '123456789012', 's3', 'list_buckets', 'us-east-1', '{\"prefix\":\"demo\"}', :catalog, 'aws_read', :retrieved, '{}', 'measured', :artifact, :artifact_digest)"
            ),
            {
                "id": _uuid(connection, generic_id),
                "project": _uuid(connection, project_id),
                "conversation": _uuid(connection, conversation_id),
                "run": _uuid(connection, run_id),
                "operation_id": _uuid(connection, operation_id),
                "connector": _uuid(connection, connector_id),
                "catalog": "b" * 64,
                "retrieved": datetime.now(UTC),
                "artifact": _uuid(connection, artifact_id),
                "artifact_digest": digest,
            },
        )
        generic = connection.execute(
            text(
                "SELECT schema_version, resource_object_id, resource_binding_id, binding_revision FROM resource_observations WHERE id=:id"
            ),
            {"id": _uuid(connection, generic_id)},
        ).one()
        assert generic == (2, None, None, None)
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                text("UPDATE resource_observations SET outcome='failed' WHERE id=:id"),
                {"id": _uuid(connection, generic_id)},
            )

        # The publication helper stages a complete v2 record without inventing
        # any resource or environment identity and leaves commit ownership here.
        sessions = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = sessions()
        operation = session.query(ToolOperation).filter_by(id=read_operation_id).one()
        read_observation = AwsReadObservation(
            tool=CapabilityRef(
                id="aws_read", version="1.0.0", manifest_digest="b" * 64
            ),
            operation_id=read_operation_id,
            project_id=project_id,
            connector_id=connector_id,
            connector_revision=1,
            catalog_digest="b" * 64,
            account_id="123456789012",
            service="s3",
            operation="list_buckets",
            region="us-east-1",
            target={"prefix": "demo"},
            retrieved_at=datetime.now(UTC),
            request_count=2,
            data={"buckets": []},
            outcome="measured",
            coverage={"truncated": False},
        )
        staged = record_aws_read_observation(
            session,
            authorization=AuthorizationContext(
                principal_id=user_id,
                project_id=project_id,
                conversation_id=conversation_id,
                action="execute_tool",
                target_id=str(run_id),
            ),
            operation=operation,
            observation=read_observation,
            artifact=ArtifactRef(
                id=artifact_id,
                sha256=digest,
                size_bytes=42,
                media_type="application/json",
            ),
        )
        session.flush()
        assert staged.schema_version == 2
        assert staged.resource_object_id is None
        assert staged.resource_binding_id is None
        assert staged.binding_revision is None
        assert staged.target_json == '{"prefix":"demo"}'
        session.commit()
        session.close()

        generic_insert = text(
            "INSERT INTO resource_observations (id, project_id, conversation_id, run_id, operation_id, schema_version, connector_id, connector_revision, account_id, service, operation, region, target_json, catalog_digest, source, retrieved_at, coverage_json, outcome, evidence_artifact_id, evidence_sha256) "
            "VALUES (:id, :project, :conversation, :run, :operation_id, 2, :connector, :revision, '123456789012', 's3', 'list_buckets', 'us-east-1', '{}', :catalog, 'aws_read', :retrieved, '{}', 'measured', :artifact, :digest)"
        )
        invalid_rows = (
            {"revision": 99, "operation": operation_id, "artifact": artifact_id},
            {"revision": 1, "operation": uuid.uuid4(), "artifact": artifact_id},
            {"revision": 1, "operation": operation_id, "artifact": uuid.uuid4()},
        )
        for invalid in invalid_rows:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    generic_insert,
                    {
                        "id": _uuid(connection, uuid.uuid4()),
                        "project": _uuid(connection, project_id),
                        "conversation": _uuid(connection, conversation_id),
                        "run": _uuid(connection, run_id),
                        "operation_id": _uuid(connection, invalid["operation"]),
                        "connector": _uuid(connection, connector_id),
                        "revision": invalid["revision"],
                        "catalog": "b" * 64,
                        "retrieved": datetime.now(UTC),
                        "artifact": _uuid(connection, invalid["artifact"]),
                        "digest": digest,
                    },
                )
    engine.dispose()
