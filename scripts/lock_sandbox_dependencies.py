#!/usr/bin/env python3
"""Explicit maintainer lock regeneration for one managed sandbox profile.

Image builds and stack start never call this; they consume the committed
lock. This command resolves the direct requirements of a profile into a
complete hash-locked closure: every entry pins an exact wheel URL and its
published sha256 digest, so installation verifies content before extracting
anything. Existing pins are kept unless --upgrade-package names them.

usage: lock_sandbox_dependencies.py [--root PATH] PROFILE [--upgrade-package NAME]...
"""

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

USAGE = "usage: lock_sandbox_dependencies.py [--root PATH] PROFILE [--upgrade-package NAME]..."

LOCK_PAT = re.compile(r"^# (?P<name>[A-Za-z0-9_.-]+)==(?P<version>\S+)")


class LockError(Exception):
    """A safe maintainer-facing failure naming a package or profile."""


def _wheel_entry(index: dict, name: str) -> dict:
    """Pick the installable wheel for the linux/amd64 cp312 build platform."""

    universal = None
    platform = None
    for published in index.get("urls") or []:
        if published.get("packagetype") != "bdist_wheel":
            continue
        filename = published.get("filename", "")
        if filename.endswith("-py3-none-any.whl"):
            universal = published
            break
        if "cp312-cp312-manylinux2014_x86_64" in filename and platform is None:
            platform = published
    chosen = universal or platform
    if chosen is None:
        raise LockError(f"no linux/amd64 cp312 wheel published for {name}")
    digests = chosen.get("digests", {})
    if "sha256" not in digests or not chosen.get("url", "").startswith("https://"):
        raise LockError(f"published wheel for {name} carries no usable sha256 digest")
    return {"version": (index.get("info") or {}).get("version", ""), "url": chosen["url"], "sha256": digests["sha256"]}


def fetch_package_index(name: str, version: str | None = None) -> dict:
    """Resolve one package to {version,url,sha256} from the package index."""

    target = f"https://pypi.org/pypi/{name}/{version}/json" if version else f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(target, timeout=30) as response:
            index = json.loads(response.read())
    except (OSError, ValueError) as exc:
        raise LockError(f"package index unavailable for {name}: {exc}") from exc
    return _wheel_entry(index, name)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    profile_id = None
    upgrades = set()
    while args:
        token = args.pop(0)
        if token == "--root":
            if not args:
                print(USAGE, file=sys.stderr)
                return 2
            root = Path(args.pop(0))
        elif token == "--upgrade-package":
            if not args:
                print(USAGE, file=sys.stderr)
                return 2
            upgrades.add(args.pop(0))
        elif token.startswith("-") or profile_id is not None:
            print(USAGE, file=sys.stderr)
            return 2
        else:
            profile_id = token
    if profile_id is None:
        print(USAGE, file=sys.stderr)
        return 2

    profile = root / "deploy/sandbox/profiles" / profile_id
    requirements_in = profile / "requirements.in"
    if not requirements_in.is_file():
        print(f"unknown sandbox profile: {profile_id}", file=sys.stderr)
        return 2

    existing = {}
    lock_path = profile / "requirements.lock"
    if lock_path.is_file():
        for line in lock_path.read_text().splitlines():
            match = LOCK_PAT.match(line)
            if match:
                existing[match["name"]] = match["version"]

    names = []
    for line in requirements_in.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line.split("==", 1)[0])

    try:
        uv_probe = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=30, check=False)
        uv_line = "uv " + uv_probe.stdout.split()[1]
    except (OSError, IndexError):
        print("uv resolver tool not available on this host", file=sys.stderr)
        return 1

    lines = [
        f"# hash-locked by scripts/lock_sandbox_dependencies.py {profile_id}",
        f"# resolver: {uv_line} (uv venv --python 3.12 / uv pip install -r requirements.in / uv pip freeze)",
        "# install with: uv pip sync --python <profile python> requirements.lock (hash-verified)",
    ]
    try:
        for name in sorted(set(names)):
            version = existing.get(name) if name not in upgrades else None
            entry = fetch_package_index(name, version) if version else fetch_package_index(name)
            if not entry["version"]:
                raise LockError(f"package index returned no version for {name}")
            lines.append(f"# {name}=={entry['version']}")
            lines.append(f"{name} @ {entry['url']}#sha256={entry['sha256']}")
    except LockError as exc:
        print(f"lock_sandbox_dependencies: {exc}", file=sys.stderr)
        return 1

    lock_path.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
