"""Checks for the opt-in OpenSandbox manager deployment."""

import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
HOST_FILES = ROOT / "scripts/bootstrap/roles/rootless_podman/files"


def test_sandbox_services_in_base_compose_are_private_and_opt_in():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    server = compose["services"]["opensandbox-server"]
    assert server["profiles"] == ["sandbox"]
    assert not server.get("ports")
    assert "OPENSHIP_SANDBOX_SERVER_IMAGE" in server["image"]
    assert "OPENSHIP_SANDBOX_API_KEY" in server["environment"]["OPENSANDBOX_SERVER_API_KEY"]
    assert server["healthcheck"]
    assert server["restart"] == "unless-stopped"
    assert any("docker.sock" in volume for volume in server["volumes"])
    assert "sandbox-manager" in server["networks"]
    assert "sandbox-data" in server["networks"]
    for name, service in compose["services"].items():
        if name != "opensandbox-server":
            assert all("docker.sock" not in volume for volume in service.get("volumes", []))
    assert compose["networks"]["sandbox-data"]["internal"] is True
    assert compose["networks"]["sandbox-data"]["name"] == "openship-sandbox-data"


def test_example_config_explicitly_selects_gvisor_and_private_network():
    config = tomllib.loads((ROOT / "deploy/sandbox/config.example.toml").read_text())
    assert config["runtime"]["type"] == "docker"
    assert config["secure_runtime"] == {"type": "gvisor", "docker_runtime": "runsc"}
    assert config["docker"]["network_mode"] == "openship-sandbox-data"
    assert config["docker"]["no_new_privileges"] is True
    assert config["server"]["max_sandbox_timeout_seconds"] >= 1800
    assert config["runtime"]["execd_run_as_init"] is True
    assert config["docker"]["publish_host"] == "127.0.0.1"


def test_python_image_keeps_input_read_only_and_output_owned_by_workload():
    dockerfile = (ROOT / "deploy/sandbox/Dockerfile.python").read_text()
    assert "ARG PYTHON_BASE_IMAGE" in dockerfile
    assert "--uid 1000" in dockerfile
    assert "/code /inputs" in dockerfile
    assert "/outputs" in dockerfile


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_stack_selects_sandbox_profile_only_when_requested(tmp_path, engine):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    stack = scripts / "openship-stack"
    stack.write_text((ROOT / "scripts/openship-stack").read_text())
    stack.chmod(0o755)
    (tmp_path / ".env").write_text("")
    docker = tmp_path / engine
    docker.write_text('#!/bin/sh\nif [ "$2" = version ]; then echo 2.30.0; else printf "%s\\n" "$*" >> "$OPENSHIP_TEST_LOG"; fi\n')
    docker.chmod(0o755)
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CONTAINER_ENGINE": engine, "OPENSHIP_TEST_LOG": str(log)}

    subprocess.run([str(stack), "-e", "dev", "status"], env=env, check=True)
    assert "docker-compose.sandbox.yml" not in log.read_text()
    assert "docker-compose.sandbox.rootless.yml" not in log.read_text()
    log.unlink()
    subprocess.run([str(stack), "-e", "dev", "--sandbox", "status"], env=env, check=True)
    assert "docker-compose.sandbox.yml" not in log.read_text()
    assert "--profile sandbox" in log.read_text()
    assert ("docker-compose.sandbox.rootless.yml" in log.read_text()) == (engine == "podman")


def test_stack_defaults_to_docker_when_both_engines_exist(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    stack = scripts / "openship-stack"
    stack.write_text((ROOT / "scripts/openship-stack").read_text())
    stack.chmod(0o755)
    (tmp_path / ".env").write_text("")
    for name in ("docker", "podman"):
        executable = tmp_path / name
        executable.write_text(
            '#!/bin/sh\nif [ "$2" = version ]; then echo 2.30.0; '
            'else basename "$0" >> "$OPENSHIP_TEST_LOG"; fi\n'
        )
        executable.chmod(0o755)
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "OPENSHIP_TEST_LOG": str(log)}
    env.pop("CONTAINER_ENGINE", None)
    subprocess.run([str(stack), "-e", "dev", "--sandbox", "status"], env=env, check=True)
    assert log.read_text().splitlines() == ["docker"]


def test_rootless_manager_uses_user_socket_and_keeps_socket_access_private():
    compose = yaml.safe_load((ROOT / "docker-compose.sandbox.rootless.yml").read_text())
    assert set(compose["services"]) == {"opensandbox-server"}
    server = compose["services"]["opensandbox-server"]
    assert server["runtime"] == "runc"
    assert server["user"] == "0:0"
    assert server["security_opt"] == ["label=disable"]
    assert server["environment"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    mount = server["volumes"][0]
    assert mount.endswith(":/var/run/docker.sock")
    assert "XDG_RUNTIME_DIR" in mount
    assert "openship-sandbox/podman.sock" in mount
    assert "OPENSHIP_SANDBOX_ENGINE_SOCKET" in mount


def test_rootless_engine_profile_passes_flags_only_to_runsc():
    config = tomllib.loads((HOST_FILES / "containers.rootless.conf").read_text())
    assert config["engine"]["runtime"] == "runc"
    assert config["engine"]["runtimes"]["runsc"] == ["/usr/bin/runsc"]
    assert config["engine"]["runtimes_flags"] == {"runsc": ["ignore-cgroups"]}


def test_sandbox_socket_selects_runsc_in_its_own_rootless_service():
    service = (HOST_FILES / "openship-sandbox-engine.service").read_text()
    socket = (HOST_FILES / "openship-sandbox-engine.socket").read_text()
    assert "ExecStart=/usr/bin/podman --runtime=runsc system service" in service
    assert "Requires=openship-sandbox-engine.socket" in service
    assert "Delegate=true" in service
    assert "ListenStream=%t/openship-sandbox/podman.sock" in socket
    assert "SocketMode=0600" in socket


def test_gateway_authenticates_proxy_without_worker_bypass():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    server = compose["services"]["opensandbox-server"]
    gateway = compose["services"]["opensandbox-gateway"]
    assert "sandbox-control" not in server["networks"]
    assert set(gateway["networks"]) == {"sandbox-control", "sandbox-manager"}
    assert "sandbox-manager" in server["networks"]
    assert "sandbox-manager" not in compose["services"]["worker"].get("networks", [])
    assert not gateway.get("ports")
    assert gateway["profiles"] == ["sandbox"]
    assert "OPENSHIP_SANDBOX_GATEWAY_IMAGE" in gateway["image"]
    assert any("OPENSHIP_SANDBOX_AUTH_MAP" in value and value.endswith(":ro") for value in gateway["volumes"])
    config = (ROOT / "deploy/sandbox/nginx.conf").read_text()
    assert "map $http_open_sandbox_api_key $sandbox_authorized" in config
    assert "default 0;" in config
    assert "include /etc/opensandbox/auth.map;" in config
    assert "if ($sandbox_authorized = 0) { return 403; }" in config
    assert "proxy_pass http://opensandbox-server:8080;" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config


def test_provider_test_override_exposes_only_loopback_gateway():
    overlay = yaml.safe_load((ROOT / "docker-compose.sandbox.yml").read_text())
    assert set(overlay["services"]) == {"opensandbox-gateway"}
    assert overlay["services"]["opensandbox-gateway"]["ports"] == [
        "127.0.0.1:${OPENSHIP_SANDBOX_TEST_PORT:-18080}:8080"
    ]
    assert "sandbox-test-ingress" in overlay["services"]["opensandbox-gateway"]["networks"]
    assert not overlay["networks"]["sandbox-test-ingress"].get("internal", False)


def test_default_compose_renders_without_sandbox_settings():
    env = {**os.environ, "JWT_SECRET": "test", "ARTIFACT_STORAGE_ID": "test"}
    for name in list(env):
        if name.startswith("OPENSHIP_SANDBOX_"):
            del env[name]
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "config", "--quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_manager_rejects_missing_key_before_starting():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    entrypoint = compose["services"]["opensandbox-server"]["entrypoint"]
    env = {**os.environ, "OPENSANDBOX_SERVER_API_KEY": ""}
    result = subprocess.run(entrypoint, env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "key" in result.stderr.lower()
