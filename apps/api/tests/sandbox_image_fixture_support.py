"""Fixture scaffolding shared by the sandbox-image unit suites."""

import json
import os
import socket
from pathlib import Path


def build_catalog(tmp_path, monkeypatch) -> dict:
    """A complete temporary repository whose images.json declares two profiles.

    The fake executables named docker/podman stand in for the container CLI
    only at the subprocess boundary: they record every invocation to
    engine-calls.log and answer with canned JSON, so dispatch assertions run
    against real command construction and no container ever starts.
    """
    root = tmp_path / "repo"
    sandbox = root / "deploy" / "sandbox"

    def write(rel: str, body: str) -> Path:
        path = sandbox / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    write("Dockerfile.base", "FROM digest-pinned base\n")
    write("install_pinned.py", "# pinned shared installer\n")
    write("build-requirements.in", "# build prerequisites\n")
    write("build-requirements.lock", "# generated\n")
    write(
        "pins.json",
        json.dumps(
            {
                "python_base_image": "python:3.12-slim@sha256:" + "a" * 64,
                "uv_image": "uv@sha256:" + "b" * 64,
                "python_version": "3.12",
                "uv_version": "0.12.23",
                "supported_platforms": ["linux/amd64"],
                "debian_snapshot": "2026-11-01T00:00:00Z",
            }
        ),
    )
    for profile_id in ("logs-data", "standard"):
        write(f"profiles/{profile_id}/Dockerfile", f"FROM WORKLOAD_BASE_IMAGE\n# {profile_id}\n")
        write(f"profiles/{profile_id}/install.sh", "# pinned tool installation helper\n")
        write(f"profiles/{profile_id}/requirements.in", "sympy\nregex\nmpmath\n")
        write(
            f"profiles/{profile_id}/requirements.lock",
            f"# generated lock for {profile_id}\n",
        )
        write(f"profiles/{profile_id}/os-packages.lock", f"# os packages for {profile_id}\n")
        write(f"profiles/{profile_id}/smoke.py", f"# smoke for {profile_id}\n")
    write(
        "tools.lock.json",
        json.dumps(
            {
                "schema_version": 1,
                "tools": {
                    "node": {
                        "linux/amd64": {
                            "version": "20.0.0",
                            "url": "https://fixtures.test/node/v20.tar.gz",
                            "sha256": "c" * 64,
                        }
                    }
                },
            }
        ),
    )
    images = {
        "schema_version": 1,
        "purpose": "fixture inventory keeping legacy keys",
        "python_base_image": "python:3.11-slim@sha256:" + "0" * 64,
        "workload_dockerfile": "deploy/sandbox/Dockerfile.python",
        "managed_base": {
            "dockerfile": "deploy/sandbox/Dockerfile.base",
            "pins": "deploy/sandbox/pins.json",
            "build_requirements_in": "deploy/sandbox/build-requirements.in",
            "build_requirements_lock": "deploy/sandbox/build-requirements.lock",
            "install_helper": "deploy/sandbox/install_pinned.py",
        },
        "managed_profiles": {
            profile_id: {
                "dockerfile": f"deploy/sandbox/profiles/{profile_id}/Dockerfile",
                "requirements_in": f"deploy/sandbox/profiles/{profile_id}/requirements.in",
                "requirements_lock": f"deploy/sandbox/profiles/{profile_id}/requirements.lock",
                "os_packages_lock": f"deploy/sandbox/profiles/{profile_id}/os-packages.lock",
                "smoke": f"deploy/sandbox/profiles/{profile_id}/smoke.py",
                "install_helper": f"deploy/sandbox/profiles/{profile_id}/install.sh",
                "extra_inputs": [],
                "tools": ["node"] if profile_id == "standard" else [],
            }
            for profile_id in ("logs-data", "standard")
        },
    }
    (root / "deploy/sandbox").mkdir(parents=True, exist_ok=True)
    (root / "deploy/sandbox" / "images.json").write_text(json.dumps(images, indent=2))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "engine-calls.log"
    for name in ("docker", "podman"):
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "{name} $*" >> "{log}"\n'
            "sub=''\n"
            'for arg in "$@"; do\n'
            "  case \"$arg\" in\n"
            "    info|inspect|build|run|rm) sub=$arg; break;;\n"
            "  esac\n"
            "done\n"
            'if [ -n "${FAKE_SUB_OVERRIDE:-}" ]; then sub="$FAKE_SUB_OVERRIDE"; fi\n'
            'case "$sub" in\n'
            '  info)\n'
            '    if [ -n "${FAKE_INFO_JSON:-}" ]; then printf \'%s\\n\' "$FAKE_INFO_JSON"; '
            f'elif [ "{name}" = podman ]; then printf \'%s\\n\' \'{{"host":{{"arch":"amd64","os":"linux"}}}}\'; '
            'else printf \'%s\\n\' \'{"Architecture":"amd64","OSType":"linux"}\'; fi\n'
            '    exit "${FAKE_INFO_RC:-0}";;\n'
            "  inspect)\n"
            '    if [ -n "${FAKE_INSPECT_JSON:-}" ]; then printf \'%s\\n\' "$FAKE_INSPECT_JSON"; '
            "else printf '%s\\n' '[{\"Id\": \"IMAGEID-FROMCANNED\", \"Machine\": \"x86_64\"}]'; fi\n"
            "    exit 0;;\n"
            "  build)\n"
            "    last=''\n"
            '    for arg in "$@"; do last="$arg"; done\n'
            '    if [ -d "$last" ]; then find "$last" -type f | sed "s|$last/||" '
            f'>> "{log}"; fi\n'
            "    exit \"${FAKE_BUILD_RC:-0}\";;\n"
            "  run)\n"
            '    sleep "${FAKE_RUN_SLEEP:-0}"\n'
            "    exit \"${FAKE_RUN_RC:-0}\";;\n"
            "  *) exit 0;;\n"
            "esac\n"
        )
        script.chmod(0o755)
    monkeypatch.setitem(os.environ, "PATH", f"{bin_dir}:{os.environ['PATH']}")
    socket_path = tmp_path / "manager.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(socket_path))
    monkeypatch.setitem(os.environ, "OPENSHIP_SANDBOX_ENGINE_SOCKET", str(socket_path))
    return {
        "root": root,
        "sandbox": sandbox,
        "images": images,
        "log": log,
        "bin": bin_dir,
    }
