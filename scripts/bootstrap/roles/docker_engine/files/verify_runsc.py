#!/usr/bin/env python3
"""Trusted Docker/runsc startup and cgroup probe; not isolation qualification."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def docker(*args, check=True):
    endpoint = os.environ.get("DOCKER_HOST", "")
    if not endpoint.startswith("unix://"):
        raise RuntimeError("set an explicit engine Unix socket")
    if os.environ.get("OPENSHIP_PROBE_ENGINE", "docker") == "podman":
        prefix = ["podman", "--remote", "--url", endpoint]
    else:
        prefix = ["docker", "--host", endpoint]
    return subprocess.run(
        [*prefix, *args],
        capture_output=True, text=True, timeout=120, check=check,
    )


def require_runsc(details, expected="runsc"):
    actual = details.get("OCIRuntime", details.get("HostConfig", {}).get("Runtime"))
    if actual != expected:
        raise RuntimeError("expected runsc; refusing runtime fallback")


def validate_identity(identity):
    expected = {"uid": 1000, "gid": 1000, "cap_eff": 0, "cap_bnd": 0,
                "cap_prm": 0, "cap_inh": 0, "nnp": 1}
    if any(identity.get(key) != value for key, value in expected.items()):
        raise RuntimeError("workload privilege requirements failed")


IDENTITY_SOURCE = (
    "import ctypes,json,os; "
    "s=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line); "
    "v={k:int(s[n].strip(),16) for k,n in "
    "[('cap_eff','CapEff'),('cap_bnd','CapBnd'),('cap_prm','CapPrm'),('cap_inh','CapInh')]}; "
    "v.update(uid=os.getuid(),gid=os.getgid(),nnp=ctypes.CDLL(None).prctl(39,0,0,0,0)); "
    "print(json.dumps(v))"
)


def inspect_limits(pid, proc_root=Path("/proc"), group_root=Path("/sys/fs/cgroup")):
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("missing runtime process PID")
    lines = (proc_root / str(pid) / "cgroup").read_text().splitlines()
    paths = [line[3:] for line in lines if line.startswith("0::")]
    if len(paths) != 1 or not paths[0].startswith("/") or ".." in Path(paths[0]).parts:
        raise RuntimeError("expected a host cgroup v2 path")
    root = group_root.resolve()
    leaf = (root / paths[0].lstrip("/")).resolve()
    if not leaf.is_relative_to(root):
        raise RuntimeError("runtime cgroup is outside the host mount")
    values = {key: (leaf / key).read_text().strip() for key in (
        "memory.max", "memory.swap.max", "cpu.max", "pids.max",
    )}
    expected = {"memory.max": "536870912", "memory.swap.max": "0", "pids.max": "128"}
    if any(values[key] != value for key, value in expected.items()):
        raise RuntimeError("requested memory/swap/task limit is absent or different")
    quota, period = values["cpu.max"].split()
    if quota == "max" or int(quota) <= 0 or int(quota) != int(period):
        raise RuntimeError("requested one-CPU limit is absent or different")
    return values


def probe(image, *, mode="rootless", engine="docker", runtime="runsc"):
    if engine == "podman":
        rootless_value = docker("info", "--format", "{{.Host.Security.Rootless}}").stdout.strip()
        if rootless_value not in ("true", "false"):
            raise RuntimeError("engine privilege mode is unknown")
        rootless = rootless_value == "true"
    else:
        security = json.loads(docker("info", "--format", "{{json .SecurityOptions}}").stdout)
        rootless = "name=rootless" in security
    if rootless != (mode == "rootless"):
        raise RuntimeError(f"expected a {mode} engine; refusing the other privilege mode")
    name = "openship-docker-runsc-probe-" + uuid4().hex
    receipt = {"image": image, "engine": engine, "mode": mode, "isolation_qualified": False}
    if docker("image", "inspect", image, check=False).returncode:
        docker("pull", image)
    try:
        runtime_args = ["--runtime=" + runtime] if engine == "docker" else []
        docker(
            "create", "--name", name, *runtime_args, "--network=none",
            "--label", "openship.bootstrap.probe=" + name,
            "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=128",
            image, "python3", "-c", "import time; time.sleep(90)",
        )
        details = json.loads(docker("inspect", name).stdout)[0]
        require_runsc(details, runtime)
        docker("start", name)
        details = json.loads(docker("inspect", name).stdout)[0]
        require_runsc(details, runtime)
        receipt.update({
            "runtime": details.get("OCIRuntime", details["HostConfig"].get("Runtime")),
            "image_id": details["Image"],
            "cgroup_limits": inspect_limits(details["State"]["Pid"]),
        })
        identity = json.loads(docker("exec", name, "python3", "-I", "-B", "-c", IDENTITY_SOURCE).stdout)
        validate_identity(identity)
        receipt["workload_identity"] = identity
    finally:
        removed = docker("rm", "--force", name, check=False)
        remaining = docker("ps", "--all", "--filter", "label=openship.bootstrap.probe=" + name, "--format", "{{.ID}}")
        if remaining.stdout.strip():
            raise RuntimeError("probe cleanup unconfirmed; container remains")
        receipt["cleanup_confirmed"] = True
        receipt["remove_exit"] = removed.returncode
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="explicit engine Unix socket")
    parser.add_argument("--engine", choices=("docker", "podman"), default="docker")
    parser.add_argument("--mode", choices=("rootful", "rootless"), default="rootless")
    parser.add_argument("--runtime", choices=("runsc", "io.containerd.runsc.v1"), default="runsc")
    parser.add_argument("--image", required=True, help="trusted Python image; pulled if absent")
    args = parser.parse_args()
    os.environ["DOCKER_HOST"] = args.host
    os.environ["OPENSHIP_PROBE_ENGINE"] = args.engine
    try:
        result = probe(args.image, mode=args.mode, engine=args.engine, runtime=args.runtime)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Docker/runsc probe failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr[-4096:].strip(), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
