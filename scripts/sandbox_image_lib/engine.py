"""Container-engine adapter for the explicitly selected CLI and socket.

Every operation — info, inspect, build, run — goes through one prefix so the
sandbox manager's actual image store is the one consulted, never the
executable's implicit default connection.
"""

import json
import subprocess
import uuid

from .catalog import ImageError


class Engine:
    def __init__(self, cli: str, endpoint: str, platform: str, host_timeout_s: float = 35.0):
        endpoint = endpoint.strip()
        if endpoint.startswith("unix://"):
            path = endpoint[len("unix://") :]
        elif endpoint.startswith("/"):
            path = endpoint
            endpoint = "unix://" + path
        else:
            raise ImageError(
                "only a unix socket endpoint is supported; pass unix://<path> or an absolute socket path"
            )
        if not path.startswith("/"):
            raise ImageError("engine socket path must be absolute")
        self.cli = cli
        self.endpoint = f"unix://{path}"
        self.platform = platform
        self.host_timeout_s = host_timeout_s

    @property
    def identity(self) -> str:
        return f"{self.cli}@{self.endpoint}@{self.platform}"

    def _prefix(self) -> list:
        if self.cli == "podman":
            return ["podman", "--remote", "--url", self.endpoint]
        return [self.cli, "--host", self.endpoint]

    def _run(self, args: list, timeout: float | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                self._prefix() + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise ImageError(f"engine executable is not usable: {self.cli}") from exc

    def info(self) -> subprocess.CompletedProcess:
        try:
            res = self._run(["info", "--format", "{{json .}}"], timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise ImageError("selected engine did not answer info; check the socket and daemon") from exc
        if res.returncode != 0:
            raise ImageError("selected engine is not reachable; check OPENSHIP_SANDBOX_ENGINE_SOCKET")
        try:
            data = json.loads(res.stdout)
            if self.cli == "podman":
                arch, operating_system = data["host"]["arch"], data["host"]["os"]
            else:
                arch, operating_system = data["Architecture"], data["OSType"]
            if arch not in ("x86_64", "amd64") or operating_system != "linux":
                raise ImageError("selected engine must serve linux/amd64")
        except (ValueError, TypeError, KeyError) as exc:
            raise ImageError("selected engine metadata does not match the requested Docker/Podman engine") from exc
        return res

    def inspect_image(self, reference: str) -> dict | None:
        res = self._run(["inspect", reference])
        if res.returncode != 0:
            return None
        try:
            entries = json.loads(res.stdout)
        except ValueError:
            return None
        if not entries:
            return None
        entry = dict(entries[0])
        entry["Tag"] = _strip_localhost(entry.get("Tag", reference))
        for key in ("tag",):
            if key in entry:
                entry[key] = _strip_localhost(entry[key])
        if "RepoTags" in entry:
            entry["RepoTags"] = [_strip_localhost(tag) for tag in entry["RepoTags"]]
        return entry

    def build(self, dockerfile, context, tag: str, build_args: dict, labels: dict) -> str:
        args = ["build", "--platform", self.platform, "-f", str(dockerfile), "-t", tag]
        args += [element for key, value in build_args.items() for element in ("--build-arg", f"{key}={value}")]
        args += [element for key, value in labels.items() for element in ("--label", f"{key}={value}")]
        args.append(str(context))
        res = self._run(args)
        if res.returncode != 0:
            raise ImageError(f"image build for tag {tag} failed (exit {res.returncode}); no local success record was saved")
        inspected = self.inspect_image(tag)
        if inspected is None:
            raise ImageError(f"built image for tag {tag} is not in the selected engine's store")
        return inspected["Id"]

    def smoke(self, image_id: str) -> subprocess.CompletedProcess:
        name = f"openship-image-smoke-{uuid.uuid4().hex}"
        args = [
            "run", "--rm",
            "--network", "none",
            "--user", "1000:1000",
            "--cpus", "1",
            "--memory", "512m",
            "--pids-limit", "128",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--name", name,
            image_id,
            "timeout", "--signal=TERM", "--kill-after=2", "30s",
            "python", "/opt/openship/smoke.py",
        ]
        try:
            res = self._run(args, timeout=self.host_timeout_s)
        except subprocess.TimeoutExpired:
            self._run(["rm", "-f", name])
            raise ImageError(f"smoke check for image {image_id} exceeded its host deadline")
        if res.returncode != 0:
            self._run(["rm", "-f", name])
        return res


def _strip_localhost(value: str) -> str:
    # Podman qualifies local names with a localhost/ registry prefix; stripping
    # it only at the exact prefix position keeps digest-bearing names intact.
    if value.startswith("localhost/"):
        return value[len("localhost/"):]
    return value
