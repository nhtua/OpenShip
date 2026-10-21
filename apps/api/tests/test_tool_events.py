"""Tool and artifact read models remain authoritative after event pruning."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.events.service import read_snapshot
from src.openship.execution.models import ToolAttempt, ToolOperation
from src.openship.execution.reads import read_operation_page
from src.openship.runs.models import Event, Run
from src.openship.runs.routes import get_run_operations
from src.openship.workspace.models import Project


def _conversation(db):
    user = User(
        username=f"tool_read_{uuid.uuid4().hex[:12]}",
        email=f"tool_read_{uuid.uuid4().hex}@example.com",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Tool read model")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Search")
    db.add(conversation)
    db.flush()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        kind="tool",
        graph_version=1,
        status="completed",
        fence=1,
    )
    db.add(run)
    db.flush()
    return user, project, conversation, run


def _published_search(db, user, project, conversation, run, *, query="terraform modules"):
    manifest = next(item for item in list_capability_manifests() if item.id == "brave_web_search")
    operation_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    observed_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    artifact = Artifact(
        id=artifact_id,
        project_id=project.id,
        conversation_id=conversation.id,
        originating_run_id=run.id,
        originating_operation_id=operation_id,
        kind="search_result",
        media_type="application/json",
        display_name="search-results.json",
        size_bytes=512,
        sha256="a" * 64,
        relative_storage_key=f"projects/{project.id}/conversations/{conversation.id}/artifacts/{artifact_id}/content",
        status="ready",
        mutability="immutable",
        source="brave_web_search",
        created_at=observed_at,
    )
    result = {
        "operation_id": str(operation_id),
        "tool": {
            "id": manifest.id,
            "version": manifest.version,
            "manifest_digest": manifest.manifest_digest,
        },
        "observed_at": observed_at.isoformat(),
        "query": query,
        "items": [
            {
                "title": "Terraform modules",
                "url": "https://docs.example.test/modules",
                "snippet": "A retained result.",
                "rank": 1,
            }
        ],
        "truncated": False,
        "artifact": {
            "id": str(artifact_id),
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
        },
    }
    operation = ToolOperation(
        id=operation_id,
        run_id=run.id,
        project_id=project.id,
        conversation_id=conversation.id,
        principal_id=user.id,
        logical_call_id=f"manual-{operation_id}",
        capability_id=manifest.id,
        capability_version=manifest.version,
        manifest_digest=manifest.manifest_digest,
        arguments_json=json.dumps({"query": query, "max_results": 5}),
        arguments_digest="b" * 64,
        request_digest="c" * 64,
        configuration_revision="phase4a",
        execution_bundle_digest="d" * 64,
        status="succeeded",
        result_json=json.dumps(result),
        result_digest="e" * 64,
        output_artifact_ids=json.dumps([str(artifact_id)]),
        created_at=observed_at,
        updated_at=observed_at,
        finished_at=observed_at,
    )
    attempt = ToolAttempt(
        operation_id=operation_id,
        ordinal=1,
        fence=1,
        outcome="succeeded",
        request_digest="c" * 64,
        response_digest="f" * 64,
        dispatch_started_at=observed_at,
        finished_at=observed_at,
    )
    db.add_all([artifact, operation, attempt])
    db.flush()
    return operation, artifact


def test_snapshot_rebuilds_operations_and_artifacts_after_events_are_pruned():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        operation, artifact = _published_search(db, user, project, conversation, run)
        event = Event(
            project_id=project.id,
            conversation_id=conversation.id,
            run_id=run.id,
            sequence=0,
            type="tool.completed",
            payload="{}",
        )
        db.add(event)
        conversation.next_event_sequence = 1
        db.commit()
        db.delete(event)
        db.commit()  # retained rows, unlike this event, rebuild the operation.

        snapshot = read_snapshot(db, user.id, str(conversation.id))

        assert snapshot.events == []
        assert snapshot.runs[0].kind == "tool"
        assert snapshot.runs[0].graph_version == 1
        read_operation = snapshot.runs[0].operations[0]
        assert read_operation.id == operation.id
        assert read_operation.query == "terraform modules"
        assert read_operation.result["query"] == "terraform modules"
        assert read_operation.result["items"][0]["url"] == "https://docs.example.test/modules"
        assert read_operation.attempts[0].outcome == "succeeded"
        assert read_operation.artifacts[0].id == artifact.id
        assert snapshot.artifacts[0].id == artifact.id
    finally:
        db.close()


def test_operation_pages_are_bounded_ordered_and_secret_safe():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        _published_search(db, user, project, conversation, run, query="first search")
        _published_search(db, user, project, conversation, run, query="second search")
        db.commit()

        first_page = read_operation_page(db, run.id, limit=1)
        second_page = read_operation_page(
            db, run.id, limit=1, cursor=first_page.next_cursor
        )

        assert len(first_page.items) == 1
        assert first_page.next_cursor
        assert len(second_page.items) == 1
        assert second_page.next_cursor is None
        serialized = first_page.model_dump_json()
        assert "credential_binding_ref" not in serialized
        assert "arguments_json" not in serialized
        assert "request_digest" not in serialized
    finally:
        db.close()


def test_operation_projection_keeps_validated_query_for_a_failed_search():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        operation, _ = _published_search(db, user, project, conversation, run)
        operation.status = "failed"
        operation.error_code = "provider_unavailable"
        operation.result_json = None
        db.commit()

        projected = read_operation_page(db, run.id).items[0]

        assert projected.query == "terraform modules"
        assert projected.result is None
        assert projected.error is not None
        assert "arguments_json" not in projected.model_dump_json()
    finally:
        db.close()


def test_operation_projection_omits_a_query_that_fails_search_validation():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        operation, _ = _published_search(db, user, project, conversation, run)
        operation.status = "failed"
        operation.error_code = "provider_unavailable"
        operation.result_json = None
        operation.arguments_json = json.dumps({"query": " padded query ", "max_results": 5})
        db.commit()

        projected = read_operation_page(db, run.id).items[0]

        assert projected.query is None
        assert "padded query" not in projected.model_dump_json()
        assert "arguments_json" not in projected.model_dump_json()
    finally:
        db.close()


def test_run_operations_route_enforces_owner_and_returns_continuations():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        _published_search(db, user, project, conversation, run, query="first search")
        _published_search(db, user, project, conversation, run, query="second search")
        foreign_user = User(
            username=f"foreign_{uuid.uuid4().hex[:12]}",
            email=f"foreign_{uuid.uuid4().hex}@example.com",
            password_hash="test-hash",
        )
        db.add(foreign_user)
        db.commit()

        first_page = asyncio.run(
            get_run_operations(str(run.id), limit=1, cursor=None, db=db, user=user)
        )
        second_page = asyncio.run(
            get_run_operations(
                str(run.id), limit=1, cursor=first_page.next_cursor, db=db, user=user
            )
        )

        assert len(first_page.items) == len(second_page.items) == 1
        assert first_page.next_cursor
        assert second_page.next_cursor is None
        assert first_page.items[0].id != second_page.items[0].id
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                get_run_operations(str(run.id), limit=1, cursor=None, db=db, user=foreign_user)
            )
        assert error.value.status_code == 404
    finally:
        db.close()


def test_snapshot_artifact_pages_continue_without_repeating_rows():
    db = SessionLocal()
    try:
        user, project, conversation, run = _conversation(db)
        _published_search(db, user, project, conversation, run, query="first retained result")
        _published_search(db, user, project, conversation, run, query="second retained result")
        db.commit()

        first_page = read_snapshot(db, user.id, str(conversation.id), artifact_limit=1)
        second_page = read_snapshot(
            db,
            user.id,
            str(conversation.id),
            artifact_limit=1,
            artifact_cursor=first_page.artifacts_next_cursor,
        )

        assert len(first_page.artifacts) == len(second_page.artifacts) == 1
        assert first_page.artifacts_next_cursor
        assert second_page.artifacts_next_cursor is None
        assert first_page.artifacts[0].id != second_page.artifacts[0].id
    finally:
        db.close()
