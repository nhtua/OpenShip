"""Authorized build writes preserve prior state and publish only verified profiles."""

import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import sandbox_images
from sandbox_image_lib import profiles
from sandbox_image_lib.catalog import ImageError, load_metadata, resolve_recipe
from sandbox_image_lib.config import resolve_profiles_source

from src.openship.capabilities.contracts import canonical_digest
from src.openship.sandboxes.profile_schema import decode_catalog, encode_catalog

PLATFORM = "linux/amd64"
BUILT_IMAGE_ID = "sha256:" + "a" * 64


def _valid_inspect() -> str:
    return json.dumps([{"Id": BUILT_IMAGE_ID, "Machine": "x86_64"}])


def _entry(root: Path, profile_id: str, *, enabled: bool = True, extensions: dict | None = None) -> dict:
    metadata = load_metadata(root, profile_id)
    fingerprint = resolve_recipe(root, profile_id, PLATFORM).fingerprint
    image_id = "sha256:" + "2" * 64
    return {
        "enabled": enabled,
        "extensions": extensions or {},
        "metadata": metadata,
        "metadata_digest": canonical_digest(metadata),
        "runtime": {
            "image_tag": f"openship-sandbox-{profile_id}:previous",
            "image_id": image_id,
            "dependency_identity": f"sha256:{fingerprint}",
            "platform": PLATFORM,
            "fingerprint": fingerprint,
            "engine_identity": "docker@unix:///run/fixtures/openship.sock@linux/amd64",
            "smoke_image_id": image_id,
            "smoke_fingerprint": fingerprint,
            "smoke_passed": True,
            "smoke_evidence_digest": "3" * 64,
        },
    }


def _catalog_bytes(entries: dict) -> bytes:
    return encode_catalog(decode_catalog(json.dumps({"schema_version": 1, "profiles": entries}).encode()))


def _update(root: Path, profile_id: str) -> dict:
    recipe = resolve_recipe(root, profile_id, PLATFORM)
    image_id = "sha256:" + "4" * 64
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "platform": PLATFORM,
        "engine_identity": "docker@unix:///run/fixtures/openship.sock@linux/amd64",
        "fingerprint": recipe.fingerprint,
        "image_tag": recipe.image_tag,
        "image_id": image_id,
        "dependency_identity": recipe.dependency_identity,
        "built_at": "2026-11-08T00:00:00+00:00",
        "smoke": {
            "status": "passed",
            "image_id": image_id,
            "fingerprint": recipe.fingerprint,
            "at": "2026-11-08T00:00:00+00:00",
        },
        "metadata": load_metadata(root, profile_id),
    }


def _prepare(tmp_path, monkeypatch) -> Path:
    catalog = build_catalog(tmp_path, monkeypatch)
    monkeypatch.delenv("SANDBOX_PROFILES_FILE", raising=False)
    monkeypatch.setenv("FAKE_INSPECT_JSON", _valid_inspect())
    return catalog["root"]


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_flag_creates_or_merges_without_deleting_other_profiles(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    previous = decode_catalog(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": {
                    "standard": _entry(root, "standard"),
                    "logs-data": _entry(root, "logs-data", enabled=False, extensions={"owner": "platform"}),
                },
            }
        ).encode()
    )
    destination.write_bytes(encode_catalog(previous))

    exit_code = sandbox_images.main(
        ["--root", str(root), "--engine", "docker", "build", "logs-data", "--overwrite-profiles"]
    )

    merged = decode_catalog(destination.read_bytes())
    assert exit_code == 0
    assert merged.profiles["standard"] == previous.profiles["standard"]
    assert merged.profiles["logs-data"].enabled is False  # existing operator choice
    assert merged.profiles["logs-data"].extensions == {"owner": "platform"}
    assert merged.profiles["logs-data"].runtime.image_id == BUILT_IMAGE_ID
    assert merged.profiles["logs-data"].metadata == previous.profiles["logs-data"].metadata


def test_decline_and_non_tty_leave_bytes_unchanged(tmp_path, monkeypatch, capsys):
    root = _prepare(tmp_path, monkeypatch)
    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    destination.write_bytes(_catalog_bytes({"standard": _entry(root, "standard")}))
    original = destination.read_bytes()

    monkeypatch.setattr(sys, "stdin", _FakeTTY("n\n"))
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "logs-data"]) == 0
    assert destination.read_bytes() == original
    declined = capsys.readouterr().out
    assert str(destination) in declined and "logs-data" in declined and "declined" in declined

    monkeypatch.setattr(sys, "stdin", _FakeTTY(""))
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "logs-data"]) == 0
    assert destination.read_bytes() == original
    eof = capsys.readouterr().out
    assert str(destination) in eof and "declined" in eof

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "logs-data"]) == 0
    assert destination.read_bytes() == original
    skipped = capsys.readouterr().out
    assert str(destination) in skipped and "not written" in skipped


def test_partial_build_writes_successes_and_returns_failure(tmp_path, monkeypatch, capsys):
    root = _prepare(tmp_path, monkeypatch)
    real_ensure_image = sandbox_images.ensure_image

    def ensure_one_profile(root_arg, recipe, engine):
        if recipe.profile.id == "standard":
            raise ImageError("simulated build failure")
        return real_ensure_image(root_arg, recipe, engine)

    monkeypatch.setattr(sandbox_images, "ensure_image", ensure_one_profile)

    exit_code = sandbox_images.main(
        ["--root", str(root), "--engine", "docker", "build", "--all", "--overwrite-profiles"]
    )

    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    published = decode_catalog(destination.read_bytes())
    assert exit_code == 1 and tuple(sorted(published.profiles)) == ("logs-data",)  # another build failed
    assert "standard" in capsys.readouterr().err

    direct = profiles.merge_profile_updates(
        root, tmp_path / "published.yaml", {"logs-data": _update(root, "logs-data")}
    )
    assert direct == ("logs-data",)


def test_concurrent_writers_reread_under_lock(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    destination = tmp_path / "profiles.yaml"
    errors = []

    def write(profile_id: str) -> None:
        try:
            profiles.merge_profile_updates(root, destination, {profile_id: _update(root, profile_id)})
        except ImageError as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=("logs-data",)),
               threading.Thread(target=write, args=("standard",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    merged = decode_catalog(destination.read_bytes())
    assert tuple(sorted(merged.profiles)) == ("logs-data", "standard")


def test_failed_codec_replace_or_symlink_preserves_old_file(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    destination = tmp_path / "profiles.yaml"
    previous = _catalog_bytes({"standard": _entry(root, "standard")})
    destination.write_bytes(previous)
    update = {"logs-data": _update(root, "logs-data")}

    def fail_codec(*args, **kwargs):
        raise ImageError("codec failed")

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_run_codec", fail_codec)
        with pytest.raises(ImageError):
            profiles.merge_profile_updates(root, destination, update)
    assert destination.read_bytes() == previous

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_atomic_replace", fail_replace)
        with pytest.raises(ImageError):
            profiles.merge_profile_updates(root, destination, update)
    assert destination.read_bytes() == previous
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")]

    target = tmp_path / "target.yaml"
    target.write_bytes(previous)
    destination.unlink()
    destination.symlink_to(target)
    with pytest.raises(ImageError):
        profiles.merge_profile_updates(root, destination, update)
    assert target.read_bytes() == previous


def test_external_destination_validates_all_ancestors_and_sticky_policy(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    update = {"logs-data": _update(root, "logs-data")}

    unsafe = tmp_path / "unsafe"
    safe_inside = unsafe / "safe"
    safe_inside.mkdir(parents=True)
    unsafe.chmod(0o770)
    with pytest.raises(ImageError):
        profiles.merge_profile_updates(root, safe_inside / "profiles.yaml", update)
    assert not (safe_inside / "profiles.yaml").exists()

    linked = tmp_path / "linked"
    linked.symlink_to(safe_inside, target_is_directory=True)
    with pytest.raises(ImageError):
        profiles.merge_profile_updates(root, linked / "profiles.yaml", update)

    sticky = tmp_path / "sticky"
    sticky_inside = sticky / "inside"
    sticky_inside.mkdir(parents=True)
    sticky.chmod(0o1777)
    sticky_inside.chmod(0o700)
    destination = sticky_inside / "profiles.yaml"
    assert profiles.merge_profile_updates(root, destination, update) == ("logs-data",)
    assert decode_catalog(destination.read_bytes()).profiles["logs-data"].runtime.image_id == (
        "sha256:" + "4" * 64
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the ignore check")
def test_custom_in_repo_path_requires_ignore(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    ignore = root / ".gitignore"
    ignore.write_text("*.log\n")
    destination = root / "deploy/sandbox/custom-profiles.yaml"
    update = {"logs-data": _update(root, "logs-data")}

    with pytest.raises(ImageError):
        profiles.merge_profile_updates(root, destination, update)
    assert not destination.exists()

    ignore.write_text(
        "*.log\ndeploy/sandbox/custom-profiles.yaml\n"
        "deploy/sandbox/.custom-profiles.yaml.lock\n"
    )
    assert profiles.merge_profile_updates(root, destination, update) == ("logs-data",)


def test_resolver_prefers_process_env_then_root_dotenv_then_default(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("SANDBOX_PROFILES_FILE='dotenv path.yaml'\n")

    path, explicit = resolve_profiles_source(root, {"SANDBOX_PROFILES_FILE": "'env path.yaml'"})
    assert explicit is True and path == root / "env path.yaml"

    path, explicit = resolve_profiles_source(root, {})
    assert explicit is True and path == root / "dotenv path.yaml"

    (root / ".env").write_text("")
    path, explicit = resolve_profiles_source(root, {})
    assert explicit is False and path == root / "deploy/sandbox/sandbox-profiles.yaml"


def test_resolver_is_relative_to_root_not_the_current_directory(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    path, explicit = resolve_profiles_source(root, {})

    assert explicit is False
    assert path == root / "deploy/sandbox/sandbox-profiles.yaml"


@pytest.mark.parametrize("value", ["", "${HOME}/profiles.yaml", "$PROFILES", "`profiles`", "a\\b.yaml"])
def test_resolver_rejects_empty_and_interpolated_explicit_paths(tmp_path, value):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ImageError):
        resolve_profiles_source(root, {"SANDBOX_PROFILES_FILE": value})
