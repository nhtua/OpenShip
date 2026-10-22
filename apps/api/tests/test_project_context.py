"""Owned project identity is available before and throughout a conversation."""

import uuid
from types import SimpleNamespace

import pytest

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


@pytest.fixture
def owned_context(client):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"project-context-{user_id.hex[:12]}",
        email=f"project-context-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    own_project_id = uuid.uuid4()
    foreign_user_id = uuid.uuid4()
    foreign_user = User(
        id=foreign_user_id,
        username=f"foreign-project-{foreign_user_id.hex[:12]}",
        email=f"foreign-project-{foreign_user_id.hex}@example.test",
        password_hash="test-only",
    )
    foreign_project_id = uuid.uuid4()
    own_project = Project(
        owner_user_id=user_id, name="Personal project", id=own_project_id
    )
    foreign_project = Project(
        owner_user_id=foreign_user_id, name="Foreign project", id=foreign_project_id
    )
    with SessionLocal() as db:
        db.add_all([user, own_project, foreign_user, foreign_project])
        db.commit()
    client.app.dependency_overrides[require_jwt] = lambda: SimpleNamespace(id=user_id)
    yield (
        client,
        SimpleNamespace(id=own_project_id, name="Personal project"),
        SimpleNamespace(id=foreign_project_id, name="Foreign project"),
    )
    client.app.dependency_overrides.pop(require_jwt, None)


def test_project_list_is_authenticated_and_contains_only_owned_projects(owned_context):
    client, own_project, foreign_project = owned_context
    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == [
        {"id": str(own_project.id), "name": own_project.name}
    ]
    assert str(foreign_project.id) not in response.text


def test_project_list_requires_authentication(client):
    response = client.get("/api/projects")

    assert response.status_code == 401


def test_conversation_and_snapshot_responses_include_project_identity(owned_context):
    client, project, _foreign_project = owned_context
    headers = {}

    created = client.post(
        "/api/conversations",
        headers=headers,
        json={"title": "Project context", "project_id": str(project.id)},
    )

    assert created.status_code == 201
    assert created.json()["project_id"] == str(project.id)
    conversation_id = created.json()["id"]
    snapshot = client.get(
        f"/api/conversations/{conversation_id}/snapshot", headers=headers
    )
    listed = client.get("/api/workspace/conversations", headers=headers)

    assert snapshot.status_code == 200
    assert snapshot.json()["project_id"] == str(project.id)
    listed_conversation = next(
        item for item in listed.json() if item["id"] == conversation_id
    )
    assert listed_conversation["project_id"] == str(project.id)


def test_conversation_creation_rejects_a_foreign_project(owned_context):
    client, _own_project, foreign_project = owned_context

    response = client.post(
        "/api/conversations",
        json={"title": "Must stay owned", "project_id": str(foreign_project.id)},
    )

    assert response.status_code == 404
    assert "Must stay owned" not in response.text
