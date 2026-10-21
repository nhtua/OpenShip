"""Fenced ordered model transcripts contain only complete normalized messages."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, ExecutionConfig
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.providers.contracts import ChatMessage, ToolCall, ToolCallDelta
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.transcript import append_transcript, read_transcript
from src.openship.workspace.models import Project


def _claim(db):
    user = User(
        username=f"transcript_{uuid.uuid4().hex[:12]}",
        email=f"transcript_{uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Transcript project")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Transcript")
    db.add(conversation)
    db.flush()
    run = Run(
        conversation_id=conversation.id,
        project_id=project.id,
        status="running",
        kind="chat",
        graph_version=2,
        fence=7,
        execution_configuration=ExecutionConfig(
            graph_kind="chat",
            graph_version=2,
            allowed_capabilities=(
                CapabilityRef(
                    id=list_capability_manifests()[0].id,
                    version=list_capability_manifests()[0].version,
                    manifest_digest=list_capability_manifests()[0].manifest_digest,
                ),
            ),
        ).model_dump_json(),
    )
    db.add(run)
    db.flush()
    lease_until = datetime.now(UTC) + timedelta(minutes=1)
    db.add(RunJob(run_id=run.id, fence=7, owner_id="worker-transcript", lease_until=lease_until))
    db.commit()
    claim = Claim(
        run_id=run.id,
        fence=7,
        lease_until=lease_until,
        worker_id="worker-transcript",
        user_id=user.id,
    )
    return claim


def test_transcript_round_trips_ordered_tool_call_and_result_without_internal_id_aliasing():
    db = SessionLocal()
    try:
        claim = _claim(db)
        append_transcript(db, claim, 0, ChatMessage(role="user", content="Find docs."))
        assistant = append_transcript(
            db,
            claim,
            0,
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(
                    provider_call_id="call_provider_77",
                    name="brave_web_search",
                    arguments={"query": "terraform modules"},
                ),),
            ),
        )
        tool = append_transcript(
            db,
            claim,
            0,
            ChatMessage(
                role="tool",
                name="brave_web_search",
                tool_call_id="call_provider_77",
                content='{"items": []}',
            ),
        )
        db.commit()

        entries = read_transcript(db, claim.run_id)
        assert [entry.ordinal for entry in entries] == [0, 1, 2]
        assert entries[0].message.content == "Find docs."
        assert entries[1].message.tool_calls[0].provider_call_id == "call_provider_77"
        assert assistant.logical_call_ids == ("model:0:call:0",)
        assert tool.logical_call_id == "model:0:call:0"
        assert tool.logical_call_id != tool.message.tool_call_id
    finally:
        db.close()


def test_transcript_rejects_unknown_or_repeated_tool_result_ids():
    db = SessionLocal()
    try:
        claim = _claim(db)
        unknown = ChatMessage(
            role="tool", name="brave_web_search", tool_call_id="unknown", content="{}"
        )
        with pytest.raises(ProviderError, match="tool call"):
            append_transcript(db, claim, 0, unknown)

        append_transcript(
            db,
            claim,
            0,
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(
                    provider_call_id="call_1",
                    name="brave_web_search",
                    arguments={"query": "docs"},
                ),),
            ),
        )
        valid_result = ChatMessage(
            role="tool", name="brave_web_search", tool_call_id="call_1", content="{}"
        )
        append_transcript(db, claim, 0, valid_result)
        with pytest.raises(ProviderError, match="already has a result"):
            append_transcript(db, claim, 0, valid_result)
    finally:
        db.rollback()
        db.close()


def test_transcript_rejects_a_call_outside_the_pinned_capability_allowlist():
    db = SessionLocal()
    try:
        claim = _claim(db)
        request = ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(
                provider_call_id="call_unallowed",
                name="tavily_web_search",
                arguments={"query": "docs"},
            ),),
        )
        with pytest.raises(ProviderError, match="did not authorize"):
            append_transcript(db, claim, 0, request)
    finally:
        db.rollback()
        db.close()


def test_transcript_allows_only_one_assistant_response_per_model_turn():
    db = SessionLocal()
    try:
        claim = _claim(db)
        append_transcript(db, claim, 1, ChatMessage(role="assistant", content="Done."))
        with pytest.raises(ProviderError, match="already has an assistant response"):
            append_transcript(db, claim, 1, ChatMessage(role="assistant", content="Another answer."))
    finally:
        db.rollback()
        db.close()


def test_transcript_never_persists_partial_provider_fragments():
    db = SessionLocal()
    try:
        claim = _claim(db)
        fragment = ToolCallDelta(
            index=0,
            id_delta="provider_",
            name_delta="brave_",
            arguments_delta='{"query":"untrusted partial',
        )
        with pytest.raises(ProviderError, match="Only complete normalized messages"):
            append_transcript(db, claim, 0, fragment)
        assert read_transcript(db, claim.run_id) == ()
    finally:
        db.rollback()
        db.close()


def test_transcript_append_requires_live_run_fence_and_is_caller_committed():
    db = SessionLocal()
    try:
        claim = _claim(db)
        first = append_transcript(db, claim, 0, ChatMessage(role="user", content="question"))
        assert first.ordinal == 0
        db.rollback()
        assert read_transcript(db, claim.run_id) == ()

        stale = Claim(
            run_id=claim.run_id,
            fence=claim.fence - 1,
            lease_until=claim.lease_until,
            worker_id=claim.worker_id,
            user_id=claim.user_id,
        )
        with pytest.raises(HTTPException) as expired:
            append_transcript(db, stale, 0, ChatMessage(role="user", content="question"))
        assert expired.value.detail["error"]["code"] == "lease_lost"
    finally:
        db.rollback()
        db.close()
