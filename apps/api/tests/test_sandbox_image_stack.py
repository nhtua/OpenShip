"""Exercise the actual Bash wrapper; stop at the first initialization boundary."""

import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.sandbox_image_fixture_support import build_catalog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from sandbox_image_lib.catalog import ImageError, resolve_recipe
from sandbox_image_lib.config import read_settings, resolve_endpoint, validate_selection


def wrapper(tmp_path, monkeypatch, *, enabled="true", profile="logs-data"):
    catalog = build_catalog(tmp_path, monkeypatch)
    root = catalog["root"]
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/sandbox_images.py", scripts)
    shutil.copytree(ROOT / "scripts/sandbox_image_lib", scripts / "sandbox_image_lib", ignore=shutil.ignore_patterns("__pycache__"))
    text = (ROOT / "scripts/openship-stack").read_text().replace(
        "initialize_dev_messaging_if_missing() {",
        'initialize_dev_messaging_if_missing() {\n  touch "$ROOT/mutated"; return 42',
    )
    stack = scripts / "openship-stack"
    stack.write_text(text)
    stack.chmod(0o755)
    recipe = resolve_recipe(root, "logs-data", "linux/amd64")
    config = (
        f"SANDBOX_ENABLED={enabled}\nSANDBOX_PROFILE_ID={profile}\n"
        f"SANDBOX_IMAGE={recipe.image_tag}\nSANDBOX_DEPENDENCY_IDENTITY={recipe.dependency_identity}\n"
        "UNRELATED_SECRET=$(touch should-not-exist)\n"
    )
    (root / ".env").write_text(config)
    return catalog, stack


def call(stack, *args):
    return subprocess.run([str(stack), *args], capture_output=True, text=True, check=False)


def test_public_image_commands_leave_protected_files_and_stack_untouched(tmp_path, monkeypatch):
    catalog, stack = wrapper(tmp_path, monkeypatch)
    root = catalog["root"]
    (root / "secrets").mkdir()
    secret = root / "secrets/openship-sandbox-key"
    secret.write_text("fixture-private-key-not-for-output\n")
    before = (secret.read_bytes(), (root / ".env").read_bytes())
    for command in (("list",), ("build", "logs-data"), ("build", "--all"), ("smoke", "logs-data")):
        result = call(stack, "-e", "dev", "sandbox-images", *command)
        assert result.returncode == 0, result.stderr
        assert "fixture-private-key-not-for-output" not in result.stdout + result.stderr
    assert (secret.read_bytes(), (root / ".env").read_bytes()) == before
    assert not (root / "mutated").exists()
    assert not (root / "should-not-exist").exists()


def test_missing_image_start_never_builds_or_initializes(tmp_path, monkeypatch):
    catalog, stack = wrapper(tmp_path, monkeypatch)
    result = call(stack, "-e", "dev", "--sandbox", "start")
    assert result.returncode != 0
    assert "sandbox-images build logs-data" in result.stderr
    assert not (catalog["root"] / "mutated").exists()
    assert " build " not in catalog["log"].read_text()


def test_opt_in_builds_only_selected_profile_then_matching_start_reuses(tmp_path, monkeypatch):
    catalog, stack = wrapper(tmp_path, monkeypatch)
    result = call(stack, "-e", "dev", "--sandbox", "start", "--build-sandbox",
                  "--cutover", "11111111-1111-1111-1111-111111111111")
    assert result.returncode == 42, result.stderr
    log = catalog["log"].read_text()
    assert "--label openship.profile=logs-data" in log
    assert "--label openship.profile=standard" not in log
    assert (catalog["root"] / "mutated").exists()
    catalog["log"].write_text("")
    result = call(stack, "-e", "dev", "--sandbox", "start")
    assert result.returncode == 42, result.stderr
    assert " build " not in catalog["log"].read_text()


@pytest.mark.parametrize("failure", ["build", "selection", "stale"])
def test_preflight_failure_precedes_initialization(tmp_path, monkeypatch, failure):
    catalog, stack = wrapper(tmp_path, monkeypatch)
    if failure == "build":
        monkeypatch.setenv("FAKE_BUILD_RC", "1")
    elif failure == "selection":
        monkeypatch.setenv("SANDBOX_DEPENDENCY_IDENTITY", "contradictory")
    else:
        assert call(stack, "-e", "dev", "sandbox-images", "build", "logs-data").returncode == 0
        monkeypatch.setenv("FAKE_INSPECT_JSON", '[{"Id":"retagged","Architecture":"amd64"}]')
    flags = ("--build-sandbox",) if failure != "stale" else ()
    result = call(stack, "-e", "dev", "--sandbox", "start", *flags)
    assert result.returncode != 0
    assert not (catalog["root"] / "mutated").exists()


@pytest.mark.parametrize("args", [
    ("-e", "dev", "start", "--build-sandbox"),
    ("-e", "local", "start", "--build-sandbox"),
    ("-e", "prod", "start", "--build-sandbox"),
    ("-e", "prod", "sandbox-images", "list"),
    ("-e", "local", "sandbox-images", "list"),
    ("-e", "dev", "--sandbox", "start", "--build-sandbox", "--build-sandbox"),
    ("-e", "dev", "--sandbox", "start", "--unknown"),
    ("-e", "dev", "sandbox-images", "preflight"),
])
def test_invalid_build_options_fail_before_side_effects(tmp_path, monkeypatch, args):
    catalog, stack = wrapper(tmp_path, monkeypatch)
    assert call(stack, *args).returncode == 2
    assert not (catalog["root"] / "mutated").exists()
    assert not (catalog["root"] / ".openship").exists()


@pytest.mark.parametrize("enabled,profile", [("false", "logs-data"), ("true", "python311")])
def test_disabled_and_legacy_start_do_not_validate_managed_identity(tmp_path, monkeypatch, enabled, profile):
    catalog, stack = wrapper(tmp_path, monkeypatch, enabled=enabled, profile=profile)
    monkeypatch.setenv("SANDBOX_IMAGE", "${LEGACY_INTERPOLATION}")
    assert call(stack, "-e", "dev", "--sandbox", "start").returncode == 42
    assert not catalog["log"].exists()


def test_disabled_opt_in_prepares_image_without_enabling_execution(tmp_path, monkeypatch):
    catalog, stack = wrapper(tmp_path, monkeypatch, enabled="false")
    before = (catalog["root"] / ".env").read_bytes()
    assert call(stack, "-e", "dev", "--sandbox", "start", "--build-sandbox").returncode == 42
    assert "--label openship.profile=logs-data" in catalog["log"].read_text()
    assert (catalog["root"] / ".env").read_bytes() == before


def test_selected_dotenv_literals_overrides_and_socket_resolution(tmp_path):
    sockpath = tmp_path / "manager with spaces.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(sockpath))
        (tmp_path / ".env").write_bytes((
            "export SANDBOX_ENABLED = 'true' # comment\r\nSANDBOX_PROFILE_ID=standard\r\n"
            'SANDBOX_IMAGE="${UNSUPPORTED}"\r\n'
            f'OPENSHIP_SANDBOX_ENGINE_SOCKET="{sockpath}" # comment\r\n'
            "UNRELATED_SECRET='unterminated\r\n"
        ).encode())
        with pytest.raises(ImageError, match="literal"):
            read_settings(tmp_path, {})
        settings = read_settings(tmp_path, {"SANDBOX_IMAGE": "literal-image"})
        assert settings["SANDBOX_IMAGE"] == "literal-image"
        assert "UNRELATED_SECRET" not in settings
        assert resolve_endpoint("docker", settings) == f"unix://{sockpath}"
        with pytest.raises(ImageError):
            resolve_endpoint("docker", {"OPENSHIP_SANDBOX_ENGINE_SOCKET": "tcp://remote:1234"})


def test_selection_rejects_conflicting_identity_without_echoing_it(tmp_path, monkeypatch):
    catalog = build_catalog(tmp_path, monkeypatch)
    recipe = resolve_recipe(catalog["root"], "standard", "linux/amd64")
    with pytest.raises(ImageError) as caught:
        validate_selection(recipe, {"SANDBOX_IMAGE": "private-sensitive-value"})
    assert "private-sensitive-value" not in str(caught.value)


def test_compose_interpolation_in_selectors_does_not_tighten_legacy_or_disabled_paths(tmp_path):
    (tmp_path / ".env").write_text(
        "SANDBOX_ENABLED=${SANDBOX_ENABLED:-false}\n"
        "SANDBOX_PROFILE_ID=${SANDBOX_PROFILE_ID:-python311}\n"
        "SANDBOX_IMAGE=${LEGACY_IMAGE}\n"
    )
    settings = read_settings(tmp_path, {})
    assert settings["SANDBOX_ENABLED"] == "${SANDBOX_ENABLED:-false}"
    assert settings["SANDBOX_PROFILE_ID"] == "${SANDBOX_PROFILE_ID:-python311}"
    assert settings["SANDBOX_IMAGE"] == "${LEGACY_IMAGE}"
