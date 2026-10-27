#!/usr/bin/env python3
"""Create protected local NATS users and server configs once per environment."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROLES = ("api", "worker", "relay", "operator")


def role_permissions(role: str) -> dict | None:
    if role == "api":
        return {
            "publish": {
                "allow": [
                    "openship.*.v1.command.>",
                    "openship.*.v1.control.>",
                    "openship.*.v1.rpc.worker.health",
                    "$JS.API.STREAM.INFO.OS_EVENTS",
                    "$JS.API.CONSUMER.CREATE.OS_EVENTS.>",
                    "$JS.API.CONSUMER.DELETE.OS_EVENTS.>",
                    "$JS.ACK.>",
                ]
            },
            "subscribe": {
                "allow": [
                    "openship.*.v1.event.>",
                    "openship.*.v1.reply.>",
                    "openship.*.v1.inbox.api.>",
                    "_INBOX.>",
                ]
            },
        }
    if role == "worker":
        return {
            "publish": {
                "allow": [
                    "$JS.API.STREAM.INFO.OS_RUNS",
                    "$JS.API.STREAM.INFO.OS_MAINTENANCE",
                    "$JS.API.STREAM.INFO.OS_EVENTS",
                    "$JS.API.STREAM.INFO.OS_CONTROLS",
                    "$JS.API.STREAM.INFO.OS_FAILURES",
                    "$JS.API.STREAM.INFO.OS_ADVISORIES",
                    "$JS.API.CONSUMER.INFO.>",
                    "$JS.API.CONSUMER.MSG.NEXT.OS_RUNS.run-v1",
                    "$JS.API.CONSUMER.MSG.NEXT.OS_MAINTENANCE.maintenance-v1",
                    "$JS.API.CONSUMER.MSG.NEXT.OS_ADVISORIES.exhaustion-v1",
                    "$JS.API.STREAM.MSG.GET.OS_RUNS",
                    "$JS.API.STREAM.MSG.DELETE.OS_RUNS",
                    "$JS.ACK.>",
                    "openship.*.v1.reply.>",
                ]
            },
            "subscribe": {
                "allow": [
                    "openship.*.v1.command.>",
                    "openship.*.v1.rpc.worker.health",
                    "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_RUNS.run-v1",
                    "_INBOX.>",
                ]
            },
        }
    if role == "relay":
        return {
            "publish": {"allow": ["openship.*.v1.>"]},
            "subscribe": {"allow": ["_INBOX.>"]},
        }
    return None


def _user(role: str, password: str) -> dict:
    user = {"user": f"openship_{role}", "password": password}
    permissions = role_permissions(role)
    if permissions is not None:
        user["permissions"] = permissions
    return user


def make_configs(
    credentials: dict[str, dict[str, str]], target: Path, tls: bool
) -> dict:
    users = [_user(role, credentials[role]["password"]) for role in ROLES]
    common = {
        "authorization": {"timeout": 2, "users": users},
        "jetstream": {
            "store_dir": "/data/jetstream",
            "max_file_store": 2 * 1024 * 1024 * 1024,
            "max_mem_store": 256 * 1024 * 1024,
        },
    }
    local = {
        **common,
        "listen": "127.0.0.1:4222",
        "jetstream": {
            **common["jetstream"],
            "store_dir": str((target / "natsdata").resolve()),
        },
    }
    compose = {**common, "listen": "0.0.0.0:4222"}
    if tls:
        compose["tls"] = {
            "cert_file": "/run/nats-tls/server.crt",
            "key_file": "/run/nats-tls/server.key",
            "timeout": 2,
        }
        local["tls"] = {
            "cert_file": str((target / "server.crt").resolve()),
            "key_file": str((target / "server.key").resolve()),
            "timeout": 2,
        }
        compose["jetstream"] = {
            **compose["jetstream"],
            "sync_interval": "always",
        }
        local["jetstream"] = {
            **local["jetstream"],
            "sync_interval": "always",
        }
    return {"server.compose.json": compose, "server.local.json": local}


def initialize(
    target: Path, *, tls_cert: Path | None, tls_key: Path | None, tls_ca: Path | None
) -> None:
    tls_paths = (tls_cert, tls_key, tls_ca)
    if any(tls_paths) and not all(tls_paths):
        raise ValueError("TLS setup requires --tls-cert, --tls-key, and --tls-ca")
    tls = all(tls_paths)
    if tls and any(not path.is_file() for path in tls_paths if path is not None):
        raise ValueError("every supplied TLS path must name a readable file")
    tls_material = {}
    if tls_cert is not None and tls_key is not None and tls_ca is not None:
        tls_material = {
            "ca.crt": tls_ca.read_bytes(),
            "server.crt": tls_cert.read_bytes(),
            "server.key": tls_key.read_bytes(),
        }

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    credentials = {
        role: {
            "user": f"openship_{role}",
            "password": secrets.token_urlsafe(48),
        }
        for role in ROLES
    }
    outputs: dict[str, bytes] = {
        f"{role}.json": (json.dumps(credentials[role], sort_keys=True) + "\n").encode()
        for role in ROLES
    }
    configs = make_configs(credentials, target, tls)
    outputs.update(
        {
            name: (json.dumps(config, indent=2, sort_keys=True) + "\n").encode()
            for name, config in configs.items()
        }
    )
    outputs.update(tls_material)

    target.mkdir(mode=0o700)
    os.chmod(target, 0o700)
    created_files = []
    try:
        for name, content in outputs.items():
            if not content:
                continue
            path = target / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_files.append(path)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            os.chmod(path, 0o600)
    except BaseException:
        for path in created_files:
            path.unlink(missing_ok=True)
        target.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-ca", type=Path)
    args = parser.parse_args()
    try:
        initialize(
            args.directory.expanduser().resolve(),
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            tls_ca=args.tls_ca,
        )
    except (FileExistsError, ValueError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(f"Created protected NATS configuration in {args.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
