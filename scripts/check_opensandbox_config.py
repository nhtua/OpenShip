"""Check essential OpenSandbox settings; this is not an isolation qualification."""

import argparse
import os
import re
import sys
from pathlib import Path

import tomllib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        with args.config.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        print("Cannot read OpenSandbox configuration", file=sys.stderr)
        return 1

    runtime = config.get("runtime", {})
    secure = config.get("secure_runtime", {})
    server = config.get("server", {})
    docker = config.get("docker", {})
    execd_image = runtime.get("execd_image")
    if (
        runtime.get("type") != "docker"
        or runtime.get("execd_run_as_init") is not True
        or not isinstance(execd_image, str)
        or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", execd_image)
        or secure.get("type") != "gvisor"
        or secure.get("docker_runtime") != "runsc"
    ):
        print(
            "OpenSandbox requires digest-pinned execd-as-init with Docker and gVisor runsc",
            file=sys.stderr,
        )
        return 1
    pids_limit = docker.get("pids_limit")
    timeout_limit = server.get("max_sandbox_timeout_seconds")
    if (
        docker.get("network_mode") != "openship-sandbox-data"
        or docker.get("publish_host") != "127.0.0.1"
        or docker.get("no_new_privileges") is not True
        or docker.get("drop_capabilities") != ["ALL"]
        or docker.get("add_capabilities", []) != []
        or isinstance(pids_limit, bool)
        or not isinstance(pids_limit, int)
        or not 1 <= pids_limit <= 128
        or isinstance(timeout_limit, bool)
        or not isinstance(timeout_limit, int)
        or not 60 <= timeout_limit <= 120
    ):
        print("OpenSandbox sandbox profile is not bounded and isolated", file=sys.stderr)
        return 1
    key = os.environ.get("OPENSANDBOX_SERVER_API_KEY", server.get("api_key", ""))
    if not isinstance(key, str) or not key.strip():
        print("OpenSandbox API key is required", file=sys.stderr)
        return 1
    print("OpenSandbox baseline configuration is valid; runtime qualification is still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
