import hashlib
import os

import pytest

from src.openship.sandboxes.collector import CollectionError, collect_outputs


def test_collects_declared_regular_files_with_digests(tmp_path):
    (tmp_path / "report.txt").write_bytes(b"result")
    result = collect_outputs(tmp_path, ["report.txt"])
    assert result["report.txt"].content == b"result"
    assert result["report.txt"].sha256 == hashlib.sha256(b"result").hexdigest()


@pytest.mark.parametrize("name", ["../escape", "/tmp/x", ".", "a/b", "a\\b", ""])
def test_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(CollectionError):
        collect_outputs(tmp_path, [name])


def test_rejects_links_and_special_files(tmp_path):
    (tmp_path / "target").write_bytes(b"secret")
    (tmp_path / "symlink").symlink_to("target")
    os.link(tmp_path / "target", tmp_path / "hardlink")
    os.mkfifo(tmp_path / "fifo")
    for name in ("symlink", "hardlink", "fifo"):
        with pytest.raises(CollectionError):
            collect_outputs(tmp_path, [name])


def test_rejects_duplicate_and_excessive_count(tmp_path):
    with pytest.raises(CollectionError):
        collect_outputs(tmp_path, ["a", "a"])
    with pytest.raises(CollectionError):
        collect_outputs(tmp_path, [f"{i}.txt" for i in range(9)])


def test_rejects_files_over_limit(tmp_path):
    (tmp_path / "huge").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(CollectionError):
        collect_outputs(tmp_path, ["huge"])


def test_rejects_output_directory_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (target / "a").write_bytes(b"ok")
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(CollectionError):
        collect_outputs(tmp_path / "link", ["a"])
