import base64
import hashlib
import json
import uuid

import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import ArtifactQuota
from src.openship.artifacts.service import (
    begin_artifact_delete,
    decode_artifact_name,
    finish_artifact_delete,
    mark_artifact_ready,
    parse_sha256_digest,
    reserve_artifact,
)
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Run
from src.openship.workspace.models import Project


def _conversation(db):
    suffix = uuid.uuid4().hex
    user = User(username=f"artifact_{suffix}", email=f"{suffix}@example.test", password_hash="hash")
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Artifact project")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Artifacts")
    db.add(conversation)
    db.commit()
    return project, conversation


def test_digest_and_percent_encoded_name_validation():
    digest = hashlib.sha256(b"hello").digest()
    assert parse_sha256_digest("sha-256=" + base64.b64encode(digest).decode()) == digest.hex()
    assert decode_artifact_name("hello%20world.txt") == "hello world.txt"
    for invalid in ("sha-256=", "md5=abc", "sha-256=abcd"):
        with pytest.raises(ValueError):
            parse_sha256_digest(invalid)
    for invalid in ("%2e%2e", "a%2Fb", "a%5Cb", "%00", "%FF", "%GG"):
        with pytest.raises(ValueError):
            decode_artifact_name(invalid)


def test_reservation_is_caller_owned_and_digest_bound_replay(monkeypatch):
    monkeypatch.setattr(settings, "artifact_conversation_quota_bytes", 100)
    monkeypatch.setattr(settings, "artifact_project_quota_bytes", 200)
    db = SessionLocal()
    try:
        project, conversation = _conversation(db)
        key = str(uuid.uuid4())
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            32,
            "upload",
            key,
            expected_sha256=hashlib.sha256(b"body").hexdigest(),
            display_name="input.txt",
            media_type="text/plain",
        )
        assert artifact.status == "staging"
        assert db.query(ArtifactQuota).filter(ArtifactQuota.project_id == project.id).count() == 2
        with pytest.raises(HTTPException) as pending:
            reserve_artifact(
                db,
                conversation.id,
                project.id,
                32,
                "upload",
                key,
                expected_sha256=hashlib.sha256(b"body").hexdigest(),
                display_name="input.txt",
                media_type="text/plain",
            )
        assert pending.value.status_code == 409
        mark_artifact_ready(
            db,
            artifact.id,
            ArtifactRef(
                id=artifact.id,
                sha256=hashlib.sha256(b"body").hexdigest(),
                size_bytes=4,
                media_type="text/plain",
            ),
        )
        db.commit()

        replay = reserve_artifact(
            db,
            conversation.id,
            project.id,
            32,
            "upload",
            key,
            expected_sha256=hashlib.sha256(b"body").hexdigest(),
            display_name="input.txt",
            media_type="text/plain",
        )
        assert replay.id == artifact.id
        with pytest.raises(HTTPException) as changed:
            reserve_artifact(
                db,
                conversation.id,
                project.id,
                32,
                "upload",
                key,
                expected_sha256=hashlib.sha256(b"different body").hexdigest(),
                display_name="input.txt",
                media_type="text/plain",
            )
        assert changed.value.status_code == 409
    finally:
        db.close()


def test_quota_reservation_transfers_reserved_to_committed_bytes(monkeypatch):
    monkeypatch.setattr(settings, "artifact_conversation_quota_bytes", 100)
    monkeypatch.setattr(settings, "artifact_project_quota_bytes", 200)
    db = SessionLocal()
    try:
        project, conversation = _conversation(db)
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            32,
            "search_result",
            str(uuid.uuid4()),
            display_name="search-results.json",
            media_type="application/json",
        )
        db.commit()
        observed = ArtifactRef(
            id=artifact.id,
            sha256=hashlib.sha256(b"{}").hexdigest(),
            size_bytes=2,
            media_type="application/json",
        )
        ready = mark_artifact_ready(db, artifact.id, observed)
        db.commit()

        assert ready.status == "ready"
        assert ready.size_bytes == 2
        quotas = db.query(ArtifactQuota).filter(ArtifactQuota.project_id == project.id).all()
        assert sum(quota.reserved_bytes for quota in quotas) == 0
        assert len(quotas) == 2
        assert {quota.committed_bytes for quota in quotas} == {2}
    finally:
        db.close()


def test_active_run_pins_selected_artifact_from_deletion():
    db = SessionLocal()
    try:
        project, conversation = _conversation(db)
        body = b"{}"
        digest = hashlib.sha256(body).hexdigest()
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            32,
            "search_result",
            str(uuid.uuid4()),
            expected_sha256=digest,
            display_name="search-results.json",
            media_type="application/json",
        )
        mark_artifact_ready(
            db,
            artifact.id,
            ArtifactRef(
                id=artifact.id,
                sha256=digest,
                size_bytes=len(body),
                media_type="application/json",
            ),
        )
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            status="running",
            execution_configuration=json.dumps(
                {"selected_artifacts": [{"id": str(artifact.id), "sha256": digest}]}
            ),
        )
        db.add(run)
        db.commit()

        with pytest.raises(HTTPException) as pinned:
            begin_artifact_delete(db, conversation.user_id, artifact.id)

        assert pinned.value.status_code == 409
        assert db.query(type(artifact)).filter_by(id=artifact.id).one().status == "ready"
    finally:
        db.close()


def test_delete_finishes_tombstone_and_releases_both_quota_scopes():
    db = SessionLocal()
    try:
        project, conversation = _conversation(db)
        body = b"{}"
        digest = hashlib.sha256(body).hexdigest()
        artifact = reserve_artifact(
            db,
            conversation.id,
            project.id,
            32,
            "search_result",
            str(uuid.uuid4()),
            expected_sha256=digest,
            display_name="search-results.json",
            media_type="application/json",
        )
        mark_artifact_ready(
            db,
            artifact.id,
            ArtifactRef(
                id=artifact.id,
                sha256=digest,
                size_bytes=len(body),
                media_type="application/json",
            ),
        )
        db.commit()

        tombstone = begin_artifact_delete(db, conversation.user_id, artifact.id)
        db.commit()
        assert tombstone.status == "deleting"
        deleted = finish_artifact_delete(db, artifact.id)
        db.commit()

        quotas = db.query(ArtifactQuota).filter(ArtifactQuota.project_id == project.id).all()
        assert deleted.status == "deleted"
        assert len(quotas) == 2
        assert all(row.committed_bytes == 0 and row.committed_count == 0 for row in quotas)
    finally:
        db.close()


def test_reservation_rejects_conversation_project_mismatch():
    db = SessionLocal()
    try:
        _project, conversation = _conversation(db)
        other_user = User(
            username=f"other_{uuid.uuid4().hex}",
            email=f"other_{uuid.uuid4().hex}@example.test",
            password_hash="hash",
        )
        db.add(other_user)
        db.flush()
        other_project = Project(owner_user_id=other_user.id, name="Other")
        db.add(other_project)
        db.commit()

        with pytest.raises(HTTPException) as error:
            reserve_artifact(
                db,
                conversation.id,
                other_project.id,
                10,
                "upload",
                str(uuid.uuid4()),
                display_name="x.txt",
                media_type="text/plain",
            )
        assert error.value.status_code == 404
    finally:
        db.close()
