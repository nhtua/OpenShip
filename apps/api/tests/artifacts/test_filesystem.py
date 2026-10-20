import hashlib
import os
import stat
import uuid
from types import SimpleNamespace

import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore


def _expected(artifact_id, body, media_type="text/plain"):
    return ArtifactRef(
        id=artifact_id,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        media_type=media_type,
    )


def _relative_key(project_id, conversation_id, artifact_id):
    return f"projects/{project_id}/conversations/{conversation_id}/artifacts/{artifact_id}/content"


def test_streamed_eight_megabyte_artifact_is_durable_and_verified(store):
    artifact_id = uuid.uuid4()
    project_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    body = b"x" * (8 * 1024 * 1024)
    staged = store.stage(artifact_id, (body[i : i + 65536] for i in range(0, len(body), 65536)), _expected(artifact_id, body))
    key = _relative_key(project_id, conversation_id, artifact_id)
    store.publish_file(staged, key)

    verified = store.open_verified(
        SimpleNamespace(relative_storage_key=key, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    )
    try:
        assert verified.read() == body
    finally:
        verified.close()

    restarted = LocalArtifactStore(store.root, store.storage_id)
    restarted.verify()
    assert (store.root / key).stat().st_size == len(body)


@pytest.mark.parametrize(
    "body,media_type",
    [
        (b"\xff", "text/plain"),
        (b"{\"a\":1,\"a\":2}", "application/json"),
        (b"{\"a\":NaN}", "application/json"),
        (b"{\"a\":\"\x00\"}", "application/json"),
        ((b"[" * 33) + b"0" + (b"]" * 33), "application/json"),
    ],
)
def test_staging_rejects_invalid_text_and_json(body, media_type, store):
    artifact_id = uuid.uuid4()
    with pytest.raises(ArtifactStorageError):
        store.stage(artifact_id, (body,), _expected(artifact_id, body, media_type))


def test_staging_rejects_mismatched_digest_or_artifact_id(store):
    artifact_id = uuid.uuid4()
    with pytest.raises(ArtifactStorageError):
        store.stage(artifact_id, (b"actual",), _expected(artifact_id, b"claimed"))
    with pytest.raises(ArtifactStorageError):
        store.stage(uuid.uuid4(), (b"actual",), _expected(artifact_id, b"actual"))


def test_storage_marker_must_match_and_missing_root_is_not_recreated(store):
    with pytest.raises(ArtifactStorageError):
        LocalArtifactStore(store.root, uuid.uuid4()).verify()

    moved = store.root.with_name("moved-artifacts")
    store.root.rename(moved)
    with pytest.raises(ArtifactStorageError):
        store.verify()
    assert not store.root.exists()


def test_initialize_refuses_to_claim_a_nonempty_unmarked_root(tmp_path):
    root = tmp_path / "replacement"
    root.mkdir()
    (root / "unrelated-file").write_text("keep")
    store = LocalArtifactStore(root, uuid.uuid4())
    with pytest.raises(ArtifactStorageError):
        store.initialize()
    assert (root / "unrelated-file").read_text() == "keep"


def test_final_paths_reject_traversal_and_symlink_parent(store, tmp_path):
    artifact_id = uuid.uuid4()
    body = b"safe"
    staged = store.stage(artifact_id, (body,), _expected(artifact_id, body))
    with pytest.raises(ArtifactStorageError):
        store.publish_file(staged, "../escape/content")

    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "projects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactStorageError):
        store.publish_file(staged, _relative_key(uuid.uuid4(), uuid.uuid4(), artifact_id))


def test_verified_open_rejects_symlink_hardlink_and_fifo(store, tmp_path):
    project_id, conversation_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    body = b"retained"
    key = _relative_key(project_id, conversation_id, artifact_id)
    staged = store.stage(artifact_id, (body,), _expected(artifact_id, body))
    store.publish_file(staged, key)
    record = SimpleNamespace(relative_storage_key=key, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    path = store.root / key

    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(ArtifactStorageError):
        store.open_verified(record)
    path.unlink()

    path.write_bytes(body)
    outside_link = tmp_path / "hardlink"
    os.link(path, outside_link)
    with pytest.raises(ArtifactStorageError):
        store.open_verified(record)
    outside_link.unlink()
    path.unlink()

    if hasattr(os, "mkfifo"):
        os.mkfifo(path)
        assert stat.S_ISFIFO(path.lstat().st_mode)
        with pytest.raises(ArtifactStorageError):
            store.open_verified(record)


def test_open_descriptor_survives_path_swap_after_verification(store):
    project_id, conversation_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    body = b"original bytes"
    key = _relative_key(project_id, conversation_id, artifact_id)
    staged = store.stage(artifact_id, (body,), _expected(artifact_id, body))
    store.publish_file(staged, key)
    record = SimpleNamespace(relative_storage_key=key, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())

    verified = store.open_verified(record)
    path = store.root / key
    path.rename(path.with_name("replaced-content"))
    path.write_bytes(b"attacker replacement")
    try:
        assert verified.read() == body
    finally:
        verified.close()


def test_removal_treats_missing_parent_as_already_absent(store):
    record = SimpleNamespace(
        relative_storage_key=_relative_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    assert store.remove(record) is False
    assert store.remove_unpublished(record.relative_storage_key) is False
    assert store.remove_staging(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()) is False
