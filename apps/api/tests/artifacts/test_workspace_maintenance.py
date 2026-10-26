"""Explicit workspace purge and coordinated artifact restore behavior."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.maintenance import reconcile_artifacts
from src.openship.artifacts.models import Artifact, ArtifactQuota
from src.openship.artifacts.service import mark_artifact_ready, reserve_artifact
from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.models import Base
from src.openship.messaging.models import MessageTransportState
from src.openship.runs.models import Outbox, Run
from src.openship.workspace.models import ConversationWorkspace, Project
from src.openship.workspace.service import ensure_workspace


@contextmanager
def _isolated_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            db.add(
                MessageTransportState(
                    id=1,
                    installation_id=uuid.uuid4(),
                    transport_epoch=uuid.uuid4(),
                    active=False,
                )
            )
            db.commit()
        with factory() as db:
            yield db
    finally:
        engine.dispose()


def _conversation(db):
    suffix = uuid.uuid4().hex
    user = User(
        username=f"purge_{suffix[:10]}",
        email=f"purge_{suffix}@example.test",
        password_hash="fixture-hash",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name="Retention fixture")
    db.add(project)
    db.flush()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="Files")
    db.add(conversation)
    db.flush()
    return user, project, conversation


def _saved_artifact(db, store, project, conversation, body: bytes, name="saved.txt"):
    digest = hashlib.sha256(body).hexdigest()
    artifact = reserve_artifact(
        db,
        conversation.id,
        project.id,
        len(body),
        "upload",
        str(uuid.uuid4()),
        expected_sha256=digest,
        display_name=name,
        media_type="text/plain",
    )
    ref = ArtifactRef(
        id=artifact.id,
        sha256=digest,
        size_bytes=len(body),
        media_type="text/plain",
    )
    staged = store.stage(
        artifact.id,
        [body],
        ref,
        project_id=project.id,
        conversation_id=conversation.id,
    )
    store.publish_file(staged, artifact.relative_storage_key)
    mark_artifact_ready(db, artifact.id, ref)
    db.flush()
    return artifact


def test_purge_plan_is_non_mutating_and_purge_keeps_digest_tombstone(tmp_path):
    from src.openship.workspace.maintenance import plan_workspace_purge, purge_workspace

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "purge-store", uuid.uuid4())
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        artifact = _saved_artifact(db, store, project, conversation, b"keep these bytes")
        db.commit()
        digest = artifact.sha256
        key = artifact.relative_storage_key

        plan = plan_workspace_purge(db, workspace.id)
        assert plan.storage_id == str(store.storage_id)
        assert [item.artifact_id for item in plan.items] == [str(artifact.id)]
        preview = purge_workspace(db, store, plan, dry_run=True)
        db.expire_all()
        assert preview.dry_run is True
        assert preview.deleted == 0
        assert db.get(Artifact, artifact.id).status == "ready"
        assert (store.root / key).read_bytes() == b"keep these bytes"
        assert db.get(ConversationWorkspace, workspace.id).status == "available"

        report = purge_workspace(db, store, plan)
        db.expire_all()
        tombstone = db.get(Artifact, artifact.id)
        quotas = db.query(ArtifactQuota).filter_by(project_id=project.id).all()
        assert report.deleted == report.examined == 1
        assert report.failed == 0
        assert tombstone.status == "deleted"
        assert tombstone.sha256 == digest
        assert tombstone.id == artifact.id
        assert not (store.root / key).exists()
        assert all(row.committed_count == row.committed_bytes == 0 for row in quotas)
        assert db.get(ConversationWorkspace, workspace.id).status == "deleted"
        assert db.query(Outbox).filter_by(type="artifact.deleted").count() == 1


def test_workspace_purge_refuses_an_active_run_input_pin(tmp_path):
    from src.openship.workspace.maintenance import plan_workspace_purge

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "pinned-store", uuid.uuid4())
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        artifact = _saved_artifact(db, store, project, conversation, b"pinned")
        db.add(
            Run(
                conversation_id=conversation.id,
                project_id=project.id,
                status="running",
                execution_configuration=json.dumps(
                    {"selected_artifacts": [{"id": str(artifact.id), "sha256": artifact.sha256}]}
                ),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as caught:
            plan_workspace_purge(db, workspace.id)
        assert caught.value.status_code == 409
        assert caught.value.detail["error"]["code"] == "artifact_is_pinned"
        assert db.get(Artifact, artifact.id).status == "ready"


def test_purge_interruption_leaves_recoverable_tombstone(tmp_path, monkeypatch):
    from src.openship.workspace.maintenance import plan_workspace_purge, purge_workspace

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "interrupted-store", uuid.uuid4())
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        artifact = _saved_artifact(db, store, project, conversation, b"interrupted")
        db.commit()
        plan = plan_workspace_purge(db, workspace.id)

        def fail_remove(_artifact):
            raise ArtifactStorageError("simulated unlink interruption")

        monkeypatch.setattr(store, "remove", fail_remove)
        report = purge_workspace(db, store, plan)
        db.expire_all()
        assert report.failed == 1
        assert db.get(Artifact, artifact.id).status == "deleting"
        assert (store.root / artifact.relative_storage_key).exists()

        monkeypatch.undo()
        recovered = reconcile_artifacts(db, store)
        db.expire_all()
        assert recovered.deleted == 1
        assert db.get(Artifact, artifact.id).status == "deleted"
        assert db.get(ConversationWorkspace, workspace.id).status == "deleted"


def test_restore_verifies_existing_bytes_and_marks_missing_or_changed_files_unavailable(tmp_path):
    from src.openship.workspace.maintenance import verify_restored_artifacts

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        original = LocalArtifactStore(tmp_path / "original", uuid.uuid4())
        original.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, original)
        good = _saved_artifact(db, original, project, conversation, b"restored bytes", "good.txt")
        missing = _saved_artifact(db, original, project, conversation, b"missing", "missing.txt")
        changed = _saved_artifact(db, original, project, conversation, b"original", "changed.txt")
        ids = (good.id, missing.id, changed.id)
        key_good, key_changed = good.relative_storage_key, changed.relative_storage_key
        original_id = original.storage_id
        db.commit()

        restored = LocalArtifactStore(tmp_path / "restored", original_id)
        restored.initialize()
        for key in (key_good, key_changed):
            target = restored.root / key
            target.parent.mkdir(parents=True)
            source = original.root / key
            shutil.copyfile(source, target)
        (restored.root / key_changed).write_bytes(b"changed")
        original_file = original.root / missing.relative_storage_key
        original_file.unlink()

        report = verify_restored_artifacts(db, restored)
        db.expire_all()
        statuses = {item.id: item.status for item in db.query(Artifact).filter(Artifact.id.in_(ids))}
        assert report.storage_available is True
        assert report.verified == 1
        assert report.unavailable == 2
        assert db.get(ConversationWorkspace, workspace.id).status == "available"
        assert statuses == {good.id: "ready", missing.id: "unavailable", changed.id: "unavailable"}
        # The verifier reports the problem; it does not initialize, replace, or rewrite files.
        assert (restored.root / key_good).read_bytes() == b"restored bytes"
        assert (restored.root / key_changed).read_bytes() == b"changed"


def test_restore_with_wrong_storage_identity_fails_closed_without_rebinding(tmp_path):
    from src.openship.workspace.maintenance import verify_restored_artifacts

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        bound_store = LocalArtifactStore(tmp_path / "bound", uuid.uuid4())
        bound_store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, bound_store)
        artifact = _saved_artifact(db, bound_store, project, conversation, b"bound")
        bound_id = workspace.storage_id
        db.commit()

        other_store = LocalArtifactStore(tmp_path / "other", uuid.uuid4())
        other_store.initialize()
        report = verify_restored_artifacts(db, other_store)
        db.expire_all()

        assert report.storage_available is True
        assert report.verified == 0
        assert report.unavailable == 1
        assert db.get(ConversationWorkspace, workspace.id).storage_id == bound_id
        assert db.get(ConversationWorkspace, workspace.id).status == "unavailable"
        assert db.get(Artifact, artifact.id).status == "unavailable"


def test_restore_missing_root_does_not_create_or_rebind_storage(tmp_path):
    from src.openship.workspace.maintenance import verify_restored_artifacts

    with _isolated_session() as db:
        user, project, conversation = _conversation(db)
        store = LocalArtifactStore(tmp_path / "present", uuid.uuid4())
        store.initialize()
        workspace = ensure_workspace(db, user.id, conversation.id, store)
        artifact = _saved_artifact(db, store, project, conversation, b"gone")
        storage_id = workspace.storage_id
        db.commit()
        missing_store = LocalArtifactStore(tmp_path / "absent", storage_id)

        report = verify_restored_artifacts(db, missing_store)
        db.expire_all()
        assert report.storage_available is False
        assert report.unavailable == 1
        assert not missing_store.root.exists()
        assert db.get(ConversationWorkspace, workspace.id).storage_id == storage_id
        assert db.get(ConversationWorkspace, workspace.id).status == "unavailable"
        assert db.get(Artifact, artifact.id).status == "unavailable"
