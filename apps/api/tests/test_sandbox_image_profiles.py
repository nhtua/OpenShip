"""Per-profile inputs cannot accidentally leak into unrelated identities."""

import hashlib
import io
import json
import runpy
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from sandbox_image_lib.catalog import resolve_recipe


def test_iac_profile_uses_tflint_without_consuming_node(tmp_path):
    shutil.copytree(ROOT / "deploy/sandbox", tmp_path / "deploy/sandbox")
    recipe = resolve_recipe(tmp_path, "iac-validation", "linux/amd64")
    manifest = tmp_path / "deploy/sandbox/tools.lock.json"
    tools = json.loads(manifest.read_text())
    tools["tools"]["node"]["linux/amd64"]["version"] = "fixture-unrelated"
    manifest.write_text(json.dumps(tools))
    assert resolve_recipe(tmp_path, "iac-validation", "linux/amd64").fingerprint == recipe.fingerprint
    tools["tools"]["tflint"]["linux/amd64"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(tools))
    assert resolve_recipe(tmp_path, "iac-validation", "linux/amd64").fingerprint != recipe.fingerprint


def test_inventory_contains_exactly_four_buildable_profiles():
    images = json.loads((ROOT / "deploy/sandbox/images.json").read_text())
    assert set(images["managed_profiles"]) == {"standard", "logs-data", "iac-validation", "devops-shell"}
    for name in images["managed_profiles"]:
        assert resolve_recipe(ROOT, name, "linux/amd64").profile.id == name


def test_corrupt_devops_download_is_rejected_before_writing_binary(tmp_path):
    module = runpy.run_path(str(ROOT / "deploy/sandbox/profiles/devops-shell/install.py"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("terraform", b"synthetic verified binary")
    source = tmp_path / "fixture.zip"
    source.write_bytes(buf.getvalue())
    target = tmp_path / "terraform"
    with pytest.raises(ValueError, match="checksum mismatch"):
        module["install_zip_binary"](source.as_uri(), "0" * 64, target, "terraform")
    assert not target.exists()
    module["install_zip_binary"](
        source.as_uri(), hashlib.sha256(source.read_bytes()).hexdigest(), target, "terraform",
    )
    assert target.read_bytes() == b"synthetic verified binary"
    assert target.stat().st_mode & 0o111
