from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.service import delete_connector
from src.openship.database.models import Base
from src.openship.database.session import SessionLocal, get_db
from src.openship.resources import service as resource_service
from src.openship.resources.contracts import EnvironmentCreate, ResourceBindingCreate
from src.openship.resources.models import ResourceBinding, ResourceObject
from src.openship.resources.routes import router
from src.openship.resources.service import (
    ResourceServiceError,
    create_environment,
    create_resource_binding,
    list_resource_bindings,
)
from src.openship.workspace.models import Project

ACCOUNT_ID = "123456789012"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/ReadOnly"


@pytest.fixture
def resource_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resource-bindings.db'}")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        engine.dispose()


def _scope(db, label: str):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"resource-{label}-{user_id.hex[:10]}",
        email=f"resource-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(id=uuid.uuid4(), owner_user_id=user_id, name=f"Project {label}")
    db.add(user)
    db.flush()
    db.add(project)
    db.flush()
    connector_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    configuration = ConnectorConfiguration(
        kind="aws",
        region="us-west-2",
        expected_account_id=ACCOUNT_ID,
        basic_role_arn=ROLE_ARN,
    )
    connector = Connector(
        id=connector_id,
        kind="aws",
        visibility="project",
        project_id=project.id,
        name="Read only account",
        current_revision=1,
        enabled=True,
        managed_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    credential = CredentialVersion(
        id=credential_id,
        connector_id=connector_id,
        visibility="project",
        project_id=project.id,
        kind="aws_bootstrap",
        schema_version=1,
        key_id="test-v1",
        nonce=b"n" * 12,
        ciphertext=b"c" * 32,
        state="active",
    )
    db.add(connector)
    db.flush()
    db.add(credential)
    db.flush()
    db.add(
        ConnectorRevision(
            id=uuid.uuid4(),
            connector_id=connector_id,
            revision=1,
            schema_version=1,
            visibility="project",
            project_id=project.id,
            configuration_json=configuration.canonical_bytes().decode(),
            configuration_digest=configuration.digest(),
            credential_version_id=credential_id,
            created_by_user_id=user.id,
        )
    )
    db.flush()
    return user, project, connector


def test_owned_environment_and_bucket_binding_are_local_identity_only():
    with SessionLocal() as db:
        user, project, connector = _scope(db, "create")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="development", display_name="Development"),
        )
        resource, binding = create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=ResourceBindingCreate(
                environment_id=environment.id,
                connector_id=connector.id,
                connector_revision=1,
                bucket_name="openship-dev-data-123",
                display_name="Development data",
            ),
        )

        assert environment.project_id == project.id
        assert resource.project_id == project.id
        assert resource.uri == f"openship://{project.id}/resource/{resource.id}"
        assert resource.display_name == "Development data"
        assert binding.environment_id == environment.id
        assert binding.connector_id == connector.id
        assert binding.account_id == ACCOUNT_ID
        assert binding.expected_owner == ACCOUNT_ID
        assert binding.region == "us-west-2"
        assert binding.bucket_name == "openship-dev-data-123"
        assert binding.bucket_arn == "arn:aws:s3:::openship-dev-data-123"
        assert binding.management_method == "external"

        items, next_cursor = list_resource_bindings(
            db, owner_id=user.id, project_id=project.id, limit=20
        )
        assert [item.binding_id for item in items] == [binding.id]
        assert next_cursor is None


@pytest.mark.asyncio
async def test_binding_http_accepts_uuid_strings_and_persists_selected_target(resource_sessions):
    with resource_sessions() as db:
        user, project, connector = _scope(db, "http-create")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="development", display_name="Development"),
        )
        db.commit()

        async def database():
            yield db

        async def authenticated_user():
            return user

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_jwt] = authenticated_user
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/projects/{project.id}/resources",
                json={
                    "environment_id": str(environment.id),
                    "connector_id": str(connector.id),
                    "connector_revision": 1,
                    "bucket_name": "example-public-data",
                    "display_name": "Public data",
                },
            )

            assert response.status_code == 201, response.text
            created = response.json()
            assert created["environment_id"] == str(environment.id)
            assert created["connector_id"] == str(connector.id)
            assert created["connector_revision"] == 1
            assert created["bucket_name"] == "example-public-data"
            assert created["expected_owner"] == ACCOUNT_ID
            assert created["region"] == "us-west-2"

            listed = await client.get(f"/api/projects/{project.id}/resources")
            assert listed.status_code == 200, listed.text
            assert listed.json()["items"] == [created]


def test_binding_and_resource_object_roll_back_together(resource_sessions):
    with resource_sessions() as db:
        user, project, connector = _scope(db, "rollback")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="development", display_name="Development"),
        )
        db.commit()

        resource, binding = create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=ResourceBindingCreate(
                environment_id=environment.id,
                connector_id=connector.id,
                connector_revision=1,
                bucket_name="example-rollback-data",
                display_name="Rollback data",
            ),
        )
        resource_id, binding_id = resource.id, binding.id
        db.rollback()

    with resource_sessions() as db:
        assert db.get(ResourceObject, resource_id) is None
        assert db.get(ResourceBinding, binding_id) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment_id", "not-a-uuid"),
        ("connector_id", "not-a-uuid"),
        ("environment_id", 123),
        ("connector_id", 123),
        ("connector_revision", "1"),
        ("connector_revision", True),
        ("connector_revision", 0),
    ],
)
def test_binding_request_rejects_invalid_ids_and_revisions(field, value):
    payload = {
        "environment_id": uuid.uuid4(),
        "connector_id": uuid.uuid4(),
        "connector_revision": 1,
        "bucket_name": "example-public-data",
        "display_name": "Public data",
    }
    payload[field] = value

    with pytest.raises(ValidationError) as caught:
        ResourceBindingCreate.model_validate(payload)

    assert [error["loc"] for error in caught.value.errors()] == [(field,)]


def test_binding_rejects_foreign_environment_and_duplicate_active_target():
    with SessionLocal() as db:
        user, project, connector = _scope(db, "scope")
        other_user, other_project, _other_connector = _scope(db, "other-scope")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="production", display_name="Production"),
        )
        foreign_environment = create_environment(
            db,
            owner_id=other_user.id,
            project_id=other_project.id,
            request=EnvironmentCreate(name="production", display_name="Production"),
        )
        request = ResourceBindingCreate(
            environment_id=environment.id,
            connector_id=connector.id,
            connector_revision=1,
            bucket_name="openship-prod-data-123",
            display_name="Production data",
        )
        create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=request,
        )

        with pytest.raises(ResourceServiceError) as caught:
            create_resource_binding(
                db,
                owner_id=user.id,
                project_id=project.id,
                request=request,
            )
        assert caught.value.code == "resource_binding_conflict"

        with pytest.raises(ResourceServiceError) as caught:
            create_resource_binding(
                db,
                owner_id=user.id,
                project_id=project.id,
                request=request.model_copy(update={"environment_id": foreign_environment.id}),
            )
        assert caught.value.code == "resource_scope_denied"


def test_environment_and_bucket_contracts_are_bounded_and_reject_reserved_names():
    with pytest.raises(ValidationError):
        EnvironmentCreate(name="Production Space", display_name="Production")
    with pytest.raises(ValidationError):
        ResourceBindingCreate(
            environment_id=uuid.uuid4(),
            connector_id=uuid.uuid4(),
            connector_revision=1,
            bucket_name="192.168.5.4",
            display_name="Invalid bucket",
        )
    with pytest.raises(ValidationError):
        ResourceBindingCreate(
            environment_id=uuid.uuid4(),
            connector_id=uuid.uuid4(),
            connector_revision=1,
            bucket_name="example--x-s3",
            display_name="Directory bucket",
        )


def test_binding_limit_counts_retained_revisions(monkeypatch):
    monkeypatch.setattr(resource_service, "MAX_BINDINGS_PER_PROJECT", 1)
    with SessionLocal() as db:
        user, project, connector = _scope(db, "retained-limit")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="production", display_name="Production"),
        )
        request = ResourceBindingCreate(
            environment_id=environment.id,
            connector_id=connector.id,
            connector_revision=1,
            bucket_name="openship-retained-data-123",
            display_name="Retained data",
        )
        _resource, binding = create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=request,
        )
        binding.state = "retired"
        binding.retired_at = datetime.now(UTC)
        db.commit()

        with pytest.raises(ResourceServiceError) as caught:
            create_resource_binding(
                db,
                owner_id=user.id,
                project_id=project.id,
                request=request.model_copy(
                    update={"bucket_name": "openship-retained-data-456"}
                ),
            )
        assert caught.value.code == "resource_limit_exceeded"


def test_revoking_bound_aws_connector_purges_secrets_and_retains_resource_history():
    with SessionLocal() as db:
        user, project, connector = _scope(db, "revoke-bound")
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=EnvironmentCreate(name="production", display_name="Production"),
        )
        resource, binding = create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project.id,
            request=ResourceBindingCreate(
                environment_id=environment.id,
                connector_id=connector.id,
                connector_revision=1,
                bucket_name="openship-revoke-data-123",
                display_name="Retained production data",
            ),
        )

        result = delete_connector(
            db,
            connector=connector,
            actor=user,
            expected_revision=1,
        )
        db.commit()

        credential = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector.id)
            .one()
        )
        revision = (
            db.query(ConnectorRevision)
            .filter(ConnectorRevision.connector_id == connector.id)
            .one()
        )
        retained_binding = db.get(type(binding), binding.id)
        retained_resource = db.get(type(resource), resource.id)

        assert result == {"id": str(connector.id), "deleted": True}
        assert connector.revoked_at is not None
        assert credential.state == "revoked"
        assert credential.revoked_at is not None
        assert credential.purged_at is not None
        assert credential.key_id == "purged"
        assert credential.nonce == b"\x00" * 12
        assert credential.ciphertext == b"\x00" * 16
        assert revision.id is not None
        assert retained_binding is not None
        assert retained_resource is not None
        assert retained_binding.connector_id == connector.id
        assert retained_binding.state == "active"
