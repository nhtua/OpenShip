"""Owner-scoped Project lifecycle and unambiguous conversation assignment."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.auth.models import User
from src.openship.chat.models import Conversation, Message
from src.openship.chat.routes import ConversationCreateRequest, create_conversation
from src.openship.connectors.models import ProjectConnectorDefault
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import ConversationWorkspace
from src.openship.workspace.project_routes import (
    _ProjectName,
    project_archive,
    project_create,
    project_delete,
    project_get,
    project_list,
    project_rename,
    project_restore,
)
from src.openship.workspace.project_routes import (
    router as project_router,
)
from src.openship.workspace.projects import (
    ProjectLifecycleError,
    archive_project,
    create_project,
    delete_project,
    get_owned_project,
    list_owned_projects,
    rename_project,
    restore_project,
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


def test_project_archive_preserves_populated_scope(project_owner, monkeypatch):
    import src.openship.workspace.projects as project_services

    with SessionLocal() as db:
        project = _create_project(db, project_owner.id, "Has retained scope")
        _create_project(db, project_owner.id, "Keep one active")
        conversation = Conversation(
            user_id=project_owner.id,
            project_id=project.id,
            title="Retained conversation",
        )
        db.add(conversation)
        db.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="Retained message",
        )
        artifact = Artifact(
            project_id=project.id,
            conversation_id=conversation.id,
            kind="upload",
            media_type="text/plain",
            display_name="state.txt",
            size_bytes=4,
            sha256="a" * 64,
            relative_storage_key="projects/retained/conversations/state.txt",
            status="ready",
            source="upload",
            mutability="immutable",
        )
        quota = ArtifactQuota(
            scope="project",
            project_id=project.id,
            committed_bytes=4,
            committed_count=1,
        )
        workspace = ConversationWorkspace(
            conversation_id=conversation.id,
            project_id=project.id,
            schema_version=1,
            relative_root="projects/retained/conversations/existing",
            storage_id=uuid.uuid4(),
            status="available",
        )
        default = ProjectConnectorDefault(
            project_id=project.id,
            service="brave",
            updated_by_user_id=project_owner.id,
        )
        db.add_all([message, artifact, quota, workspace, default])
        db.commit()
        project_id = project.id
        conversation_id = conversation.id
        message_id = message.id
        artifact_id = artifact.id
        quota_id = quota.id
        workspace_id = workspace.id
        workspace_storage_id = workspace.storage_id
        default_id = default.id
        artifact_key = artifact.relative_storage_key
        monkeypatch.setattr(
            project_services,
            "_has_retained_data",
            lambda *_args, **_kwargs: pytest.fail("archive must not scan or delete retained scope"),
            raising=False,
        )

        actor = db.get(User, project_owner.id)
        response = project_delete(project.id, db, actor)
        assert response.status_code == 204

        assert get_owned_project(db, owner_id=project_owner.id, project_id=project.id).archived_at
        assert db.get(Conversation, conversation_id) is not None
        assert db.get(Message, message_id).content == "Retained message"
        retained_artifact = db.get(Artifact, artifact_id)
        assert retained_artifact.relative_storage_key == artifact_key
        assert retained_artifact.status == "ready"
        assert db.get(ArtifactQuota, quota_id).committed_bytes == 4
        retained_workspace = db.get(ConversationWorkspace, workspace_id)
        assert retained_workspace.relative_root == "projects/retained/conversations/existing"
        assert retained_workspace.storage_id == workspace_storage_id
        retained_default = db.get(ProjectConnectorDefault, default_id)
        assert retained_default.project_id == project_id


def test_archive_restore_idempotency(project_owner):
    with SessionLocal() as db:
        project = _create_project(db, project_owner.id, "Archive me")
        _create_project(db, project_owner.id, "Keep active")

        archived = archive_project(db, owner_id=project_owner.id, project_id=project.id)
        first_archive_time = archived.archived_at
        db.commit()
        repeated = archive_project(db, owner_id=project_owner.id, project_id=project.id)
        assert repeated.archived_at == first_archive_time
        db.commit()

        restored = restore_project(db, owner_id=project_owner.id, project_id=project.id)
        assert restored.archived_at is None
        db.commit()
        assert restore_project(db, owner_id=project_owner.id, project_id=project.id).archived_at is None

        conversation = Conversation(
            user_id=project_owner.id, project_id=project.id, title="Lifecycle"
        )
        db.add(conversation)
        db.commit()
        from src.openship.chat.lifecycle import (
            archive_conversation,
            restore_conversation,
        )

        archived_conversation, _ = archive_conversation(
            db, owner_id=project_owner.id, conversation_id=conversation.id
        )
        first_conversation_archive_time = archived_conversation.archived_at
        db.commit()
        repeated_conversation, _ = archive_conversation(
            db, owner_id=project_owner.id, conversation_id=conversation.id
        )
        assert repeated_conversation.archived_at == first_conversation_archive_time
        db.commit()
        restored_conversation, _ = restore_conversation(
            db, owner_id=project_owner.id, conversation_id=conversation.id
        )
        assert restored_conversation.archived_at is None
        db.commit()
        restored_again, _ = restore_conversation(
            db, owner_id=project_owner.id, conversation_id=conversation.id
        )
        assert restored_again.archived_at is None


def test_final_active_project_cannot_be_archived(project_owner):
    with SessionLocal() as db:
        project = _create_project(db, project_owner.id, "Only active project")

        with pytest.raises(ProjectLifecycleError) as last_project:
            archive_project(db, owner_id=project_owner.id, project_id=project.id)

        assert last_project.value.code == "last_project_required"
        db.rollback()
        assert get_owned_project(db, owner_id=project_owner.id, project_id=project.id).archived_at is None


def test_project_list_state_filters_and_validates(project_owner):
    with SessionLocal() as db:
        actor = db.get(User, project_owner.id)
        first = _create_project(db, project_owner.id, "Active")
        archived = _create_project(db, project_owner.id, "Archived")
        first_id = str(first.id)
        archived_id = str(archived.id)

        archived_response = project_archive(archived.id, db, actor)
        assert archived_response["archived_at"]
        assert {item["name"] for item in project_list(db, actor)} == {"Active"}
        with pytest.raises(HTTPException) as rejected_rename:
            project_rename(
                archived.id,
                _ProjectName(name="Still archived"),
                db,
                actor,
            )
        assert rejected_rename.value.status_code == 409
        assert rejected_rename.value.detail["error"]["code"] == "project_archived"
        archived_list = project_list(db, actor, state="archived")
        assert archived_list[0]["id"] == archived_id
        assert archived_list[0]["archived_at"]
        assert project_restore(archived.id, db, actor)["archived_at"] is None
        assert {item["id"] for item in project_list(db, actor)} == {
            first_id,
            archived_id,
        }


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
