"""Run required JetStream topology checks against a disposable pinned server."""

# NATS startup failures wrap transport errors differently by platform; retry all
# connection exceptions until the readiness deadline.
# ruff: noqa: BLE001

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import nats

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = API_ROOT / "tests/integration/test_messaging_topology.py"


def _load_versions() -> dict[str, str]:
    values = {}
    for line in (REPOSITORY_ROOT / "deploy/versions.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", maxsplit=1)
            values[key] = value
    return values


def _runtime() -> str:
    configured = os.environ.get("CONTAINER_RUNTIME")
    if configured:
        executable = shutil.which(configured)
        if executable is None:
            raise RuntimeError(f"CONTAINER_RUNTIME executable not found: {configured}")
        return executable
    for candidate in ("docker", "podman"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    raise RuntimeError("Docker or Podman is required for JetStream qualification")


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _write_private(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o600)


async def _wait_for_server(url: str, user: str, password: str) -> None:
    # This credential is generated for this disposable local server and is
    # removed with its private temporary directory when the runner exits.
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client = await nats.connect(
                url,
                user=user,
                password=password,  # rayloc:ignore
                connect_timeout=1,
                name="openship-jetstream-qualification-readiness",
            )
            await client.close()
            return
        except Exception as error:
            last_error = error
            await asyncio.sleep(0.25)
    raise RuntimeError("pinned NATS server did not become ready") from last_error


def _check_junit(path: Path) -> None:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    failures = sum(
        int(suite.attrib.get("failures", 0)) + int(suite.attrib.get("errors", 0))
        for suite in suites
    )
    if tests == 0 or skipped or failures:
        raise RuntimeError(
            "JetStream qualification must run at least one test without skips "
            f"or failures (tests={tests}, skipped={skipped}, failures={failures})"
        )
    print(f"JetStream qualification: {tests} tests, no skips")


def main() -> int:
    versions = _load_versions()
    image = versions["NATS_SERVER_IMAGE"]
    runtime = _runtime()
    container_name = f"openship-jetstream-qual-{uuid.uuid4().hex[:12]}"
    port = _port()

    with tempfile.TemporaryDirectory(prefix="openship-jetstream-qual-") as temp_name:
        temp_dir = Path(temp_name)
        temp_dir.chmod(0o700)
        store_dir = temp_dir / "store"
        store_dir.mkdir(mode=0o700)
        auth = {
            "admin": {
                "user": "admin_" + secrets.token_urlsafe(8),
                "password": secrets.token_urlsafe(32),
            },
            "restricted": {
                "user": "limited_" + secrets.token_urlsafe(8),
                "password": secrets.token_urlsafe(32),
            },
        }
        auth_file = temp_dir / "auth.json"
        config_file = temp_dir / "nats.conf"
        result_file = temp_dir / "results.xml"
        _write_private(auth_file, json.dumps(auth))
        config = f'''\
listen: 0.0.0.0:4222
jetstream {{
  store_dir: "/data"
  max_mem_store: 67108864
  max_file_store: 536870912
  sync_interval: 100ms
}}
authorization {{
  users: [
    {{
      user: "{auth["admin"]["user"]}"
      password: "{auth["admin"]["password"]}"
      permissions: {{ publish: [">"], subscribe: [">"] }}
    }}
    {{
      user: "{auth["restricted"]["user"]}"
      password: "{auth["restricted"]["password"]}"
      permissions: {{ publish: ["openship.test.>"], subscribe: ["_INBOX.>"] }}
    }}
  ]
}}
'''
        _write_private(config_file, config)

        volume_suffix = ",Z" if Path(runtime).name == "podman" and sys.platform == "linux" else ""
        command = [
            runtime,
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--publish",
            f"127.0.0.1:{port}:4222",
            "--volume",
            f"{config_file}:/etc/nats/nats.conf:ro{volume_suffix}",
            "--volume",
            f"{store_dir}:/data{volume_suffix}",
        ]
        if Path(runtime).name != "podman":
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        command.extend(
            [
            image,
            "-c",
            "/etc/nats/nats.conf",
            ]
        )
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        url = f"nats://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment["TEST_NATS_URL"] = url
        environment["TEST_NATS_AUTH_FILE"] = str(auth_file)
        environment["TEST_NATS_SERVER_CONFIG"] = str(config_file)
        try:
            asyncio.run(
                _wait_for_server(url, auth["admin"]["user"], auth["admin"]["password"])
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-vv",
                    "-ra",
                    "--junitxml",
                    str(result_file),
                    "-m",
                    "jetstream",
                    str(TEST_PATH),
                ],
                cwd=API_ROOT,
                env=environment,
                check=False,
            )
            if result.returncode != 0:
                return result.returncode
            _check_junit(result_file)
            return 0
        finally:
            subprocess.run(
                [runtime, "stop", "--time", "5", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
