"""Owner-scoped Project lifecycle and unambiguous conversation assignment."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.chat.routes import ConversationCreateRequest, create_conversation
from src.openship.database.session import SessionLocal
from src.openship.workspace.project_routes import (
    _ProjectName,
    project_create,
    project_delete,
    project_get,
    project_list,
    project_rename,
)
from src.openship.workspace.project_routes import (
    router as project_router,
)
from src.openship.workspace.projects import (
    ProjectLifecycleError,
    create_project,
    delete_project,
    get_owned_project,
    list_owned_projects,
    rename_project,
)


@pytest.fixture
def project_owner():
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"project-lifecycle-{user_id.hex[:12]}",
        email=f"{user_id.hex}@example.test",
        password_hash="test-only",
    )
    with SessionLocal() as db:
        db.add(user)
        db.commit()
    return SimpleNamespace(id=user_id)


def _create_project(db, owner_id, name):
    project = create_project(db, owner_id=owner_id, name=name)
    db.commit()
    return project


def test_owner_can_create_list_and_rename_multiple_projects(project_owner):
    with SessionLocal() as db:
        first = _create_project(db, project_owner.id, "  Production  ")
        second = _create_project(db, project_owner.id, "Staging")
        renamed = rename_project(
            db,
            owner_id=project_owner.id,
            project_id=second.id,
            name="  Preview  ",
        )
        db.commit()

        assert first.name == "Production"
        assert renamed.name == "Preview"
        assert {item.id for item in list_owned_projects(db, project_owner.id)} == {
            first.id,
            second.id,
        }
        assert get_owned_project(
            db, owner_id=project_owner.id, project_id=first.id
        ).name == "Production"


def test_authenticated_project_routes_expose_crud_for_owned_projects(project_owner):
    from src.openship.main import app

    with SessionLocal() as db:
        actor = db.get(User, project_owner.id)
        first = project_create(_ProjectName(name="  Production  "), db, actor)
        second = project_create(_ProjectName(name="Staging"), db, actor)

        assert first["name"] == "Production"
        assert len(first["id"]) == 36
        assert {item["id"] for item in project_list(db, actor)} == {
            first["id"],
            second["id"],
        }
        assert project_get(uuid.UUID(second["id"]), db, actor) == second
        renamed = project_rename(
            uuid.UUID(second["id"]), _ProjectName(name="Preview"), db, actor
        )
        assert renamed["name"] == "Preview"
        assert project_delete(uuid.UUID(second["id"]), db, actor).status_code == 204
        assert project_list(db, actor) == [first]
        with pytest.raises(HTTPException) as invalid_name:
            project_create(_ProjectName(name="   "), db, actor)
        assert invalid_name.value.status_code == 422

    api_project_routes = {
        (route.path, method): route
        for route in project_router.routes
        for method in route.methods
    }
    assert ("/api/projects", "GET") in api_project_routes
    assert api_project_routes[("/api/projects", "POST")].status_code == 201
    assert ("/api/projects/{project_id}", "GET") in api_project_routes
    assert ("/api/projects/{project_id}", "PATCH") in api_project_routes
    assert api_project_routes[("/api/projects/{project_id}", "DELETE")].status_code == 204
    assert all(
        any(dependency.call.__name__ == "require_jwt" for dependency in route.dependant.dependencies)
        for route in api_project_routes.values()
    )
    registered_project_path = app.openapi()["paths"]["/api/projects"]
    assert "get" in registered_project_path
    assert "post" in registered_project_path


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_project_names_must_be_trimmed_nonempty_and_at_most_255(name, project_owner):
    with SessionLocal() as db, pytest.raises(ProjectLifecycleError) as invalid:
        create_project(db, owner_id=project_owner.id, name=name)

    assert invalid.value.code == "project_name_invalid"


def test_foreign_project_reads_and_mutations_are_hidden(project_owner):
    foreign_user = User(
        username=f"foreign-project-{uuid.uuid4().hex[:12]}",
        email=f"{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    with SessionLocal() as db:
        db.add(foreign_user)
        db.commit()
        foreign = _create_project(db, foreign_user.id, "Private")

        with pytest.raises(ProjectLifecycleError) as read:
            get_owned_project(db, owner_id=project_owner.id, project_id=foreign.id)
        assert read.value.code == "project_not_found"

        with pytest.raises(ProjectLifecycleError) as rename:
            rename_project(
                db,
                owner_id=project_owner.id,
                project_id=foreign.id,
                name="Changed",
            )
        assert rename.value.code == "project_not_found"
        db.rollback()

        with pytest.raises(ProjectLifecycleError) as delete:
            delete_project(db, owner_id=project_owner.id, project_id=foreign.id)
        assert delete.value.code == "project_not_found"
        db.rollback()
        assert db.get(type(foreign), foreign.id) is not None


def test_delete_allows_only_empty_projects_and_preserves_one_owned_project(project_owner):
    with SessionLocal() as db:
        retained = _create_project(db, project_owner.id, "Retained")
        removable = _create_project(db, project_owner.id, "Empty")

        delete_project(db, owner_id=project_owner.id, project_id=removable.id)
        db.commit()
        assert db.get(type(removable), removable.id) is None

        with pytest.raises(ProjectLifecycleError) as last_project:
            delete_project(db, owner_id=project_owner.id, project_id=retained.id)
        assert last_project.value.code == "last_project_required"
        db.rollback()
        assert db.get(type(retained), retained.id) is not None


def test_delete_refuses_projects_with_retained_conversations(project_owner):
    with SessionLocal() as db:
        project = _create_project(db, project_owner.id, "Has history")
        _create_project(db, project_owner.id, "Keep last-project rule separate")
        conversation = Conversation(
            user_id=project_owner.id,
            project_id=project.id,
            title="Retained conversation",
        )
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id

        with pytest.raises(ProjectLifecycleError) as retained:
            delete_project(db, owner_id=project_owner.id, project_id=project.id)
        assert retained.value.code == "project_not_empty"
        db.rollback()
        assert db.get(Conversation, conversation_id) is not None


def test_delete_refuses_project_defaults_artifact_quotas_and_audit_records(project_owner):
    from src.openship.artifacts.models import ArtifactQuota
    from src.openship.connectors.models import (
        ConnectorAuditEvent,
        ProjectConnectorDefault,
    )

    with SessionLocal() as db:
        project = _create_project(db, project_owner.id, "Has retained settings")
        _create_project(db, project_owner.id, "Keep last-project rule separate")
        db.add_all(
            [
                ArtifactQuota(scope="project", project_id=project.id),
                ProjectConnectorDefault(
                    project_id=project.id,
                    service="brave",
                    updated_by_user_id=project_owner.id,
                ),
                ConnectorAuditEvent(
                    visibility="project",
                    project_id=project.id,
                    actor_user_id=project_owner.id,
                    action="created",
                    outcome="success",
                ),
            ]
        )
        db.commit()

        with pytest.raises(ProjectLifecycleError) as retained:
            delete_project(db, owner_id=project_owner.id, project_id=project.id)

        assert retained.value.code == "project_not_empty"
        db.rollback()
        assert get_owned_project(db, owner_id=project_owner.id, project_id=project.id)


def test_conversation_requires_explicit_project_when_owner_has_multiple(project_owner):
    with SessionLocal() as db:
        actor = db.get(User, project_owner.id)
        first = _create_project(db, actor.id, "First")

        initial = asyncio.run(
            create_conversation(ConversationCreateRequest(title="One project"), db, actor)
        )
        assert initial.project_id == str(first.id)

        second = _create_project(db, actor.id, "Second")
        before = db.query(Conversation).filter(Conversation.user_id == actor.id).count()
        with pytest.raises(HTTPException) as ambiguous:
            asyncio.run(
                create_conversation(
                    ConversationCreateRequest(title="Choose a project"), db, actor
                )
            )
        assert ambiguous.value.status_code == 409
        assert ambiguous.value.detail["error"]["code"] == "project_required"
        assert db.query(Conversation).filter(Conversation.user_id == actor.id).count() == before

        selected = asyncio.run(
            create_conversation(
                ConversationCreateRequest(title="Explicit project", project_id=second.id),
                db,
                actor,
            )
        )
        assert selected.project_id == str(second.id)
