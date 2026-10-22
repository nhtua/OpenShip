"""Owner-scoped conversation archive APIs and retained history reads."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.chat.models import Conversation, Message
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


@pytest.fixture
def archive_context(client, monkeypatch):
    owner_id = uuid.uuid4()
    foreign_owner_id = uuid.uuid4()
    owner = User(
        id=owner_id,
        username=f"archive-owner-{owner_id.hex[:12]}",
        email=f"archive-owner-{owner_id.hex}@example.test",
        password_hash="test-only",
    )
    foreign_owner = User(
        id=foreign_owner_id,
        username=f"archive-foreign-{foreign_owner_id.hex[:12]}",
        email=f"archive-foreign-{foreign_owner_id.hex}@example.test",
        password_hash="test-only",
    )
    active_project = Project(
        id=uuid.uuid4(), owner_user_id=owner_id, name="Active project"
    )
    parent_project = Project(
        id=uuid.uuid4(), owner_user_id=owner_id, name="Parent archived project"
    )
    foreign_project = Project(
        id=uuid.uuid4(), owner_user_id=foreign_owner_id, name="Foreign project"
    )
    active_conversation = Conversation(
        id=uuid.uuid4(),
        user_id=owner_id, project_id=active_project.id, title="Active conversation"
    )
    individual_conversation = Conversation(
        id=uuid.uuid4(),
        user_id=owner_id,
        project_id=active_project.id,
        title="Individually archived conversation",
    )
    parent_conversation = Conversation(
        id=uuid.uuid4(),
        user_id=owner_id,
        project_id=parent_project.id,
        title="Conversation in archived project",
    )
    foreign_conversation = Conversation(
        id=uuid.uuid4(),
        user_id=foreign_owner_id,
        project_id=foreign_project.id,
        title="Foreign conversation",
    )
    retained_message = Message(
        conversation_id=individual_conversation.id,
        role="user",
        content="Retained history",
    )
    with SessionLocal() as db:
        db.add_all(
            [
                owner,
                foreign_owner,
                active_project,
                parent_project,
                foreign_project,
                active_conversation,
                individual_conversation,
                parent_conversation,
                foreign_conversation,
                retained_message,
            ]
        )
        db.commit()
        active_project_id = active_project.id
        parent_project_id = parent_project.id
        parent_project_name = parent_project.name
        active_conversation_id = active_conversation.id
        individual_conversation_id = individual_conversation.id
        parent_conversation_id = parent_conversation.id
        foreign_conversation_id = foreign_conversation.id

    monkeypatch.setitem(
        client.app.dependency_overrides,
        require_jwt,
        lambda: SimpleNamespace(id=owner_id),
    )
    return SimpleNamespace(
        client=client,
        owner_id=owner_id,
        active_project_id=active_project_id,
        parent_project_id=parent_project_id,
        parent_project_name=parent_project_name,
        active_conversation_id=active_conversation_id,
        individual_conversation_id=individual_conversation_id,
        parent_conversation_id=parent_conversation_id,
        foreign_conversation_id=foreign_conversation_id,
    )


def test_conversation_lists_use_same_lifecycle_filter(archive_context):
    client = archive_context.client
    individual_id = str(archive_context.individual_conversation_id)
    parent_id = str(archive_context.parent_conversation_id)

    archived = client.post(f"/api/conversations/{individual_id}/archive")
    archived_parent = client.post(
        f"/api/projects/{archive_context.parent_project_id}/archive"
    )

    assert archived.status_code == 200
    assert archived.json()["archived_at"]
    assert archived_parent.status_code == 200
    assert archived_parent.json()["archived_at"]
    creation_in_archived_project = client.post(
        "/api/conversations",
        json={
            "title": "Must restore parent first",
            "project_id": str(archive_context.parent_project_id),
        },
    )
    assert creation_in_archived_project.status_code == 409
    assert creation_in_archived_project.json()["error"]["code"] == "project_archived"

    active = client.get("/api/conversations?state=active")
    active_workspace = client.get("/api/workspace/conversations?state=active")
    assert active.status_code == active_workspace.status_code == 200
    assert {item["id"] for item in active.json()} == {
        str(archive_context.active_conversation_id)
    }
    assert {item["id"] for item in active_workspace.json()} == {
        str(archive_context.active_conversation_id)
    }
    assert active_workspace.json()[0]["message_count"] == 0

    archived_list = client.get("/api/conversations?state=archived")
    archived_workspace = client.get("/api/workspace/conversations?state=archived")
    assert archived_list.status_code == archived_workspace.status_code == 200
    assert {item["id"] for item in archived_list.json()} == {individual_id, parent_id}
    assert {item["id"] for item in archived_workspace.json()} == {individual_id, parent_id}
    parent_row = next(item for item in archived_list.json() if item["id"] == parent_id)
    assert parent_row["project_name"] == archive_context.parent_project_name
    assert parent_row["archived_at"] is None
    assert parent_row["project_archived_at"]
    individual_row = next(item for item in archived_list.json() if item["id"] == individual_id)
    assert individual_row["archived_at"]
    assert individual_row["project_archived_at"] is None
    assert client.get("/api/conversations?state=unknown").status_code == 422
    assert client.get("/api/workspace/conversations?state=unknown").status_code == 422

    metadata = client.get(f"/api/conversations/{individual_id}")
    messages = client.get(f"/api/conversations/{individual_id}/messages")
    snapshot = client.get(f"/api/conversations/{individual_id}/snapshot")
    assert metadata.status_code == 200
    assert metadata.json()["id"] == individual_id
    assert metadata.json()["project_name"] == "Active project"
    assert metadata.json()["archived_at"]
    assert messages.status_code == 200
    assert [item["content"] for item in messages.json()] == ["Retained history"]
    assert snapshot.status_code == 200
    assert snapshot.json()["archived_at"]
    assert snapshot.json()["project_archived_at"] is None
    assert [item["content"] for item in snapshot.json()["messages"]] == [
        "Retained history"
    ]

    foreign_id = str(archive_context.foreign_conversation_id)
    assert client.get(f"/api/conversations/{foreign_id}").status_code == 404
    assert client.post(f"/api/conversations/{foreign_id}/archive").status_code == 404
    assert client.post(f"/api/conversations/{foreign_id}/restore").status_code == 404


def test_parent_restore_preserves_child_archive(archive_context):
    client = archive_context.client
    conversation_id = str(archive_context.parent_conversation_id)
    project_id = str(archive_context.parent_project_id)

    archived_conversation = client.post(
        f"/api/conversations/{conversation_id}/archive"
    )
    archived_project = client.post(f"/api/projects/{project_id}/archive")
    blocked_restore = client.post(f"/api/conversations/{conversation_id}/restore")

    assert archived_conversation.status_code == 200
    assert archived_project.status_code == 200
    assert blocked_restore.status_code == 409
    assert blocked_restore.json()["error"]["code"] == "project_archived"

    restored_project = client.post(f"/api/projects/{project_id}/restore")
    assert restored_project.status_code == 200
    still_archived = client.get("/api/conversations?state=archived")
    assert {item["id"] for item in still_archived.json()} == {conversation_id}
    assert still_archived.json()[0]["project_archived_at"] is None

    restored_conversation = client.post(
        f"/api/conversations/{conversation_id}/restore"
    )
    assert restored_conversation.status_code == 200
    assert restored_conversation.json()["id"] == conversation_id
    assert restored_conversation.json()["archived_at"] is None
    active = client.get("/api/conversations?state=active")
    assert conversation_id in {item["id"] for item in active.json()}


def test_conversation_archive_restore_are_idempotent_and_preserve_identity(archive_context):
    client = archive_context.client
    conversation_id = str(archive_context.individual_conversation_id)

    first_archive = client.post(f"/api/conversations/{conversation_id}/archive")
    repeated_archive = client.post(f"/api/conversations/{conversation_id}/archive")
    assert first_archive.status_code == repeated_archive.status_code == 200
    assert first_archive.json()["id"] == repeated_archive.json()["id"] == conversation_id
    assert first_archive.json()["archived_at"] == repeated_archive.json()["archived_at"]

    first_restore = client.post(f"/api/conversations/{conversation_id}/restore")
    repeated_restore = client.post(f"/api/conversations/{conversation_id}/restore")
    assert first_restore.status_code == repeated_restore.status_code == 200
    assert first_restore.json()["id"] == repeated_restore.json()["id"] == conversation_id
    assert first_restore.json()["archived_at"] is None
    assert repeated_restore.json()["archived_at"] is None
