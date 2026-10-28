#!/usr/bin/env python3
"""Create the protected NATS CLI context imported by a Compose NATS NUI service."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def initialize_context(
    credentials_file: Path,
    context_dir: Path,
    server_url: str,
    profile: str,
) -> None:
    credentials_path = credentials_file.resolve(strict=True)
    if not credentials_path.is_file():
        raise ValueError("operator credentials must be a regular file")
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    if not isinstance(credentials, dict):
        raise ValueError("operator credentials have an invalid format")
    user = credentials.get("user")
    password = credentials.get("password")
    if (
        not isinstance(user, str)
        or not user
        or not isinstance(password, str)
        or not password
    ):
        raise ValueError("operator credentials have an invalid format")
    if profile not in {"local", "dev"}:
        raise ValueError("NUI profile must be local or dev")
    if server_url != "nats://nats:4222":
        raise ValueError("NUI context URL must target the Compose NATS service")

    context_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if context_dir.is_symlink() or not context_dir.is_dir():
        raise ValueError("NUI context directory must be a real directory")
    os.chmod(context_dir, 0o700)

    target = context_dir / f"openship-{profile}.json"
    if target.is_symlink():
        raise ValueError("NUI context file cannot be a symlink")
    payload = {
        "name": f"OpenShip {profile.title()}",
        "description": f"OpenShip {profile.title()} JetStream",
        "url": server_url,
        "user": user,
        # This forwards the operator secret from the protected NATS config file;
        # it is never a source-controlled default.
        "password": password,  # rayloc:ignore
    }
    temp_path = context_dir / f".openship-{profile}-{secrets.token_hex(8)}.tmp"
    try:
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, target)
        os.chmod(target, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--context-dir", type=Path, required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--profile", choices=("local", "dev"), default="local")
    args = parser.parse_args()
    try:
        initialize_context(args.credentials, args.context_dir, args.server_url, args.profile)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print("Prepared protected NATS NUI connection profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
