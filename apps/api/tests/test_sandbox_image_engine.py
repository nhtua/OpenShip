"""Engine adapters always address the explicitly selected socket."""

import re
import sys
from pathlib import Path

import pytest

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from sandbox_image_lib.catalog import ImageError, resolve_recipe
from sandbox_image_lib.engine import Engine

PLATFORM = "linux/amd64"


def test_each_engine_operation_targets_the_explicit_socket(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    endpoint = "unix:///run/fixtures/openship.sock"
    log = catalog["log"]
    for cli in ("docker", "podman"):
        engine = Engine(cli, endpoint, PLATFORM)
        engine.info()
        engine.inspect_image("openship-sandbox-logs-data:abc")
        engine.build(
            dockerfile=catalog["root"] / "deploy/sandbox/Dockerfile.base",
            context=catalog["root"] / "deploy",
            tag="openship-sandbox-base:abc",
            build_args={"PYTHON_BASE_IMAGE": "python:3.12-slim@sha256:aaa"},
            labels={"openship.profile": "base"},
        )
        engine.smoke("IMAGEID-FROMCANNED")
    lines = log.read_text().splitlines()
    calls = [line for line in lines if line.startswith(("docker ", "podman "))]
    assert calls
    for line in calls:
        if line.startswith("docker"):
            assert "--host unix:///run/fixtures/openship.sock" in line
        else:
            assert line.startswith("podman")
            assert "--remote --url unix:///run/fixtures/openship.sock" in line
    for cli in ("docker", "podman"):
        for sub in ("info", "inspect", "build", "run"):
            assert any(line.startswith(cli) and sub in line.split() for line in calls)
    assert "unix:///var/run/docker.sock" not in "".join(calls)


def test_smoke_container_carries_the_unprivileged_offline_policy(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    engine = Engine("podman", "/run/fixtures/openship.sock", PLATFORM)
    engine.smoke("IMAGEID-FROMCANNED")
    line = catalog["log"].read_text()
    assert "run" in line
    for flag in (
        "--rm",
        "--network none",
        "--user 1000:1000",
        "--cpus 1",
        "--memory 512m",
        "--pids-limit 128",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
    ):
        assert flag in line
    assert "--name openship-image-smoke-" in line
    assert "timeout --signal=TERM --kill-after=2 30s python /opt/openship/smoke.py" in line


def test_local_image_without_repo_digest_is_valid(tmp_path, monkeypatch):
    build_catalog(tmp_path, monkeypatch)
    engine = Engine("podman", "/run/fixtures/openship.sock", PLATFORM)
    inspected = engine.inspect_image("openship-sandbox-logs-data:abc")
    assert inspected is not None
    assert inspected["Id"] == "IMAGEID-FROMCANNED"
    assert inspected["Machine"] == "x86_64"
    assert inspected["Tag"] == "openship-sandbox-logs-data:abc"
    assert "RepoDigests" not in inspected


def test_podman_localhost_prefix_is_normalized(tmp_path, monkeypatch):
    build_catalog(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "FAKE_INSPECT_JSON",
        '[{"Id": "IMAGEID-FROMCANNED", "Tag": "localhost/openship-sandbox-logs-data:abc",'
        ' "RepoTags": ["localhost/openship-sandbox-logs-data:abc"], "Machine": "x86_64"}]',
    )
    engine = Engine("podman", "/run/fixtures/openship.sock", PLATFORM)
    inspected = engine.inspect_image("openship-sandbox-logs-data:abc")
    assert inspected["Tag"] == "openship-sandbox-logs-data:abc"
    assert inspected["RepoTags"] == ["openship-sandbox-logs-data:abc"]
    assert inspected["Id"] == "IMAGEID-FROMCANNED"


def test_smoke_timeout_removes_only_owned_container(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_RUN_SLEEP", "2")
    engine = Engine("podman", "/run/fixtures/openship.sock", PLATFORM, host_timeout_s=0.5)
    with pytest.raises(ImageError):
        engine.smoke("IMAGEID-FROMCANNED")
    lines = catalog["log"].read_text().splitlines()
    run_lines = [line for line in lines if "run" in line.split()]
    rm_lines = [line for line in lines if "rm" in line.split()]
    owned = set()
    for line in run_lines:
        owned.update(re.findall(r"--name (openship-image-smoke-[0-9a-f]+)", line))
    assert len(owned) == 1
    assert len(rm_lines) == 1
    assert all(name in rm_lines[0] for name in owned)


def test_unsupported_platform_and_missing_image_fail_before_build(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    with pytest.raises(ImageError):
        resolve_recipe(root, "logs-data", "linux/arm64")
    engine = Engine("docker", "/run/fixtures/openship.sock", PLATFORM)
    assert engine.inspect_image("IMAGEID-FROMCANNED")["Id"] == "IMAGEID-FROMCANNED"
    invoked = [part for line in catalog["log"].read_text().splitlines() for part in line.split()]
    assert "build" not in invoked


@pytest.mark.parametrize("info", [
    '{"Architecture":"arm64","OSType":"linux"}',
    '{"Architecture":"amd64","OSType":"windows"}',
    '{"host":{"arch":"amd64","os":"linux"}}',
])
def test_engine_info_rejects_wrong_platform_or_engine(tmp_path, monkeypatch, info):
    build_catalog(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_INFO_JSON", info)
    with pytest.raises(ImageError):
        Engine("docker", "/run/fixtures/openship.sock", PLATFORM).info()
