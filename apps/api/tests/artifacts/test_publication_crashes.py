import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.maintenance import reconcile_artifacts
from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.artifacts.service import mark_artifact_ready, reserve_artifact
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


def _owned_conversation(db):
    suffix = uuid.uuid4().hex
    user = User(username=f"crash_{suffix}", email=f"{suffix}@example.test", password_hash="hash")
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Crash recovery")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Crash")
    db.add(conversation)
    db.commit()
    return project, conversation


def _reserve(db, project, conversation, body):
    artifact = reserve_artifact(
        db,
        conversation.id,
        project.id,
        64,
        "search_result",
        str(uuid.uuid4()),
        expected_sha256=hashlib.sha256(body).hexdigest(),
        display_name="search-results.json",
        media_type="application/json",
    )
    db.commit()
    return artifact


def _expected(artifact_id, body):
    return ArtifactRef(
        id=artifact_id,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        media_type="application/json",
    )


def test_staging_file_after_reservation_crash_is_reclaimed(store):
    db = SessionLocal()
    try:
        project, conversation = _owned_conversation(db)
        body = b"{}"
        artifact = _reserve(db, project, conversation, body)
        staged = store.stage(
            artifact.id,
            (body,),
            _expected(artifact.id, body),
            project_id=project.id,
            conversation_id=conversation.id,
        )
        stored = db.query(Artifact).filter(Artifact.id == artifact.id).one()
        stored.created_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()

        result = reconcile_artifacts(db, store, older_than_seconds=1)
        db.commit()

        assert result.reclaimed >= 1
        assert not (store.root / staged.relative_staging_key).exists()
        assert db.query(Artifact).filter(Artifact.id == artifact.id).one().status == "deleted"
        assert sum(row.reserved_bytes for row in db.query(ArtifactQuota).filter(ArtifactQuota.project_id == project.id)) == 0
    finally:
        db.close()


def test_retention_dry_run_does_not_remove_files_or_release_quota(store):
    db = SessionLocal()
    try:
        project, conversation = _owned_conversation(db)
        body = b"{}"
        artifact = _reserve(db, project, conversation, body)
        staged = store.stage(
            artifact.id,
            (body,),
            _expected(artifact.id, body),
            project_id=project.id,
            conversation_id=conversation.id,
        )
        stored = db.query(Artifact).filter(Artifact.id == artifact.id).one()
        stored.created_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()

        result = reconcile_artifacts(db, store, older_than_seconds=1, dry_run=True)
        db.commit()

        assert result.reclaimed >= 1
        assert (store.root / staged.relative_staging_key).exists()
        assert db.query(Artifact).filter(Artifact.id == artifact.id).one().status == "staging"
        assert sum(
            row.reserved_bytes
            for row in db.query(ArtifactQuota).filter(ArtifactQuota.project_id == project.id)
        ) > 0
    finally:
        db.close()


def test_published_file_before_ready_metadata_crash_is_removed(store):
    db = SessionLocal()
    try:
        project, conversation = _owned_conversation(db)
        body = b"{}"
        artifact = _reserve(db, project, conversation, body)
        staged = store.stage(
            artifact.id,
            (body,),
            _expected(artifact.id, body),
            project_id=project.id,
            conversation_id=conversation.id,
        )
        store.publish_file(staged, artifact.relative_storage_key)
        stored = db.query(Artifact).filter(Artifact.id == artifact.id).one()
        stored.created_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()

        result = reconcile_artifacts(db, store, older_than_seconds=1)
        db.commit()

        assert result.reclaimed >= 1
        assert not (store.root / artifact.relative_storage_key).exists()
        assert db.query(Artifact).filter(Artifact.id == artifact.id).one().status == "deleted"
    finally:
        db.close()


def test_ready_metadata_commits_only_after_durable_file_and_survives_restart(store):
    db = SessionLocal()
    try:
        project, conversation = _owned_conversation(db)
        body = b"{}"
        artifact = _reserve(db, project, conversation, body)
        staged = store.stage(
            artifact.id,
            (body,),
            _expected(artifact.id, body),
            project_id=project.id,
            conversation_id=conversation.id,
        )
        store.publish_file(staged, artifact.relative_storage_key)
        ready = mark_artifact_ready(db, artifact.id, _expected(artifact.id, body))
        db.commit()

        reopened = type(store)(store.root, store.storage_id).open_verified(ready)
        try:
            assert reopened.read() == body
        finally:
            reopened.close()
    finally:
        db.close()


def test_ready_artifact_with_missing_bytes_becomes_unavailable(store):
    db = SessionLocal()
    try:
        project, conversation = _owned_conversation(db)
        body = b"{}"
        artifact = _reserve(db, project, conversation, body)
        staged = store.stage(
            artifact.id,
            (body,),
            _expected(artifact.id, body),
            project_id=project.id,
            conversation_id=conversation.id,
        )
        store.publish_file(staged, artifact.relative_storage_key)
        ready = mark_artifact_ready(db, artifact.id, _expected(artifact.id, body))
        db.commit()
        (store.root / ready.relative_storage_key).unlink()

        result = reconcile_artifacts(db, store)
        db.commit()

        assert result.unavailable >= 1
        assert db.query(Artifact).filter(Artifact.id == artifact.id).one().status == "unavailable"
    finally:
        db.close()
