import asyncio
import hashlib
import json
import uuid

import pytest
from fastapi import HTTPException

from src.openship.artifacts.file_routes import (
    CaptureRequest,
    create_from_artifact,
    list_files,
    list_versions,
    prune_version,
    replace_content,
)
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.logical_files import advance_file, create_file
from src.openship.artifacts.models import Artifact, FileVersion, LogicalFile
from src.openship.artifacts.routes import upload_artifact
from src.openship.artifacts.service import begin_artifact_delete
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.workspace.maintenance import plan_workspace_purge, purge_workspace
from src.openship.workspace.service import ensure_workspace
from tests.artifacts.test_routes import _owned_conversation, _request


def test_current_head_foreign_key_binds_the_version_to_its_logical_file():
    foreign_keys = [
        constraint
        for constraint in LogicalFile.__table__.foreign_key_constraints
        if constraint.name == "fk_logical_files_current_version"
    ]
    assert len(foreign_keys) == 1
    assert [column.name for column in foreign_keys[0].columns] == [
        "id",
        "current_version_id",
    ]
    assert [element.column.name for element in foreign_keys[0].elements] == [
        "file_id",
        "id",
    ]


def test_version_edit_replay_and_head_pin(tmp_path, monkeypatch):
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    LocalArtifactStore(settings.artifact_root, storage_id).initialize()
    with SessionLocal() as db:
        user, _, conv = _owned_conversation(db)

        def upload(body):
            response = asyncio.run(
                upload_artifact(
                    str(conv.id),
                    _request(
                        (body,),
                        key=str(uuid.uuid4()),
                        body_digest=hashlib.sha256(body).digest(),
                    ),
                    db,
                    user,
                )
            )
            return uuid.UUID(json.loads(response.body)["id"])

        first, second = upload(b"first"), upload(b"second")
        key = uuid.uuid4()
        captured = create_from_artifact(
            conv.id, CaptureRequest(artifact_id=first, request_key=key), db, user
        )
        file_id = uuid.UUID(captured["file"]["id"])
        version_id = uuid.UUID(captured["version"]["id"])
        assert captured["file"]["current_artifact"]["id"] == str(first)
        assert create_from_artifact(
            conv.id, CaptureRequest(artifact_id=first, request_key=key), db, user
        )["version"]["id"] == str(version_id)
        file = db.get(LogicalFile, file_id)
        version = db.get(FileVersion, version_id)
        key2 = uuid.uuid4()
        next_version = advance_file(
            db, user.id, conv.id, file.id, version.id, second, key2
        )
        db.commit()
        assert (
            advance_file(db, user.id, conv.id, file.id, version.id, second, key2).id
            == next_version.id
        )
        with pytest.raises(HTTPException) as conflict:
            advance_file(db, user.id, conv.id, file.id, version.id, first, uuid.uuid4())
        assert conflict.value.detail["error"]["code"] == "file_version_conflict"
        with pytest.raises(HTTPException) as pinned:
            begin_artifact_delete(db, user.id, second)
        assert pinned.value.detail["error"]["code"] == "artifact_is_pinned"

        with pytest.raises(HTTPException) as different_payload:
            advance_file(db, user.id, conv.id, file.id, version.id, first, key2)
        assert (
            different_payload.value.detail["error"]["code"] == "file_request_conflict"
        )

        other_user, _, other_conv = _owned_conversation(db)
        with pytest.raises(HTTPException) as wrong_owner:
            advance_file(
                db,
                other_user.id,
                conv.id,
                file.id,
                next_version.id,
                first,
                uuid.uuid4(),
            )
        assert wrong_owner.value.status_code == 404
        with pytest.raises(HTTPException) as wrong_conversation:
            create_file(db, other_user.id, other_conv.id, second, uuid.uuid4())
        assert wrong_conversation.value.status_code == 404

        listed = list_files(conv.id, 50, None, db, user)
        assert [item["id"] for item in listed["items"]] == [str(file.id)]
        assert listed["items"][0]["current_artifact"]["id"] == str(second)
        versions = list_versions(conv.id, file.id, 50, None, db, user)
        assert {item["artifact_id"] for item in versions["items"]} == {
            str(first),
            str(second),
        }
        with pytest.raises(HTTPException) as unauthorized_list:
            list_versions(conv.id, file.id, 50, None, db, other_user)
        assert unauthorized_list.value.status_code == 404

        store = LocalArtifactStore(settings.artifact_root, storage_id)
        workspace = ensure_workspace(db, user.id, conv.id, store)
        db.commit()
        plan = plan_workspace_purge(db, workspace.id)
        dry_run = purge_workspace(db, store, plan, dry_run=True)
        assert dry_run.failed == 0
        db.expire_all()
        assert db.get(type(file), file.id).deleted_at is None
        plan = plan_workspace_purge(db, workspace.id)
        report = purge_workspace(db, store, plan)
        assert report.failed == 0
        assert report.deleted == 2
        db.expire_all()
        assert db.get(type(file), file.id).deleted_at is not None


def test_prune_rejects_head_and_deletes_superseded_capture(tmp_path, monkeypatch):
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    LocalArtifactStore(settings.artifact_root, storage_id).initialize()
    with SessionLocal() as db:
        user, _, conv = _owned_conversation(db)

        def upload(body):
            response = asyncio.run(
                upload_artifact(
                    str(conv.id),
                    _request(
                        (body,),
                        key=str(uuid.uuid4()),
                        body_digest=hashlib.sha256(body).digest(),
                    ),
                    db,
                    user,
                )
            )
            return uuid.UUID(json.loads(response.body)["id"])

        first, second = upload(b"first"), upload(b"second")
        file, version = create_file(db, user.id, conv.id, first, uuid.uuid4())
        db.commit()
        current = advance_file(
            db, user.id, conv.id, file.id, version.id, second, uuid.uuid4()
        )
        db.commit()
        with pytest.raises(HTTPException) as pinned:
            asyncio.run(prune_version(conv.id, file.id, current.id, db, user))
        assert pinned.value.detail["error"]["code"] == "artifact_is_pinned"
        deleted = asyncio.run(prune_version(conv.id, file.id, version.id, db, user))
        assert deleted["status"] == "deleted"
        assert list_versions(conv.id, file.id, 50, None, db, user)["items"]


def test_bounded_text_edit_creates_digest_bound_immutable_version(tmp_path, monkeypatch):
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)
    LocalArtifactStore(settings.artifact_root, storage_id).initialize()
    with SessionLocal() as db:
        user, _, conv = _owned_conversation(db)
        source = b"print('first')"
        uploaded = asyncio.run(
            upload_artifact(
                str(conv.id),
                _request(
                    (source,),
                    key=str(uuid.uuid4()),
                    body_digest=hashlib.sha256(source).digest(),
                    name="parser.py",
                ),
                db,
                user,
            )
        )
        source_id = uuid.UUID(json.loads(uploaded.body)["id"])
        file, version = create_file(db, user.id, conv.id, source_id, uuid.uuid4())
        db.commit()

        body = b"print('second')"
        key = uuid.uuid4()

        async def edit(content, request_key, base):
            chunks = [content]

            async def receive():
                if chunks:
                    return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
                return {"type": "http.request", "body": b"", "more_body": False}

            from starlette.requests import Request

            request = Request(
                {
                    "type": "http",
                    "method": "PUT",
                    "path": "/files/content",
                    "headers": [(b"content-type", b"text/plain")],
                },
                receive,
            )
            return await replace_content(
                conv.id, file.id, request, base, request_key, db, user
            )

        result = asyncio.run(edit(body, key, version.id))
        assert result["base_version_id"] == str(version.id)
        replay = asyncio.run(edit(body, key, version.id))
        assert replay["id"] == result["id"]
        version_row = db.get(FileVersion, uuid.UUID(result["id"]))
        assert version_row.artifact_id != source_id
        stored_artifact = db.get(Artifact, version_row.artifact_id)
        assert stored_artifact.sha256 == hashlib.sha256(body).hexdigest()
        with LocalArtifactStore(settings.artifact_root, storage_id).open_verified(stored_artifact) as handle:
            assert handle.read() == body
        db.refresh(file)
        assert file.current_version_id == version_row.id
        with pytest.raises(HTTPException) as stale:
            asyncio.run(edit(b"print('stale')", uuid.uuid4(), version.id))
        assert stale.value.detail["error"]["code"] == "file_version_conflict"
