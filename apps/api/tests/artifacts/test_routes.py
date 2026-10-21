import asyncio
import base64
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.routes import (
    download_artifact_content,
    router,
    upload_artifact,
)
from src.openship.artifacts.service import mark_artifact_ready, reserve_artifact
from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


def _request(body_chunks, *, key, body_digest, name="report.txt", media_type="text/plain"):
    chunks = list(body_chunks)
    headers = [
        (b"idempotency-key", key.encode()),
        (b"digest", ("sha-256=" + base64.b64encode(body_digest).decode()).encode()),
        (b"x-artifact-name", name.encode()),
        (b"content-type", media_type.encode()),
    ]

    async def receive():
        if chunks:
            body = chunks.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/upload", "headers": headers}, receive)


def _owned_conversation(db):
    suffix = uuid.uuid4().hex
    user = User(username=f"route_artifact_{suffix}", email=f"{suffix}@example.test", password_hash="hash")
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Route artifacts")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Files")
    db.add(conversation)
    db.commit()
    return user, project, conversation


def test_raw_chunked_upload_and_exact_digest_bound_replay(tmp_path, monkeypatch):
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_root", Path(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    db = SessionLocal()
    try:
        from src.openship.artifacts.filesystem import LocalArtifactStore

        LocalArtifactStore(settings.artifact_root, storage_id).initialize()
        user, _, conversation = _owned_conversation(db)
        body = b"chunk one " + b"chunk two"
        digest = hashlib.sha256(body).digest()
        key = str(uuid.uuid4())

        first = asyncio.run(
            upload_artifact(
                str(conversation.id),
                _request((body[:10], body[10:]), key=key, body_digest=digest),
                db,
                user,
            )
        )
        assert first.status_code == 201
        first_body = json.loads(first.body)

        replay = asyncio.run(
            upload_artifact(
                str(conversation.id),
                _request((body,), key=key, body_digest=digest),
                db,
                user,
            )
        )
        assert replay.status_code == 200
        assert json.loads(replay.body)["id"] == first_body["id"]

        changed = b"different bytes"
        with pytest.raises(HTTPException) as conflict:
            asyncio.run(
                upload_artifact(
                    str(conversation.id),
                    _request((changed,), key=key, body_digest=hashlib.sha256(changed).digest()),
                    db,
                    user,
                )
            )
        assert conflict.value.status_code == 409
    finally:
        db.close()


def test_html_like_plain_text_is_only_returned_as_nosniff_attachment(tmp_path, monkeypatch):
    storage_id = uuid.uuid4()
    root = Path(tmp_path / "artifacts")
    monkeypatch.setattr(settings, "artifact_root", root)
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    db = SessionLocal()
    try:
        from src.openship.artifacts.filesystem import LocalArtifactStore

        store = LocalArtifactStore(root, storage_id)
        store.initialize()
        user, project, conversation = _owned_conversation(db)
        body = b"<script>should not execute</script>"
        digest = hashlib.sha256(body).hexdigest()
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            len(body),
            "upload",
            str(uuid.uuid4()),
            expected_sha256=digest,
            display_name="report.txt",
            media_type="text/plain",
        )
        db.commit()
        observed = ArtifactRef(
            id=artifact.id,
            sha256=digest,
            size_bytes=len(body),
            media_type="text/plain",
        )
        staged = store.stage(
            artifact.id,
            (body,),
            observed,
            project_id=project.id,
            conversation_id=conversation.id,
        )
        store.publish_file(staged, artifact.relative_storage_key)
        ready = mark_artifact_ready(db, artifact.id, observed)
        db.commit()

        class CapturedResponse:
            def __init__(self, content, *, media_type, headers):
                self.content = content
                self.media_type = media_type
                self.headers = {key.lower(): value for key, value in headers.items()}

        monkeypatch.setattr(
            "src.openship.artifacts.routes.StreamingResponse", CapturedResponse
        )
        response = asyncio.run(download_artifact_content(str(ready.id), db, user))
        chunks = list(response.content)

        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.media_type == "text/plain"
        assert b"".join(chunks) == body
    finally:
        db.close()


def test_artifact_routes_require_jwt_and_publish_attachment_only_content():
    from src.openship.main import app

    content_route = next(
        route for route in router.routes if route.path == "/api/artifacts/{artifact_id}/content"
    )
    assert require_jwt in [dependency.call for dependency in content_route.dependant.dependencies]
    assert "/api/artifacts/{artifact_id}/content" in app.openapi()["paths"]
