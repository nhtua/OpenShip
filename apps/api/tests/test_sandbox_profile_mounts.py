"""Launchers mount one validated snapshot at a fixed read-only target."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import sandbox_images
from sandbox_image_lib.catalog import ImageError, load_metadata, resolve_recipe
from sandbox_image_lib.config import resolve_compose_source
from sandbox_image_lib.profiles import preflight_mapping, read_catalog

from src.openship.capabilities.contracts import canonical_digest
from src.openship.config import settings
from src.openship.execution.availability import sandbox_runtime_ready
from src.openship.sandboxes.profile_schema import decode_catalog, encode_catalog
from src.openship.sandboxes.profiles import (
    DEFAULT_PROFILE_PATH,
    MANAGED_PROFILE_IDS,
    eligible_profiles,
    install_runtime_catalog,
    load_runtime_catalog,
    profile_readiness,
    reset_runtime_catalog,
    startup_catalog,
)

TARGET = "/etc/openship/sandbox-profiles.yaml"
PLATFORM = "linux/amd64"
EXAMPLE = ROOT / "deploy/sandbox/sandbox-profiles.example.yaml"
EXAMPLE_BYTES = EXAMPLE.read_bytes()


class _ComposeLoader(yaml.SafeLoader):
    """Read merged Compose files containing the `!reset` extension tag."""


def _construct_reset(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


_ComposeLoader.add_constructor("!reset", _construct_reset)


def _load(path: Path):
    return yaml.load(path.read_text(), Loader=_ComposeLoader)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "deploy/sandbox").mkdir(parents=True)
    shutil.copy(EXAMPLE, root / "deploy/sandbox/sandbox-profiles.example.yaml")
    return root


def _profile_mount(compose_path: Path, service: str) -> dict:
    compose = _load(compose_path)
    mounts = [
        volume
        for volume in compose["services"][service].get("volumes", [])
        if isinstance(volume, dict) and volume.get("target") == TARGET
    ]
    assert len(mounts) == 1
    return mounts[0]


def _entry(root: Path, profile_id: str, engine_identity: str, image_id: str) -> dict:
    metadata = load_metadata(root, profile_id)
    fingerprint = resolve_recipe(root, profile_id, PLATFORM).fingerprint
    return {
        "enabled": True,
        "extensions": {},
        "metadata": metadata,
        "metadata_digest": canonical_digest(metadata),
        "runtime": {
            "image_tag": f"openship-sandbox-{profile_id}:fixture",
            "image_id": image_id,
            "dependency_identity": f"sha256:{fingerprint}",
            "platform": PLATFORM,
            "fingerprint": fingerprint,
            "engine_identity": engine_identity,
            "smoke_image_id": image_id,
            "smoke_fingerprint": fingerprint,
            "smoke_passed": True,
            "smoke_evidence_digest": "3" * 64,
        },
    }


def _catalog_bytes(root: Path, profile_id: str, engine_identity: str, image_id: str) -> bytes:
    for path in (root / ".env",):
        path.write_text("")
    (root / ".env").write_text("SANDBOX_ENABLED=true\nSANDBOX_PROFILE_ID=logs-data\n")
    return encode_catalog(
        decode_catalog(
            json.dumps(
                {
                    "schema_version": 1,
                    "profiles": {profile_id: _entry(root, profile_id, engine_identity, image_id)},
                }
            ).encode()
        )
    )


def test_default_and_custom_sources_share_fixed_readonly_target(tmp_path):
    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose_path = ROOT / compose_name
        api_mount = _profile_mount(compose_path, "api")
        worker_mount = _profile_mount(compose_path, "worker")

        assert api_mount["target"] == worker_mount["target"] == TARGET
        assert api_mount["read_only"] is True and worker_mount["read_only"] is True
        assert api_mount["bind"]["create_host_path"] is False
        assert worker_mount["bind"]["create_host_path"] is False
        assert api_mount["type"] == worker_mount["type"] == "bind"
        assert api_mount["source"] == worker_mount["source"]
        assert api_mount["source"].startswith("${SANDBOX_PROFILES_FILE:")

        compose = _load(compose_path)
        for service in ("api", "worker"):
            environment = compose["services"][service]["environment"]
            assert "SANDBOX_PROFILES_FILE" not in environment
            assert "OPENSHIP_SANDBOX_PROFILES_FILE" not in environment

    dev = _load(ROOT / "docker-compose.dev.yml")
    expected_dev_volumes = {
        "api": [
            "./apps/api/src:/app/src:ro",
            "./apps/api/alembic:/app/alembic:ro",
            "./apps/api/alembic.ini:/app/alembic.ini:ro",
        ],
        "worker": ["./apps/api/src:/app/src:ro"],
    }
    for service, expected in expected_dev_volumes.items():
        volumes = [
            volume
            for volume in dev["services"][service]["volumes"]
            if isinstance(volume, str)
        ]
        assert volumes == expected
        assert not any("sandbox-profiles" in volume for volume in volumes)
    rootless = _load(ROOT / "docker-compose.sandbox.rootless.yml")
    assert set(rootless["services"]) == {"opensandbox-server"}

    root = _repo(tmp_path)
    (root / ".env").write_text("")
    generated = root / "deploy/sandbox/sandbox-profiles.yaml"
    generated.write_bytes(EXAMPLE_BYTES)
    default_source = resolve_compose_source(root, {})
    assert default_source == generated

    custom = root / "custom-profiles.yaml"
    custom.write_bytes(EXAMPLE_BYTES)
    (root / ".env").write_text("SANDBOX_PROFILES_FILE=custom-profiles.yaml\n")
    custom_source = resolve_compose_source(root, {})
    assert custom_source == custom

    assert load_runtime_catalog(default_source) == load_runtime_catalog(custom_source)


def test_disabled_missing_default_uses_example_without_writing(tmp_path):
    root = _repo(tmp_path)
    generated = root / "deploy/sandbox/sandbox-profiles.yaml"
    (root / ".env").write_text("SANDBOX_ENABLED=false\n")

    source = resolve_compose_source(root, {})

    assert source == root / "deploy/sandbox/sandbox-profiles.example.yaml"
    assert not generated.exists()
    assert read_catalog(root, source)["profiles"] == {}
    reset_runtime_catalog()
    empty = startup_catalog(
        settings_obj=SimpleNamespace(
            sandbox_enabled=False, sandbox_profile_id="logs-data"
        ),
        path=tmp_path / "never-read.yaml",
    )
    assert empty.profiles == {}
    assert not generated.exists()


def test_explicit_missing_or_bad_override_never_falls_back(tmp_path):
    root = _repo(tmp_path)
    example = root / "deploy/sandbox/sandbox-profiles.example.yaml"

    (root / ".env").write_text("SANDBOX_ENABLED=false\nSANDBOX_PROFILES_FILE=missing.yaml\n")
    with pytest.raises(ImageError, match="existing regular file"):
        resolve_compose_source(root, {})
    assert not (root / "deploy/sandbox/sandbox-profiles.yaml").exists()

    with pytest.raises(ImageError, match="existing regular file"):
        resolve_compose_source(root, {"SANDBOX_PROFILES_FILE": "other-missing.yaml"})

    directory = root / "not-a-file"
    directory.mkdir()
    (root / ".env").write_text("SANDBOX_PROFILES_FILE=not-a-file\n")
    with pytest.raises(ImageError, match="existing regular file"):
        resolve_compose_source(root, {})

    (root / ".env").write_text("SANDBOX_PROFILES_FILE=${MISSING}\n")
    with pytest.raises(ImageError, match="literal"):
        resolve_compose_source(root, {})

    (root / ".env").write_text("SANDBOX_ENABLED=true\n")
    with pytest.raises(ImageError, match="profile snapshot is missing"):
        resolve_compose_source(root, {})

    custom = root / "custom.yaml"
    custom.write_bytes(EXAMPLE_BYTES)
    (root / ".env").write_text("SANDBOX_ENABLED=false\nSANDBOX_PROFILES_FILE=custom.yaml\n")
    assert resolve_compose_source(root, {}) == custom
    assert custom != example


def test_mapping_preflight_uses_manager_engine_and_immutable_identity(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    identity = f"docker@unix://{os.environ['OPENSHIP_SANDBOX_ENGINE_SOCKET']}@{PLATFORM}"
    image_id = "sha256:" + "a" * 64
    mapping_engine_identity = identity

    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    destination.write_bytes(_catalog_bytes(root, "logs-data", identity, image_id))
    snapshot = decode_catalog(destination.read_bytes())

    probe_engine = SimpleNamespace(
        identity=mapping_engine_identity,
        platform=PLATFORM,
        inspect_image=lambda reference: {"Id": reference} if reference == image_id else None,
    )

    assert preflight_mapping(snapshot.model_dump(mode="json"), probe_engine) == ("logs-data",)
    assert probe_engine.identity == snapshot.profiles["logs-data"].runtime.engine_identity

    monkeypatch.setenv("FAKE_INSPECT_JSON", json.dumps([{"Id": image_id}]))
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("SANDBOX_DEPENDENCY_IDENTITY", raising=False)
    assert sandbox_images.main(["--root", str(root), "--engine", "docker", "preflight"]) == 0

    stale = SimpleNamespace(
        identity=mapping_engine_identity,
        platform=PLATFORM,
        inspect_image=lambda _reference: {"Id": "sha256:" + "b" * 64},
    )
    with pytest.raises(ImageError, match="ready"):
        preflight_mapping(snapshot.model_dump(mode="json"), stale)

    foreign = SimpleNamespace(identity="docker@unix:///other.sock@linux/amd64", platform=PLATFORM)
    with pytest.raises(ImageError, match="engine"):
        preflight_mapping(snapshot.model_dump(mode="json"), foreign)


def test_prod_env_does_not_change_root_dotenv_selector_contract(tmp_path):
    root = _repo(tmp_path)
    example = root / "deploy/sandbox/sandbox-profiles.example.yaml"
    shared = root / "shared.yaml"
    shared.write_bytes(EXAMPLE_BYTES)
    prod = root / "prod.yaml"
    prod.write_bytes(EXAMPLE_BYTES)
    settings_file = root / ".env.prod"

    (root / ".env").write_text("SANDBOX_PROFILES_FILE=shared.yaml\n")
    settings_file.write_text("SANDBOX_ENABLED=true\nSANDBOX_PROFILES_FILE=prod.yaml\n")

    assert resolve_compose_source(root, {}, settings_file=settings_file) == shared
    assert (
        resolve_compose_source(
            root,
            {"SANDBOX_PROFILES_FILE": "prod.yaml"},
            settings_file=settings_file,
        )
        == prod
    )

    (root / ".env").write_text("")
    settings_file.write_text("SANDBOX_ENABLED=true\n")
    with pytest.raises(ImageError, match="profile snapshot is missing"):
        resolve_compose_source(root, {}, settings_file=settings_file)

    settings_file.write_text("SANDBOX_ENABLED=false\n")
    assert resolve_compose_source(root, {}, settings_file=settings_file) == example


def test_runtime_catalog_is_bounded_and_eligibility_is_deterministic(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    identity = f"docker@unix://{os.environ['OPENSHIP_SANDBOX_ENGINE_SOCKET']}@{PLATFORM}"
    image_id = "sha256:" + "c" * 64
    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    destination.write_bytes(_catalog_bytes(root, "logs-data", identity, image_id))

    loaded = load_runtime_catalog(destination)
    assert eligible_profiles(loaded, frozenset({"logs-data", "standard"})) == ("logs-data",)
    assert eligible_profiles(loaded, frozenset({"standard"})) == ()
    readiness = profile_readiness(loaded, frozenset({"logs-data"}))
    assert readiness == {
        "digest": readiness["digest"],
        "eligible": ["logs-data"],
        "ready": True,
    }
    assert "sha256:" not in json.dumps(readiness)
    assert "unix://" not in json.dumps(readiness)

    reset_runtime_catalog()
    assert startup_catalog(
        settings_obj=SimpleNamespace(sandbox_enabled=True, sandbox_profile_id="logs-data"),
        path=destination,
    ) == loaded
    reset_runtime_catalog()

    oversized = tmp_path / "oversized.yaml"
    oversized.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="limit"):
        load_runtime_catalog(oversized)

    linked = tmp_path / "linked.yaml"
    linked.symlink_to(destination)
    with pytest.raises(ValueError, match="regular file"):
        load_runtime_catalog(linked)
    assert DEFAULT_PROFILE_PATH == Path("/etc/openship/sandbox-profiles.yaml")


def test_direct_compose_interpolates_the_profile_selector_only():
    expected = "${SANDBOX_PROFILES_FILE:?Set the sandbox profile snapshot source}"
    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = _load(ROOT / compose_name)
        for service in ("api", "worker"):
            mount = _profile_mount(ROOT / compose_name, service)
            assert mount["source"] == expected
            assert "OPENSHIP_SANDBOX_PROFILES_FILE" not in mount["source"]
            environment = compose["services"][service]["environment"]
            assert "SANDBOX_PROFILES_FILE" not in environment
            assert "OPENSHIP_SANDBOX_PROFILES_FILE" not in environment


def test_readiness_requires_the_selected_profile_even_when_another_is_eligible(
    tmp_path, monkeypatch
):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    identity = f"docker@unix://{os.environ['OPENSHIP_SANDBOX_ENGINE_SOCKET']}@{PLATFORM}"
    image_id = "sha256:" + "d" * 64
    destination = root / "deploy/sandbox/sandbox-profiles.yaml"
    destination.write_bytes(_catalog_bytes(root, "logs-data", identity, image_id))
    loaded = load_runtime_catalog(destination)
    key = tmp_path / "sandbox-key"
    key.write_text("secret\n")

    monkeypatch.setattr(settings, "sandbox_enabled", True)
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://opensandbox:8080")
    monkeypatch.setattr(settings, "sandbox_key_file", key)
    try:
        install_runtime_catalog(loaded)
        assert eligible_profiles(loaded, MANAGED_PROFILE_IDS) == ("logs-data",)

        monkeypatch.setattr(settings, "sandbox_profile_id", "standard")
        assert sandbox_runtime_ready() is False
        projection = profile_readiness(
            loaded, MANAGED_PROFILE_IDS, selected=settings.sandbox_profile_id
        )
        assert projection["eligible"] == ["logs-data"]
        assert projection["ready"] is False

        monkeypatch.setattr(settings, "sandbox_profile_id", "logs-data")
        assert sandbox_runtime_ready() is True
        projection = profile_readiness(
            loaded, MANAGED_PROFILE_IDS, selected=settings.sandbox_profile_id
        )
        assert projection["ready"] is True
    finally:
        reset_runtime_catalog()


def test_stack_exports_resolved_source_and_refuses_missing_explicit_override(
    tmp_path, monkeypatch
):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/sandbox_images.py", scripts)
    shutil.copytree(
        ROOT / "scripts/sandbox_image_lib",
        scripts / "sandbox_image_lib",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    stack = scripts / "openship-stack"
    stack.write_text((ROOT / "scripts/openship-stack").read_text())
    stack.chmod(0o755)
    shutil.copy(EXAMPLE, root / "deploy/sandbox/sandbox-profiles.example.yaml")

    bin_dir = tmp_path / "compose-bin"
    bin_dir.mkdir()
    compose_log = tmp_path / "compose-profile.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then echo 2.30.0; exit 0; fi\n'
        f'printf \'%s\\n\' "$SANDBOX_PROFILES_FILE" >> "{compose_log}"\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CONTAINER_ENGINE": "docker",
    }

    custom = root / "custom-profiles.yaml"
    custom.write_bytes(EXAMPLE_BYTES)
    (root / ".env").write_text("SANDBOX_ENABLED=false\nSANDBOX_PROFILES_FILE=custom-profiles.yaml\n")
    result = subprocess.run(
        [str(stack), "-e", "dev", "status"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert compose_log.read_text().splitlines() == [str(custom)]

    compose_log.unlink()
    (root / ".env").write_text("SANDBOX_ENABLED=false\n")
    result = subprocess.run(
        [str(stack), "-e", "dev", "status"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    example = root / "deploy/sandbox/sandbox-profiles.example.yaml"
    assert compose_log.read_text().splitlines() == [str(example)]
    assert not (root / "deploy/sandbox/sandbox-profiles.yaml").exists()

    compose_log.unlink()
    (root / ".env").write_text("SANDBOX_PROFILES_FILE=missing.yaml\n")
    result = subprocess.run(
        [str(stack), "-e", "dev", "status"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "existing regular file" in result.stderr
    assert not compose_log.exists()
