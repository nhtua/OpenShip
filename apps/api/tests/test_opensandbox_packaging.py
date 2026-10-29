"""Checks for the opt-in OpenSandbox manager deployment."""

import os
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_sandbox_overlay_is_private_and_opt_in():
    compose = yaml.safe_load((ROOT / "docker-compose.sandbox.yml").read_text())
    server = compose["services"]["opensandbox-server"]
    assert server["profiles"] == ["sandbox"]
    assert not server.get("ports")
    assert "OPENSHIP_SANDBOX_SERVER_IMAGE" in server["image"]
    assert "OPENSHIP_SANDBOX_API_KEY" in server["environment"]["OPENSANDBOX_SERVER_API_KEY"]
    assert server["healthcheck"]
    assert server["restart"] == "unless-stopped"
    assert any("docker.sock" in volume for volume in server["volumes"])
    assert "sandbox-control" in server["networks"]
    assert "sandbox-data" in server["networks"]
    assert "sandbox-control" in compose["services"]["worker"]["networks"]
    assert "sandbox-data" not in compose["services"]["worker"]["networks"]
    assert compose["networks"]["sandbox-data"]["internal"] is True
    assert compose["networks"]["sandbox-data"]["name"] == "openship-sandbox-data"


def test_example_config_explicitly_selects_gvisor_and_private_network():
    config = tomllib.loads((ROOT / "deploy/sandbox/config.example.toml").read_text())
    assert config["runtime"]["type"] == "docker"
    assert config["secure_runtime"] == {"type": "gvisor", "docker_runtime": "runsc"}
    assert config["docker"]["network_mode"] == "openship-sandbox-data"
    assert config["docker"]["no_new_privileges"] is True
    assert config["server"]["max_sandbox_timeout_seconds"] == 120
    assert config["runtime"]["execd_run_as_init"] is True
    assert config["docker"]["publish_host"] == "127.0.0.1"


def test_python_image_keeps_input_read_only_and_output_owned_by_workload():
    dockerfile = (ROOT / "deploy/sandbox/Dockerfile.python").read_text()
    assert "ARG PYTHON_BASE_IMAGE" in dockerfile
    assert "--uid 1000" in dockerfile
    assert "/code /input" in dockerfile
    assert "/output" in dockerfile


def test_stack_selects_sandbox_overlay_only_when_requested(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    stack = scripts / "openship-stack"
    stack.write_text((ROOT / "scripts/openship-stack").read_text())
    stack.chmod(0o755)
    (tmp_path / ".env").write_text("")
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nif [ "$2" = version ]; then echo 2.30.0; else printf "%s\\n" "$*" >> "$OPENSHIP_TEST_LOG"; fi\n')
    docker.chmod(0o755)
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CONTAINER_ENGINE": "docker", "OPENSHIP_TEST_LOG": str(log)}

    subprocess.run([str(stack), "-e", "dev", "status"], env=env, check=True)
    assert "docker-compose.sandbox.yml" not in log.read_text()
    log.unlink()
    subprocess.run([str(stack), "-e", "dev", "--sandbox", "status"], env=env, check=True)
    assert "docker-compose.sandbox.yml" in log.read_text()
    assert "--profile sandbox" in log.read_text()
