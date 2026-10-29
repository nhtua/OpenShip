"""Bootstrap routing and declarative host boundaries, without host mutations."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = ROOT / "scripts/bootstrap"
ROLE = BOOTSTRAP / "roles/rootless_podman"


@pytest.fixture
def controller(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands"
    for name in ("ansible-playbook", "ansible-galaxy", "id"):
        command = binaries / name
        command.write_text('''#!/bin/sh
if [ "${0##*/}" = id ]; then echo 1000; exit 0; fi
printf '%s %s\n' "${0##*/}" "$*" >> "$OPENSHIP_BOOTSTRAP_TEST_LOG"
printf 'config=%s\n' "$ANSIBLE_CONFIG" >> "$OPENSHIP_BOOTSTRAP_TEST_LOG"
''')
        command.chmod(0o755)
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
           "OPENSHIP_BOOTSTRAP_TEST_LOG": str(log)}
    return tmp_path, log, env


def run_bootstrap(controller, *args):
    return subprocess.run([str(BOOTSTRAP / "bootstrap.sh"), *args],
                          cwd=controller[0], env=controller[2], text=True,
                          capture_output=True, check=False)


def test_bootstrap_resolves_target_and_config_from_any_working_directory(controller):
    result = run_bootstrap(controller, "arch-podman", "--configure-only")
    assert result.returncode == 0, result.stderr
    calls = controller[1].read_text()
    assert f"collection install -r {BOOTSTRAP / 'requirements.yml'}" in calls
    assert str(BOOTSTRAP / "playbooks/arch-podman.yml") in calls
    assert f"config={BOOTSTRAP / 'ansible.cfg'}" in calls
    assert "openship_configure_only=true" in calls
    assert "--ask-become-pass" not in calls


@pytest.mark.parametrize("flag", ["--check", "--dry-run"])
def test_check_mode_never_installs_collections(controller, flag):
    result = run_bootstrap(controller, "arch-podman", flag)
    assert result.returncode == 0, result.stderr
    calls = controller[1].read_text()
    assert "ansible-galaxy" not in calls
    assert "--check --diff" in calls
    assert "--ask-become-pass" not in calls


def test_remote_inventory_options_and_probe_are_explicit(controller):
    result = run_bootstrap(controller, "arch-podman", "--verify", "--",
                           "-i", "production.ini", "-c", "ssh", "--limit", "node1")
    assert result.returncode == 0, result.stderr
    calls = controller[1].read_text()
    assert "openship_verify=true" in calls
    assert "production.ini -c ssh --limit node1" in calls
    assert "localhost," not in calls
    assert "--ask-become-pass" in calls


@pytest.mark.parametrize("inventory", ["-iproduction.ini", "--inventory=production.ini", "--inventory-file=production.ini"])
def test_attached_inventory_options_do_not_add_the_local_machine(controller, inventory):
    result = run_bootstrap(controller, "arch-podman", "--configure-only", "--", inventory)
    assert result.returncode == 0, result.stderr
    assert "localhost," not in controller[1].read_text()


@pytest.mark.parametrize("target", ["arch-docker", "debian-docker", "../arch-podman"])
def test_unimplemented_or_invalid_targets_fail_before_installing(controller, target):
    result = run_bootstrap(controller, target)
    assert result.returncode != 0
    assert not controller[1].exists()


def test_host_role_never_manages_normal_socket_or_restarts_sandbox_socket():
    documents = [yaml.safe_load(path.read_text()) for path in (ROLE / "tasks").glob("*.yml")]
    documents.append(yaml.safe_load((ROLE / "handlers/main.yml").read_text()))

    def walk(value):
        if isinstance(value, dict):
            if "ansible.builtin.systemd_service" in value:
                yield value["ansible.builtin.systemd_service"]
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    services = list(walk(documents))
    assert services
    assert all(service["scope"] == "user" for service in services)
    assert all(service.get("name") not in {"podman.socket", "podman.service"} for service in services)
    assert not any(service.get("name") == "openship-sandbox-engine.socket" and
                   service.get("state") == "restarted" for service in services)


def test_probe_validates_actual_runtime_before_execution_and_has_cleanup():
    probe = yaml.safe_load((ROLE / "tasks/verify.yml").read_text())[0]
    actions = probe["block"]
    assert "--pull=never" in actions[0]["ansible.builtin.command"]["argv"]
    assert "--network=none" in actions[0]["ansible.builtin.command"]["argv"]
    assert "'runsc'" in actions[1]["failed_when"]
    assert "inspect" in actions[1]["ansible.builtin.command"]["argv"]
    assert "start" in actions[2]["ansible.builtin.command"]["argv"]
    assert "rm" in probe["always"][0]["ansible.builtin.command"]["argv"]


def test_arch_target_declares_dependencies_and_uses_native_package_module():
    play = yaml.safe_load((BOOTSTRAP / "playbooks/arch-podman.yml").read_text())[0]
    assert set(play["vars"]["openship_arch_packages"]) == {"podman", "podman-compose", "runc", "passt", "shadow"}
    assert play["roles"] == ["rootless_podman"]
    assert any(task.get("community.general.pacman", {}).get("upgrade") for task in play["pre_tasks"])
    aur = next(task for task in play["pre_tasks"] if
               task.get("ansible.builtin.command", {}).get("argv", [""])[0] == "yay")
    assert "gvisor-bin" in aur["ansible.builtin.command"]["argv"]
    assert "creates" not in aur["ansible.builtin.command"]
    assert not aur.get("become", False)
