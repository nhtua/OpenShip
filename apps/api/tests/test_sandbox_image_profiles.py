"""Per-profile inputs cannot accidentally leak into unrelated identities."""

import json
import shutil
import sys
from pathlib import Path

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
