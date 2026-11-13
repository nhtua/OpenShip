"""Build records, reuse decisions and the public command exit codes."""

import json
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import lock_sandbox_dependencies
import sandbox_images
from sandbox_image_lib.builds import (
    ensure_image,
    inspect_status,
    run_smoke,
    runtime_values,
)
from sandbox_image_lib.catalog import ImageError, resolve_recipe
from sandbox_image_lib.engine import Engine

PLATFORM = "linux/amd64"
ENDPOINT = "unix:///run/fixtures/openship.sock"


def _engine():
    return Engine("docker", ENDPOINT, PLATFORM)


def test_runtime_assignments_describe_the_resolved_recipe():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sandbox = root / "deploy/sandbox"
        sandbox.mkdir(parents=True)
        (sandbox / "images.json").write_text(
            json.dumps(
                {
                    "managed_base": {
                        "dockerfile": "deploy/sandbox/Dockerfile.base",
                        "pins": "deploy/sandbox/pins.json",
                        "build_requirements_in": "deploy/sandbox/build-requirements.in",
                        "build_requirements_lock": "deploy/sandbox/build-requirements.lock",
                        "install_helper": "deploy/sandbox/install_pinned.py",
                    },
                    "managed_profiles": {
                        "logs-data": {
                            "dockerfile": "deploy/sandbox/profiles/logs-data/Dockerfile",
                            "requirements_in": "deploy/sandbox/profiles/logs-data/requirements.in",
                            "requirements_lock": "deploy/sandbox/profiles/logs-data/requirements.lock",
                            "os_packages_lock": "deploy/sandbox/profiles/logs-data/os-packages.lock",
                            "smoke": "deploy/sandbox/profiles/logs-data/smoke.py",
                            "install_helper": "deploy/sandbox/profiles/logs-data/install.sh",
                        }
                    },
                }
            )
        )
        (sandbox / "pins.json").write_text(json.dumps({"supported_platforms": [PLATFORM]}))
        for rel in (
            "Dockerfile.base",
            "install_pinned.py",
            "build-requirements.in",
            "build-requirements.lock",
            "profiles/logs-data/Dockerfile",
            "profiles/logs-data/requirements.in",
            "profiles/logs-data/requirements.lock",
            "profiles/logs-data/os-packages.lock",
            "profiles/logs-data/smoke.py",
            "profiles/logs-data/install.sh",
        ):
            path = sandbox / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("body\n")
        recipe = resolve_recipe(root, "logs-data", PLATFORM)
        assert recipe.image_tag == f"openship-sandbox-logs-data:{recipe.fingerprint}"
        values = runtime_values(recipe)
        assert values["SANDBOX_PROFILE_ID"] == "logs-data"
        assert values["SANDBOX_DEPENDENCY_IDENTITY"] == f"sha256:{recipe.fingerprint}"
        assert values["SANDBOX_IMAGE"] == recipe.image_tag


def test_failed_build_never_commits_a_record(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_BUILD_RC", "2")
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    with pytest.raises(ImageError):
        ensure_image(catalog["root"], recipe, _engine())
    assert not list((catalog["root"] / ".openship").rglob("logs-data.json"))


def test_build_passes_the_debian_snapshot_declared_in_pins(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "standard", PLATFORM)
    ensure_image(catalog["root"], recipe, _engine())
    assert "--build-arg DEBIAN_SNAPSHOT=2026-11-01T00:00:00Z" in catalog["log"].read_text()


def test_concurrent_same_profile_builds_leave_one_valid_record(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    results = []
    errors = []

    def build_once():
        try:
            results.append(ensure_image(catalog["root"], recipe, _engine()))
        except ImageError as exc:
            errors.append(exc)

    first = threading.Thread(target=build_once)
    second = threading.Thread(target=build_once)
    first.start()
    second.start()
    first.join()
    second.join()
    assert not errors
    assert len(results) == 2
    records = list((catalog["root"] / ".openship").rglob("logs-data.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["fingerprint"] == recipe.fingerprint
    assert record["image_id"] == "IMAGEID-FROMCANNED"
    assert record["profile_id"] == "logs-data"
    assert record["image_tag"] == recipe.image_tag
    assert record["dependency_identity"] == f"sha256:{recipe.fingerprint}"


def test_smoke_failure_does_not_become_passed_evidence(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    built = ensure_image(catalog["root"], recipe, _engine())
    assert built["status"] in ("built", "matching")
    monkeypatch.setenv("FAKE_RUN_RC", "1")
    outcome = run_smoke(catalog["root"], recipe, _engine())
    assert outcome["status"] == "failed"
    status = inspect_status(catalog["root"], recipe, _engine())
    assert status["smoke"] == "failed"
    assert status["status"] == "matching"


def test_stale_when_tag_now_points_at_another_image_id(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    ensure_image(catalog["root"], recipe, _engine())
    monkeypatch.setenv("FAKE_INSPECT_JSON", '[{"Id": "DIFFERENT-ID", "Machine": "x86_64"}]')
    status = inspect_status(catalog["root"], recipe, _engine())
    assert status["status"] == "stale"


def test_list_build_smoke_exit_codes_and_no_implicit_builds(tmp_path, monkeypatch, capsys):
    import sandbox_images

    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "list"]) == 0
    listed = capsys.readouterr().out
    assert "logs-data" in listed and "standard" in listed
    assert "missing" in listed
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "smoke", "logs-data"]) == 1
    log_text = catalog["log"].read_text()
    assert "build" not in [part for line in log_text.splitlines() for part in line.split()]
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "nope"]) == 2
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "logs-data"]) == 0
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "smoke", "logs-data"]) == 0


def test_build_accepts_the_overwrite_flag_and_rejects_other_build_shapes(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    monkeypatch.delenv("SANDBOX_PROFILES_FILE", raising=False)
    monkeypatch.setenv(
        "FAKE_INSPECT_JSON",
        json.dumps([{"Id": "sha256:" + "a" * 64, "Machine": "x86_64"}]),
    )
    for invalid in (
        ["build"],
        ["build", "--overwrite-profiles"],
        ["build", "logs-data", "extra"],
        ["build", "--all", "--overwrite-profiles", "extra"],
        ["build", "--overwrite-profiles", "logs-data"],
    ):
        assert sandbox_images.main(["--root", str(root), "--engine", "docker", *invalid]) == 2
    assert sandbox_images.main(
        ["--root", str(root), "--engine", "docker", "build", "logs-data", "--overwrite-profiles"]
    ) == 0
    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    assert destination.is_file()


def test_build_context_omits_secret_and_rendered_config_sentinels(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    (root / ".env").write_text("JWT_SECRET=test-only\n")
    secrets = root / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "openship-sandbox-key").write_text("test-only\n")
    (root / "config").mkdir()
    (root / "config" / "provider.local.toml").write_text("rendered\n")
    (root / ".openship").mkdir()
    (root / ".openship" / "state.json").write_text("{}\n")

    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "build", "standard"]) == 0
    lines = catalog["log"].read_text().splitlines()
    staged = [line for line in lines if line and not line.startswith(("docker ", "podman "))]
    images = catalog["images"]
    base = images["managed_base"]
    entry = images["managed_profiles"]["standard"]
    declared = [
        base[key]
        for key in ("dockerfile", "pins", "build_requirements_in", "build_requirements_lock", "install_helper")
    ]
    declared += [
        entry[key]
        for key in ("dockerfile", "requirements_in", "requirements_lock", "os_packages_lock", "smoke", "install_helper")
    ]
    declared += entry["extra_inputs"]
    assert sorted(staged) == sorted(declared)
    for line in lines:
        if line.startswith("docker ") and " build " in f" {line} ":
            assert not line.split()[-1].startswith(str(root))


def test_build_does_not_regenerate_locks(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    sandbox = root / "deploy/sandbox"

    assert lock_sandbox_dependencies.main(["--root", str(root), "nope"]) == 2

    standard_lock = sandbox / "profiles/standard/requirements.lock"
    logs_lock = sandbox / "profiles/logs-data/requirements.lock"
    generated = standard_lock.read_text()
    assert logs_lock.read_bytes() == b"# generated lock for logs-data\n"

    recipe = resolve_recipe(root, "standard", PLATFORM)
    ensure_image(root, recipe, _engine())
    assert standard_lock.read_text() == generated


def test_smoke_evidence_must_bind_current_image_and_recipe(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    ensure_image(catalog["root"], recipe, _engine())
    path = next((catalog["root"] / ".openship").rglob("logs-data.json"))
    record = json.loads(path.read_text())
    record["smoke"]["image_id"] = "an-older-image"
    path.write_text(json.dumps(record))
    assert inspect_status(catalog["root"], recipe, _engine())["smoke"] is None


def test_retagged_image_is_stale_even_when_the_recipe_is_unchanged(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "logs-data", PLATFORM)
    ensure_image(catalog["root"], recipe, _engine())
    path = next((catalog["root"] / ".openship").rglob("logs-data.json"))
    record = json.loads(path.read_text())
    record["image_id"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(record))
    status = inspect_status(catalog["root"], recipe, _engine())
    assert status["status"] == "stale"
    assert status["smoke"] is None


def test_symlinked_state_ancestor_is_never_written(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / ".openship").symlink_to(elsewhere, target_is_directory=True)
    recipe = resolve_recipe(root, "logs-data", PLATFORM)
    with pytest.raises(ImageError):
        ensure_image(root, recipe, _engine())
    assert not list(elsewhere.iterdir())
