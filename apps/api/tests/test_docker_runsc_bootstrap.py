"""The Docker bootstrap probe must reject fallback and ineffective limits."""

import json
import runpy
import subprocess
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[3] / "scripts/bootstrap/roles/docker_engine/files/verify_runsc.py"


@pytest.fixture
def probe():
    return runpy.run_path(str(PROBE))


@pytest.fixture
def host_files(tmp_path):
    proc, groups = tmp_path / "proc", tmp_path / "cgroups"
    (proc / "123").mkdir(parents=True)
    (proc / "123/cgroup").write_text("0::/sandbox.scope\n")
    leaf = groups / "sandbox.scope"
    leaf.mkdir(parents=True)
    for key, value in {
        "memory.max": "536870912", "memory.swap.max": "0",
        "cpu.max": "100000 100000", "pids.max": "128",
    }.items():
        (leaf / key).write_text(value + "\n")
    return proc, groups


def test_probe_reads_actual_process_limits(probe, host_files):
    result = probe["inspect_limits"](123, *host_files)
    assert result["memory.max"] == "536870912"
    assert result["cpu.max"] == "100000 100000"


@pytest.mark.parametrize("key,value", [
    ("memory.max", "max"), ("memory.swap.max", "max"),
    ("cpu.max", "max 100000"), ("pids.max", "max"),
])
def test_probe_rejects_each_missing_effective_limit(probe, host_files, key, value):
    (host_files[1] / "sandbox.scope" / key).write_text(value)
    with pytest.raises(RuntimeError, match="limit"):
        probe["inspect_limits"](123, *host_files)


def test_probe_rejects_runtime_fallback_before_start(probe):
    with pytest.raises(RuntimeError, match="runsc"):
        probe["require_runsc"]({"HostConfig": {"Runtime": "runc"}})


def test_probe_rejects_cgroup_path_outside_host_mount(probe, host_files):
    (host_files[0] / "123/cgroup").write_text("0::/../../outside\n")
    with pytest.raises(RuntimeError, match="cgroup"):
        probe["inspect_limits"](123, *host_files)


@pytest.mark.parametrize("lost_create_reply", [False, True])
def test_probe_cleans_unique_name_after_failed_or_uncertain_creation(probe, monkeypatch, lost_create_reply):
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "info":
            return subprocess.CompletedProcess(args, 0, '["name=rootless"]', '')
        if args[0] == "create" and lost_create_reply:
            raise subprocess.TimeoutExpired("docker create", 120)
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, '[{"HostConfig":{"Runtime":"runc"}}]', '')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setitem(probe["probe"].__globals__, "docker", fake_docker)
    error_type = subprocess.TimeoutExpired if lost_create_reply else RuntimeError
    with pytest.raises(error_type):
        probe["probe"]("trusted-python-image")
    assert sum(args[0] == "create" for args in calls) == 1
    assert not any(args[0] == "start" for args in calls)
    created = next(args for args in calls if args[0] == "create")
    name = created[created.index("--name") + 1]
    assert ("rm", "--force", name) in calls
    assert calls[-1][0] == "ps"


def test_probe_refuses_rootful_daemon_before_creating_or_pulling(probe, monkeypatch):
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '["name=seccomp"]', '')

    monkeypatch.setitem(probe["probe"].__globals__, "docker", fake_docker)
    with pytest.raises(RuntimeError, match="rootless"):
        probe["probe"]("trusted-python-image")
    assert [args[0] for args in calls] == ["info"]


def test_explicit_rootful_mode_rejects_a_rootless_endpoint(probe, monkeypatch):
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '["name=rootless"]', '')

    monkeypatch.setitem(probe["probe"].__globals__, "docker", fake_docker)
    with pytest.raises(RuntimeError, match="rootful"):
        probe["probe"]("trusted-python-image", mode="rootful")
    assert [args[0] for args in calls] == ["info"]


@pytest.mark.parametrize("field,value", [("uid", 0), ("gid", 0), ("cap_eff", 1), ("cap_bnd", 1), ("nnp", 0)])
def test_probe_rejects_workload_privilege_regression(probe, field, value):
    identity = {"uid": 1000, "gid": 1000, "cap_eff": 0, "cap_bnd": 0, "cap_prm": 0, "cap_inh": 0, "nnp": 1}
    identity[field] = value
    with pytest.raises(RuntimeError, match="privilege"):
        probe["validate_identity"](identity)


def test_native_podman_runtime_inspection_overrides_compatibility_field(probe):
    with pytest.raises(RuntimeError, match="runsc"):
        probe["require_runsc"]({"OCIRuntime": "runc", "HostConfig": {"Runtime": "runsc"}})


def test_native_podman_uses_service_runtime_and_checks_nonroot_identity(probe, monkeypatch):
    calls = []
    identity = {"uid": 1000, "gid": 1000, "cap_eff": 0, "cap_bnd": 0,
                "cap_prm": 0, "cap_inh": 0, "nnp": 1}

    def fake_engine(*args, **kwargs):
        calls.append(args)
        output = ""
        if args[0] == "info":
            output = "false"
        elif args[0] == "create":
            assert not any(arg.startswith("--runtime") for arg in args)
            assert "--user=1000:1000" in args
            assert "--cap-drop=ALL" in args
        elif args[0] == "inspect":
            output = json.dumps([{"OCIRuntime": "runsc", "HostConfig": {"Runtime": "oci"},
                                  "Image": "sha256:fixture", "State": {"Pid": 123}}])
        elif args[0] == "exec":
            output = json.dumps(identity)
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setitem(probe["probe"].__globals__, "docker", fake_engine)
    monkeypatch.setitem(probe["probe"].__globals__, "inspect_limits", lambda pid: {"verified_pid": pid})
    result = probe["probe"]("trusted-python-image", mode="rootful", engine="podman")
    assert result["runtime"] == "runsc"
    assert result["workload_identity"] == identity
    assert result["cleanup_confirmed"]
    assert calls[-1][0] == "ps"
