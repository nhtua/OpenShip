"""Recipe fingerprints are deterministic over exactly the declared inputs."""

import json
import sys
from pathlib import Path

import pytest

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from sandbox_image_lib.catalog import ImageError, load_profile, resolve_recipe

PLATFORM = "linux/amd64"


def test_recipe_changes_for_base_lock_helper_smoke_and_platform_inputs(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    base = resolve_recipe(root, "logs-data", PLATFORM)

    def mutate(rel: str, body: str):
        path = root / rel
        original = path.read_bytes()
        path.write_text(body)
        try:
            return resolve_recipe(root, "logs-data", PLATFORM)
        finally:
            path.write_bytes(original)

    changed = mutate("deploy/sandbox/Dockerfile.base", "FROM changed base\n")
    assert changed.fingerprint != base.fingerprint
    assert changed.base_fingerprint != base.base_fingerprint
    assert (
        mutate(
            "deploy/sandbox/pins.json",
            json.dumps({"python_base_image": "x", "supported_platforms": [PLATFORM]}),
        ).base_fingerprint
        != base.base_fingerprint
    )
    assert (
        mutate(
            "deploy/sandbox/profiles/logs-data/requirements.lock",
            "# regenerated lock\n",
        ).fingerprint
        != base.fingerprint
    )
    assert (
        mutate("deploy/sandbox/profiles/logs-data/smoke.py", "# changed smoke\n").fingerprint
        != base.fingerprint
    )
    entry = catalog["images"]["managed_profiles"]["logs-data"]
    assert entry["extra_inputs"] == []
    helper = root / "deploy/sandbox/profiles/logs-data/helper.sh"
    helper.write_text("# install helper\n")
    original_inputs = list(entry["extra_inputs"])
    entry["extra_inputs"] = ["deploy/sandbox/profiles/logs-data/helper.sh"]
    (root / "deploy/sandbox/images.json").write_text(json.dumps(catalog["images"], indent=2))
    try:
        with_helper = resolve_recipe(root, "logs-data", PLATFORM)
    finally:
        entry["extra_inputs"] = original_inputs
        (root / "deploy/sandbox/images.json").write_text(json.dumps(catalog["images"], indent=2))
    assert with_helper.fingerprint != base.fingerprint
    assert with_helper.base_fingerprint == base.base_fingerprint
    with pytest.raises(ImageError):
        resolve_recipe(root, "logs-data", "linux/arm64")


def test_recipe_ignores_unrelated_repo_files(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    base = resolve_recipe(root, "logs-data", PLATFORM)
    (root / "notes.md").write_text("unrelated\n")
    (root / "deploy/sandbox/profiles/logs-data/stray.txt").write_text("undeclared\n")
    secrets = root / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "openship-sandbox-key").write_text("not-a-secret\n")
    assert resolve_recipe(root, "logs-data", PLATFORM).fingerprint == base.fingerprint


def test_unknown_profile_and_symlink_escape_fail_before_engine_calls(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    with pytest.raises(ImageError):
        resolve_recipe(root, "logs-dat", PLATFORM)

    entry = catalog["images"]["managed_profiles"]["logs-data"]
    entry["extra_inputs"] = ["deploy/sandbox/profiles/logs-data/escape"]
    (root / "deploy/sandbox/images.json").write_text(json.dumps(catalog["images"], indent=2))
    leak = root / "secrets"
    leak.mkdir(parents=True, exist_ok=True)
    (leak / "leaked").write_text("credential material\n")
    link = root / "deploy/sandbox/profiles/logs-data/escape"
    link.symlink_to(leak / "leaked")
    with pytest.raises(ImageError):
        resolve_recipe(root, "logs-data", PLATFORM)
    assert not catalog["log"].exists(), "no engine call may precede rejection"


def test_shared_tool_manifest_projects_per_profile(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    logs_before = resolve_recipe(root, "logs-data", PLATFORM)
    standard_before = resolve_recipe(root, "standard", PLATFORM)
    tools_path = root / "deploy/sandbox/tools.lock.json"
    tools = json.loads(tools_path.read_text())
    tools["tools"]["node"]["linux/amd64"]["version"] = "21.0.0"
    tools_path.write_text(json.dumps(tools, indent=2))
    assert resolve_recipe(root, "logs-data", PLATFORM).fingerprint == logs_before.fingerprint
    assert resolve_recipe(root, "standard", PLATFORM).fingerprint != standard_before.fingerprint
    tools["tools"]["unused-tool"] = {"linux/amd64": {"version": "1", "url": "u", "sha256": "d"}}
    tools_path.write_text(json.dumps(tools, indent=2))
    assert resolve_recipe(root, "logs-data", PLATFORM).fingerprint == logs_before.fingerprint


def test_profile_spec_matches_inventory_and_keeps_legacy_keys(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    spec = load_profile(root, "standard")
    assert spec.id == "standard"
    assert spec.dockerfile == (root / "deploy/sandbox/profiles/standard/Dockerfile")
    assert spec.smoke == (root / "deploy/sandbox/profiles/standard/smoke.py")
    assert spec.extra_inputs == ()
    assert catalog["images"]["managed_profiles"]["standard"]["tools"] == ["node"]
    assert catalog["images"]["workload_dockerfile"] == "deploy/sandbox/Dockerfile.python"
