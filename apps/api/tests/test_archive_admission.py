"""Fresh run admission is fenced by archive state; accepted replays remain readable."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ToolCommandRequest
from src.openship.chat.lifecycle import archive_conversation
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.connectors.selection import ConnectorSelectionError
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Event, Outbox, Run, RunJob
from src.openship.runs.service import submit_tool_run, submit_turn
from src.openship.workspace.models import Project
from src.openship.workspace.projects import archive_project


def _scope(db):
    suffix = uuid.uuid4().hex
    user = User(
        username=f"archive-admission-{suffix[:12]}",
        email=f"{suffix}@example.test",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Primary")
    backup = Project(owner_user_id=user.id, name="Backup")
    db.add_all([project, backup])
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="History")
    db.add(conversation)
    db.commit()
    return user, project, conversation


def _counts(db, conversation_id):
    run_ids = select(Run.id).where(Run.conversation_id == conversation_id)
    return (
        db.query(Run).filter(Run.conversation_id == conversation_id).count(),
        db.query(Message).filter(Message.conversation_id == conversation_id).count(),
        db.query(RunJob).filter(RunJob.run_id.in_(run_ids)).count(),
        db.query(Event).filter(Event.conversation_id == conversation_id).count(),
        db.query(Outbox).filter(Outbox.conversation_id == conversation_id).count(),
    )


def _archive(db, scope, user, project, conversation):
    if scope in {"conversation", "both"}:
        archive_conversation(db, owner_id=user.id, conversation_id=conversation.id)
    if scope in {"project", "both"}:
        archive_project(db, owner_id=user.id, project_id=project.id)
    db.commit()


def _tool_command(request_id, query="archive admission"):
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    return ToolCommandRequest(
        client_request_id=request_id,
        capability=CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        ),
        arguments={"query": query, "max_results": 2},
    )


@pytest.mark.parametrize("scope", ["conversation", "project", "both"])
@pytest.mark.parametrize("kind", ["chat", "tool"])
def test_archived_scope_rejects_fresh_admission_and_preserves_exact_replay(
    scope, kind, monkeypatch
):
    db = SessionLocal()
    try:
        user, project, conversation = _scope(db)
        if kind == "tool":
            monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")

            def no_managed_connector(*_args, **_kwargs):
                raise ConnectorSelectionError("connector_required")

            monkeypatch.setattr(
                "src.openship.runs.service.resolve_connector", no_managed_connector
            )
            monkeypatch.setattr(
                "src.openship.runs.service._connector_rows_for_project",
                lambda *_args, **_kwargs: 0,
            )
            original_request_id = uuid.uuid4()
            original = _tool_command(original_request_id)
            accepted = submit_tool_run(db, user.id, conversation.id, original)
            db.commit()
        else:
            original_request_id = str(uuid.uuid4())
            accepted = submit_turn(
                db,
                user.id,
                str(conversation.id),
                "accepted before archive",
                original_request_id,
                provider_profile_id="primary-fixture",
            )

        before = _counts(db, conversation.id)
        _archive(db, scope, user, project, conversation)

        if kind == "tool":
            replay = submit_tool_run(db, user.id, conversation.id, original)
        else:
            replay = submit_turn(
                db,
                user.id,
                str(conversation.id),
                "accepted before archive",
                original_request_id,
                provider_profile_id="primary-fixture",
            )
        assert replay.id == accepted.id
        assert _counts(db, conversation.id) == before

        if kind == "tool":
            fresh = _tool_command(uuid.uuid4(), query="new work after archive")
            changed_replay = _tool_command(original_request_id, query="changed command")
            admit = lambda command: submit_tool_run(db, user.id, conversation.id, command)
            changed_call = lambda: admit(changed_replay)
        else:
            fresh_request_id = str(uuid.uuid4())
            admit = lambda _command: submit_turn(
                db,
                user.id,
                str(conversation.id),
                "new work after archive",
                fresh_request_id,
                provider_profile_id="primary-fixture",
            )
            changed_call = lambda: submit_turn(
                db,
                user.id,
                str(conversation.id),
                "changed content",
                original_request_id,
                provider_profile_id="primary-fixture",
            )
            fresh = None

        with pytest.raises(HTTPException) as refused:
            admit(fresh)
        assert refused.value.status_code == 409
        expected_code = "conversation_archived" if scope == "conversation" else "project_archived"
        assert refused.value.detail["error"]["code"] == expected_code
        assert _counts(db, conversation.id) == before

        with pytest.raises(HTTPException) as conflict:
            changed_call()
        assert conflict.value.status_code == 409
        assert conflict.value.detail["error"]["code"] in {
            "client_request_id_conflict",
        }
        assert _counts(db, conversation.id) == before
    finally:
        db.rollback()
        db.close()
