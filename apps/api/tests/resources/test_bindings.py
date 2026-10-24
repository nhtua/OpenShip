from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.connectors.service import delete_connector
from src.openship.database.models import Base
from src.openship.resources.models import (
    Environment,
    ResourceBinding,
    ResourceObject,
)
from src.openship.resources.routes import (
    environments_create,
    environments_list,
    resources_create,
    resources_list,
)
from src.openship.resources.routes import (
    router as resource_router,
)
from src.openship.resources.service import list_resource_bindings
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
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
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


def _seed_legacy_binding(db, *, user, project, connector, bucket_name="history-bucket"):
    environment = Environment(
        id=uuid.uuid4(),
        project_id=project.id,
        name="production",
        display_name="Production",
        created_by_user_id=user.id,
    )
    resource_id = uuid.uuid4()
    resource = ResourceObject(
        id=resource_id,
        project_id=project.id,
        kind="aws_s3_bucket",
        uri=f"openship://{project.id}/resource/{resource_id}",
        display_name="Production data",
        created_by_user_id=user.id,
    )
    db.add_all([environment, resource])
    db.flush()
    binding = ResourceBinding(
        id=uuid.uuid4(),
        project_id=project.id,
        resource_object_id=resource.id,
        environment_id=environment.id,
        connector_id=connector.id,
        connector_revision=1,
        revision=1,
        account_id=ACCOUNT_ID,
        region="us-west-2",
        bucket_name=bucket_name,
        bucket_arn=f"arn:aws:s3:::{bucket_name}",
        expected_owner=ACCOUNT_ID,
        management_method="external",
        state="active",
        created_by_user_id=user.id,
    )
    db.add(binding)
    db.flush()
    return environment, resource, binding


def test_resource_setup_routes_are_retired_and_owner_scoped(resource_sessions):
    with resource_sessions() as db:
        user, project, _connector = _scope(db, "retired-owner")
        _foreign_user, foreign_project, _foreign_connector = _scope(db, "retired-foreign")

        for endpoint in (environments_create, resources_create):
            with pytest.raises(HTTPException) as caught:
                endpoint(project.id, db, user)
            assert caught.value.status_code == 410
            assert caught.value.detail["error"]["code"] == "resource_binding_retired"

        route_statuses = {
            route.path: route.status_code
            for route in resource_router.routes
            if getattr(route, "methods", set()) == {"POST"}
        }
        assert route_statuses["/api/projects/{project_id}/environments"] == 410
        assert route_statuses["/api/projects/{project_id}/resources"] == 410

        with pytest.raises(HTTPException) as caught:
            resources_create(foreign_project.id, db, user)
        assert caught.value.status_code == 404
        assert caught.value.detail["error"]["code"] == "project_not_found"

        project.archived_at = datetime.now(UTC)
        db.commit()
        with pytest.raises(HTTPException) as caught:
            environments_create(project.id, db, user)
        assert caught.value.status_code == 410
        assert db.query(Environment).filter(Environment.project_id == project.id).count() == 0
        assert db.query(ResourceBinding).filter(ResourceBinding.project_id == project.id).count() == 0
        assert db.query(ResourceObject).filter(ResourceObject.project_id == project.id).count() == 0


def test_legacy_binding_lists_remain_owner_scoped_and_readable(resource_sessions):
    with resource_sessions() as db:
        user, project, connector = _scope(db, "history")
        _foreign_user, foreign_project, _foreign_connector = _scope(db, "foreign-history")
        environment, resource, binding = _seed_legacy_binding(
            db, user=user, project=project, connector=connector
        )
        db.commit()

        environments = environments_list(project.id, db, user)
        assert environments[0]["id"] == str(environment.id)
        page = resources_list(project.id, 20, None, db, user)
        assert [item.binding_id for item in page.items] == [binding.id]
        assert page.items[0].resource_id == resource.id
        assert page.items[0].bucket_name == "history-bucket"

        items, next_cursor = list_resource_bindings(
            db, owner_id=user.id, project_id=project.id, limit=20
        )
        assert [item.binding_id for item in items] == [binding.id]
        assert next_cursor is None
        with pytest.raises(HTTPException) as caught:
            resources_list(foreign_project.id, 20, None, db, user)
        assert caught.value.status_code == 404


def test_connector_revocation_purges_secrets_and_retains_legacy_resource_history(
    resource_sessions,
):
    with resource_sessions() as db:
        user, project, connector = _scope(db, "revoke-history")
        _environment, resource, binding = _seed_legacy_binding(
            db, user=user, project=project, connector=connector
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
        retained_binding = db.get(ResourceBinding, binding.id)
        retained_resource = db.get(ResourceObject, resource.id)

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
