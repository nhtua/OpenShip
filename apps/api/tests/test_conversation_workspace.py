"""Conversation workspaces lazily bind existing immutable snapshots."""

from __future__ import annotations

import uuid

import pytest

from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.workspace.models import ConversationWorkspace, Project
from src.openship.workspace.routes import get_conversation_workspace
from src.openship.workspace.service import ensure_workspace
from tests.conftest import TestSessionLocal


def _conversation(db):
    suffix = uuid.uuid4().hex
    user = User(
        username=f"workspace_{suffix[:10]}",
        email=f"workspace_{suffix}@example.test",
        password_hash="fixture-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Workspace fixture")
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        project_id=project.id,
        title="Existing conversation",
    )
    db.add(conversation)
    db.flush()
    return user, project, conversation


def test_workspace_is_lazy_stable_and_does_not_rewrite_existing_artifacts(tmp_path):
    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        artifact_id = uuid.uuid4()
        storage_key = (
            f"projects/{project.id}/conversations/{conversation.id}/"
            f"artifacts/{artifact_id}/content"
        )
        artifact = Artifact(
            id=artifact_id,
            project_id=project.id,
            conversation_id=conversation.id,
            kind="upload",
            media_type="text/plain",
            display_name="existing.txt",
            size_bytes=3,
            sha256="a" * 64,
            relative_storage_key=storage_key,
            status="ready",
            source="upload",
            mutability="immutable",
        )
        db.add(artifact)
        db.flush()
        store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
        store.initialize()

        assert db.query(ConversationWorkspace).filter_by(conversation_id=conversation.id).first() is None
        first = ensure_workspace(db, user.id, conversation.id, store)
        first_id = first.id
        db.commit()

        conversation.title = "Retitled after a model change"
        second = ensure_workspace(db, user.id, conversation.id, store)
        db.commit()

        assert second.id == first_id
        assert second.status == "available"
        assert second.created_at is not None
        assert second.storage_id == store.storage_id
        assert second.relative_root == f"projects/{project.id}/conversations/{conversation.id}"
        assert not (store.root / second.relative_root).exists()
        retained = db.query(Artifact).filter_by(id=artifact_id).one()
        assert retained.relative_storage_key == storage_key
        assert retained.sha256 == "a" * 64
        assert retained.status == "ready"


def test_missing_storage_is_reported_without_creating_a_replacement(tmp_path):
    with TestSessionLocal() as db:
        user, _project, conversation = _conversation(db)
        root = tmp_path / "missing-artifacts"
        store = LocalArtifactStore(root, uuid.uuid4())

        workspace = ensure_workspace(db, user.id, conversation.id, store)
        db.commit()

        assert workspace.status == "unavailable"
        assert not root.exists()


def test_workspace_is_not_created_for_a_conversation_owned_by_another_user(tmp_path):
    with TestSessionLocal() as db:
        _user, _project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
        store.initialize()

        import pytest

        from src.openship.workspace.service import WorkspaceServiceError

        with pytest.raises(WorkspaceServiceError) as caught:
            ensure_workspace(db, uuid.uuid4(), conversation.id, store)
        assert caught.value.code == "conversation_not_found"
        assert db.query(ConversationWorkspace).filter_by(conversation_id=conversation.id).first() is None


@pytest.mark.asyncio
async def test_conversation_workspace_route_returns_safe_association_and_file_page(
    monkeypatch, tmp_path
):
    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        storage_id = uuid.uuid4()
        root = tmp_path / "route-artifacts"
        LocalArtifactStore(root, storage_id).initialize()
        monkeypatch.setattr(settings, "artifact_root", root)
        monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
        for artifact_id in (uuid.uuid4(), uuid.uuid4()):
            db.add(
                Artifact(
                    id=artifact_id,
                    project_id=project.id,
                    conversation_id=conversation.id,
                    kind="upload",
                    media_type="text/plain",
                    display_name="duplicate-name.txt",
                    size_bytes=1,
                    sha256="a" * 64,
                    relative_storage_key=(
                        f"projects/{project.id}/conversations/{conversation.id}/"
                        f"artifacts/{artifact_id}/content"
                    ),
                    status="ready",
                    source="upload",
                    mutability="immutable",
                )
            )
        db.commit()

        response = await get_conversation_workspace(
            str(conversation.id), limit=1, cursor=None, db=db, user=user
        )
        next_page = await get_conversation_workspace(
            str(conversation.id),
            limit=1,
            cursor=response["files"]["next_cursor"],
            db=db,
            user=user,
        )

        assert response["workspace"]["conversation_id"] == str(conversation.id)
        assert response["workspace"]["project_id"] == str(project.id)
        assert response["workspace"]["status"] == "available"
        assert response["workspace"]["relative_root"] == (
            f"projects/{project.id}/conversations/{conversation.id}"
        )
        assert str(root) not in str(response)
        assert len(response["files"]["items"]) == 1
        assert response["files"]["next_cursor"]
        assert len(next_page["files"]["items"]) == 1
        assert next_page["files"]["items"][0]["id"] != response["files"]["items"][0]["id"]
        assert response["files"]["items"][0]["name"] == "duplicate-name.txt"
