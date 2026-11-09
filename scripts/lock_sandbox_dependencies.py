#!/usr/bin/env python3
"""Explicit, atomic lock regeneration using the pinned uv resolver.

Build/start never invoke this command. Resolution runs for Linux x86_64 and
Python 3.12, with only the committed build prerequisites available to sdists.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("profile")
    parser.add_argument("--upgrade-package", action="append", default=[])
    args = parser.parse_args(argv)
    sandbox = args.root.resolve() / "deploy/sandbox"
    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.profile):
        return 2
    directory = sandbox / "profiles" / args.profile
    if not (directory / "requirements.in").is_file():
        print(f"unknown sandbox profile: {args.profile}", file=sys.stderr)
        return 2
    try:
        pins = json.loads((sandbox / "pins.json").read_text())
        version = _run(["uv", "--version"]).stdout.split()[1]
        if version != pins["uv_version"]:
            print(f"lock generation requires uv {pins['uv_version']}", file=sys.stderr)
            return 1
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
            print("lock generation requires linux/amd64", file=sys.stderr)
            return 1
        locks = ["requirements"] + (["azure-cli"] if (directory / "azure-cli.in").is_file() else [])
        generated = {}
        with tempfile.TemporaryDirectory(prefix="openship-lock-") as temporary:
            work = Path(temporary)
            python = work / "venv/bin/python"
            _run(["uv", "venv", str(work / "venv"), "--python", pins["python_version"]])
            _run([
                "uv", "pip", "sync", "--python", str(python), "--require-hashes",
                str(sandbox / "build-requirements.lock"),
            ])
            for name in locks:
                source = f"{name}.in"
                output = f"{name}.lock"
                shutil.copyfile(directory / source, work / source)
                if (directory / output).exists():
                    old = (directory / output).read_text()
                    # Migrate the early URL-lock format without silently upgrading pins.
                    if "# hash-locked by scripts/lock_sandbox_dependencies.py" in old:
                        old = "\n".join(re.findall(r"^# ([\w.-]+==\S+)$", old, re.MULTILINE)) + "\n"
                    (work / output).write_text(old)
                command = [
                    "uv", "pip", "compile", source, "--generate-hashes", "--no-build-isolation",
                    "--python", str(python), "--python-platform", "x86_64-manylinux_2_28",
                    "--output-file", output,
                    "--custom-compile-command",
                    (f"uv {version}: python3 scripts/lock_sandbox_dependencies.py {args.profile}"
                     " (pip compile --generate-hashes --no-build-isolation; Python 3.12 linux/amd64)"),
                ]
                for package in args.upgrade_package:
                    command += ["--upgrade-package", package]
                _run(command, cwd=work)
                generated[output] = (work / output).read_bytes()
        for output, content in generated.items():
            target = directory / output
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as staged:
                staged.write(content)
            os.replace(staged.name, target)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("lock generation failed; existing locks unchanged", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
