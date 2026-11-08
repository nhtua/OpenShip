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
                    },
                    "managed_profiles": {
                        "logs-data": {
                            "dockerfile": "deploy/sandbox/profiles/logs-data/Dockerfile",
                            "requirements_in": "deploy/sandbox/profiles/logs-data/requirements.in",
                            "requirements_lock": "deploy/sandbox/profiles/logs-data/requirements.lock",
                            "os_packages_lock": "deploy/sandbox/profiles/logs-data/os-packages.lock",
                            "smoke": "deploy/sandbox/profiles/logs-data/smoke.py",
                        }
                    },
                }
            )
        )
        (sandbox / "pins.json").write_text(json.dumps({"supported_platforms": [PLATFORM]}))
        for rel in (
            "Dockerfile.base",
            "build-requirements.in",
            "build-requirements.lock",
            "profiles/logs-data/Dockerfile",
            "profiles/logs-data/requirements.in",
            "profiles/logs-data/requirements.lock",
            "profiles/logs-data/os-packages.lock",
            "profiles/logs-data/smoke.py",
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
