"""Profile snapshots reject unsafe input and preserve recipe identities."""
import base64
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.openship.capabilities.contracts import canonical_digest
from src.openship.sandboxes.profile_schema import (
    catalog_digest,
    decode_catalog,
    encode_catalog,
)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from sandbox_image_lib.catalog import load_metadata, resolve_recipe

IDS = ("logs-data", "iac-validation", "standard", "devops-shell")


def entry(label="Data"):
    metadata = {"label": label, "description": "Purpose", "use_for": ["charts"],
                "highlights": ["Python"], "limitations": ["offline"]}
    return {"enabled": True, "extensions": {}, "metadata": metadata,
            "metadata_digest": canonical_digest(metadata), "runtime": {
                "image_tag": "image:tag", "image_id": "sha256:" + "1" * 64,
                "dependency_identity": "sha256:" + "2" * 64, "platform": "linux/amd64",
                "fingerprint": "2" * 64, "engine_identity": "test-engine",
                "smoke_image_id": "sha256:" + "1" * 64, "smoke_fingerprint": "2" * 64,
                "smoke_passed": True, "smoke_evidence_digest": "3" * 64}}


def doc(profiles):
    return json.dumps({"schema_version": 1, "profiles": profiles}).encode()


def bridge(envelope):
    return subprocess.run([sys.executable, "-m", "src.openship.sandboxes.profile_codec"],
                          input=envelope, capture_output=True, timeout=10, check=False)


def test_empty_catalog_and_four_profiles_round_trip():
    assert decode_catalog(b"schema_version: 1\nprofiles: {}\n").profiles == {}
    original = decode_catalog(doc({key: entry(key) for key in IDS}))
    assert decode_catalog(encode_catalog(original)) == original
    process = bridge(json.dumps({"action": "decode", "yaml_base64": base64.b64encode(encode_catalog(original)).decode()}).encode())
    assert process.returncode == 0, process.stderr
    response = json.loads(process.stdout)
    assert response["catalog"] == original.model_dump(mode="json")
    process = bridge(json.dumps({"action": "encode", "catalog": response["catalog"]}).encode())
    assert process.returncode == 0, process.stderr
    assert decode_catalog(base64.b64decode(json.loads(process.stdout)["yaml_base64"])) == original


@pytest.mark.parametrize("bad", [
    b"schema_version: 1\nschema_version: 1\nprofiles: {}\n",
    b"schema_version: !!int 1\nprofiles: {}\n",
    b"schema_version: 1\nprofiles: &p {}\nother: *p\n",
    b"schema_version: 1\nprofiles: {<<: {}}\n",
    b"schema_version: 1\nprofiles: {}\n" + b"#" * (256 * 1024),
    b"schema_version: 1\nprofiles: {}\n---\nschema_version: 1\nprofiles: {}\n",
    b"schema_version: 1\nprofiles: {1: {}}\n",
    b"schema_version: 1\nprofiles: {}\nx: .nan\n",
    b"schema_version: 1\nprofiles: {}\nx: 2026-11-08\n",
    b"schema_version: 1\nprofiles: {}\nx: " + b"[" * 17 + b"0" + b"]" * 17,
    b"schema_version: 1\nprofiles: {}\nx: [" + b"0," * 4097 + b"]\n",
], ids=["duplicate", "tag", "alias", "merge", "oversize", "documents", "key-type",
        "nonfinite", "timestamp", "depth", "scalars"])
def test_duplicate_keys_tags_aliases_merge_and_oversize_fail(bad):
    with pytest.raises(ValueError):
        decode_catalog(bad)
    codec_process = bridge(json.dumps({"action": "decode", "yaml_base64": base64.b64encode(bad).decode()}).encode())
    assert codec_process.returncode != 0


def test_metadata_only_change_does_not_change_recipe_identity(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy/sandbox", root / "deploy/sandbox")
    before_recipe = resolve_recipe(root, "logs-data", "linux/amd64")
    metadata = load_metadata(root, "logs-data")
    assert set(metadata) == {"label", "description", "use_for", "highlights", "limitations"}
    before_metadata = decode_catalog(doc({"logs-data": entry(metadata["label"])}))
    inventory_path = root / "deploy/sandbox/images.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["managed_profiles"]["logs-data"]["label"] = "New label"
    inventory_path.write_text(json.dumps(inventory))
    after_metadata_only_change = resolve_recipe(root, "logs-data", "linux/amd64")
    after_metadata = decode_catalog(doc({"logs-data": entry(load_metadata(root, "logs-data")["label"])}))
    assert before_recipe.fingerprint == after_metadata_only_change.fingerprint
    assert catalog_digest(before_metadata) != catalog_digest(after_metadata)


def test_all_maintained_descriptors_preserve_recipe_fingerprints(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy/sandbox", root / "deploy/sandbox")
    path = root / "deploy/sandbox/images.json"
    inventory = json.loads(path.read_text())
    before = {key: resolve_recipe(root, key, "linux/amd64").fingerprint for key in IDS}
    for key in IDS:
        metadata = load_metadata(root, key)
        item = entry()
        item["metadata"] = metadata
        item["metadata_digest"] = canonical_digest(metadata)
        decode_catalog(doc({key: item}))
        for field in metadata:
            del inventory["managed_profiles"][key][field]
    path.write_text(json.dumps(inventory))
    assert {key: resolve_recipe(root, key, "linux/amd64").fingerprint for key in IDS} == before


def test_bridge_import_does_not_initialize_settings_or_database():
    process = subprocess.run(
        [sys.executable, "-c", ("import sys; import src.openship.sandboxes.profile_codec; "
         "assert not any(name.startswith(('src.openship.database', 'src.openship.config')) "
         "for name in sys.modules)")], capture_output=True, check=False, timeout=10,
    )
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("field,value", [("smoke_image_id", "sha256:" + "4" * 64),
                                        ("smoke_fingerprint", "4" * 64),
                                        ("smoke_passed", False), ("platform", "linux/arm64")])
def test_smoke_binding_must_match_image_and_fingerprint(field, value):
    item = entry()
    item["runtime"][field] = value
    with pytest.raises(ValueError):
        decode_catalog(doc({"logs-data": item}))


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(metadata_digest="0" * 64),
    lambda e: e["metadata"].update(label="é" * 41),
    lambda e: e["metadata"].update(description="x" * 513),
    lambda e: e["metadata"].update(use_for=["x"] * 9),
    lambda e: e["metadata"].update(highlights=["é" * 81]),
    lambda e: e.update(extensions={"x": "y" * 257}),
    lambda e: e.update(extensions={"x" * 65: "y"}),
    lambda e: e.update(extensions={str(i): "y" for i in range(17)}),
    lambda e: e.update(unknown="x"),
])
def test_field_bounds_and_metadata_digest_are_enforced(mutate):
    item = entry()
    mutate(item)
    if item["metadata_digest"] != "0" * 64:
        item["metadata_digest"] = canonical_digest(item["metadata"])
    with pytest.raises(ValueError):
        decode_catalog(doc({"logs-data": item}))


def test_unknown_ids_and_json_envelope_duplicates_rejected():
    with pytest.raises(ValueError):
        decode_catalog(doc({"other": entry()}))
    process = bridge(b'{"action":"decode","action":"encode","catalog":{}}')
    assert process.returncode != 0
    assert bridge(b" " * (1024 * 1024 + 1)).returncode != 0


def test_smoke_passed_requires_a_boolean_not_numeric_one():
    item = entry()
    item["runtime"]["smoke_passed"] = 1
    with pytest.raises(ValueError):
        decode_catalog(doc({"logs-data": item}))


def test_frozen_models_and_revalidation_on_encode():
    catalog = decode_catalog(doc({"logs-data": entry()}))
    with pytest.raises(ValueError):
        catalog.schema_version = 2
    broken = copy.deepcopy(catalog)
    broken.profiles["logs-data"] = broken.profiles["logs-data"].model_copy(update={"enabled": "yes"})
    with pytest.raises(ValueError):
        encode_catalog(broken)
