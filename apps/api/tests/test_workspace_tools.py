"""Bounded workspace tools can read only exact current-conversation snapshots."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from pydantic import ValidationError

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.chat.models import Conversation
from src.openship.providers.control import InvocationControl
from src.openship.workspace.models import ConversationWorkspace, Project
from src.openship.workspace.routes import read_conversation_workspace_file
from src.openship.workspace.schemas import (
    WorkspaceListArguments,
    WorkspaceReadArguments,
)
from src.openship.workspace.tools import (
    WorkspaceToolError,
    list_workspace_files,
    read_workspace_file,
)
from tests.conftest import TestSessionLocal


def _conversation(db):
    suffix = uuid.uuid4().hex
    user = User(
        username=f"files_{suffix[:10]}",
        email=f"files_{suffix}@example.test",
        password_hash="fixture-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="File fixture")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="File fixture")
    db.add(conversation)
    db.flush()
    return user, project, conversation


def _save_artifact(db, store, project, conversation, content: bytes, *, name="same.txt"):
    artifact_id = uuid.uuid4()
    digest = hashlib.sha256(content).hexdigest()
    reference = ArtifactRef(
        id=artifact_id,
        sha256=digest,
        size_bytes=len(content),
        media_type="text/plain",
    )
    staged = store.stage(
        artifact_id,
        (content,),
        reference,
        project_id=project.id,
        conversation_id=conversation.id,
    )
    storage_key = (
        f"projects/{project.id}/conversations/{conversation.id}/"
        f"artifacts/{artifact_id}/content"
    )
    store.publish_file(staged, storage_key)
    artifact = Artifact(
        id=artifact_id,
        project_id=project.id,
        conversation_id=conversation.id,
        kind="upload",
        media_type="text/plain",
        display_name=name,
        size_bytes=len(content),
        sha256=digest,
        relative_storage_key=storage_key,
        status="ready",
        mutability="immutable",
        source="upload",
    )
    db.add(artifact)
    db.flush()
    return artifact


def _context(user, project, conversation, store):
    return ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user.id,
            project_id=project.id,
            conversation_id=conversation.id,
            action="execute_tool",
            target_id=str(uuid.uuid4()),
        ),
        execution=ExecutionConfig(graph_kind="chat", graph_version=2),
        credential_resolver=lambda _name: None,
        artifact_store=store,
        session_factory=TestSessionLocal,
    )


def test_workspace_arguments_are_strict_and_bounded():
    with pytest.raises(ValidationError):
        WorkspaceListArguments(limit=True)
    with pytest.raises(ValidationError):
        WorkspaceListArguments(limit=51)
    with pytest.raises(ValidationError):
        WorkspaceReadArguments(
            artifact_id=uuid.uuid4(), offset=0, max_bytes=16385, path="/etc/passwd"
        )
    with pytest.raises(ValidationError):
        WorkspaceReadArguments(artifact_id=uuid.uuid4(), offset=-1)


@pytest.mark.asyncio
async def test_workspace_list_returns_an_empty_page_for_a_new_conversation(tmp_path):
    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "empty-artifacts", uuid.uuid4())
        store.initialize()
        db.commit()

        result = await list_workspace_files(
            WorkspaceListArguments(),
            _context(user, project, conversation, store),
            InvocationControl(),
        )

        assert result.items == ()
        assert result.next_cursor is None


@pytest.mark.asyncio
async def test_workspace_tools_scope_files_and_page_utf8_without_changing_bytes(tmp_path):
    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        _other_user, other_project, other_conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
        store.initialize()
        artifact = _save_artifact(db, store, project, conversation, "A🌙B".encode())
        second_artifact = _save_artifact(
            db, store, project, conversation, b"second", name="same.txt"
        )
        _save_artifact(db, store, other_project, other_conversation, b"private")
        db.commit()
        context = _context(user, project, conversation, store)

        page = await list_workspace_files(
            WorkspaceListArguments(limit=1), context, InvocationControl()
        )
        assert len(page.items) == 1
        assert page.next_cursor
        assert page.items[0].name == "same.txt"
        assert page.items[0].id in {artifact.id, second_artifact.id}
        next_page = await list_workspace_files(
            WorkspaceListArguments(limit=1, cursor=page.next_cursor),
            context,
            InvocationControl(),
        )
        assert len(next_page.items) == 1
        assert next_page.items[0].id in {artifact.id, second_artifact.id}
        assert next_page.items[0].id != page.items[0].id
        assert next_page.items[0].name == page.items[0].name

        first = await read_workspace_file(
            WorkspaceReadArguments(artifact_id=artifact.id, offset=1, max_bytes=4),
            context,
            InvocationControl(),
        )
        assert first.content == "🌙"
        assert first.offset == 1
        assert first.next_offset == 5
        assert first.sha256 == artifact.sha256
        second = await read_workspace_file(
            WorkspaceReadArguments(artifact_id=artifact.id, offset=5, max_bytes=4),
            context,
            InvocationControl(),
        )
        assert second.content == "B"
        assert second.next_offset is None
        with pytest.raises(WorkspaceToolError, match="UTF-8 boundary"):
            await read_workspace_file(
                WorkspaceReadArguments(artifact_id=artifact.id, offset=2),
                context,
                InvocationControl(),
            )


@pytest.mark.asyncio
async def test_workspace_read_refuses_a_file_from_another_conversation(tmp_path):
    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        _other_user, other_project, other_conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
        store.initialize()
        foreign = _save_artifact(db, store, other_project, other_conversation, b"private")
        db.commit()

        with pytest.raises(WorkspaceToolError) as caught:
            await read_workspace_file(
                WorkspaceReadArguments(artifact_id=foreign.id),
                _context(user, project, conversation, store),
                InvocationControl(),
            )
        assert caught.value.code == "artifact_not_found"


@pytest.mark.asyncio
async def test_workspace_file_http_read_is_bounded_and_conversation_scoped(
    monkeypatch, tmp_path
):
    from src.openship.config import settings

    with TestSessionLocal() as db:
        user, project, conversation = _conversation(db)
        _other_user, other_project, other_conversation = _conversation(db)
        storage_id = uuid.uuid4()
        root = tmp_path / "workspace-http"
        store = LocalArtifactStore(root, storage_id)
        store.initialize()
        artifact = _save_artifact(db, store, project, conversation, "A🌙B".encode())
        foreign = _save_artifact(db, store, other_project, other_conversation, b"private")
        db.commit()
        monkeypatch.setattr(settings, "artifact_root", root)
        monkeypatch.setattr(settings, "artifact_storage_id", storage_id)

        response = await read_conversation_workspace_file(
            str(conversation.id),
            str(artifact.id),
            offset=1,
            max_bytes=4,
            db=db,
            user=user,
        )
        assert response["artifact_id"] == str(artifact.id)
        assert response["version_id"] == str(artifact.id)
        assert response["sha256"] == artifact.sha256
        assert response["content"] == "🌙"
        assert response["next_offset"] == 5

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            await read_conversation_workspace_file(
                str(conversation.id),
                str(foreign.id),
                offset=0,
                max_bytes=16,
                db=db,
                user=user,
            )
        assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_workspace_file_http_read_returns_safe_unavailable_after_purge(
    monkeypatch, tmp_path
):
    from fastapi import HTTPException

    from src.openship.config import settings
    from src.openship.workspace.service import ensure_workspace

    with TestSessionLocal() as db:
        user, _project, conversation = _conversation(db)
        storage_id = uuid.uuid4()
        root = tmp_path / "purged-workspace"
        store = LocalArtifactStore(root, storage_id)
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        db.commit()
        row = db.query(ConversationWorkspace).filter_by(id=workspace.id).one()
        row.status = "deleted"
        db.commit()
        monkeypatch.setattr(settings, "artifact_root", root)
        monkeypatch.setattr(settings, "artifact_storage_id", storage_id)

        with pytest.raises(HTTPException) as caught:
            await read_conversation_workspace_file(
                str(conversation.id),
                str(uuid.uuid4()),
                offset=0,
                max_bytes=16,
                db=db,
                user=user,
            )

        assert caught.value.status_code == 503
        assert caught.value.detail["error"]["code"] == "workspace_unavailable"
