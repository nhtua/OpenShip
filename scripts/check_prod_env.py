#!/usr/bin/env python3
"""Validate required production inputs without displaying their values."""

from __future__ import annotations

import argparse
import re
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID


def parse_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def validate(path: Path) -> list[str]:
    errors = []
    if not path.is_file():
        return ["missing .env.prod"]
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        errors.append(
            ".env.prod must be accessible only to its owner (mode 0600 or 0400)"
        )
    try:
        values = parse_values(path)
    except OSError:
        return [".env.prod could not be read"]
    versions_file = Path(__file__).resolve().parents[1] / "deploy" / "versions.env"
    try:
        pinned_versions = parse_values(versions_file)
    except OSError:
        pinned_versions = {}

    required = (
        "OPENSHIP_API_IMAGE",
        "OPENSHIP_WEB_IMAGE",
        "POSTGRES_IMAGE",
        "NATS_SERVER_IMAGE",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "JWT_SECRET",
        "ARTIFACT_STORAGE_ID",
        "CONNECTOR_BROWSER_ORIGINS",
    )
    missing = [key for key in required if not values.get(key)]
    if missing:
        errors.append("missing required settings: " + ", ".join(missing))
    for key in (
        "OPENSHIP_API_IMAGE",
        "OPENSHIP_WEB_IMAGE",
        "POSTGRES_IMAGE",
        "NATS_SERVER_IMAGE",
    ):
        value = values.get(key, "")
        if value and not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", value):
            errors.append(f"{key} must be an immutable sha256 image reference")
    pinned_nats_image = pinned_versions.get("NATS_SERVER_IMAGE")
    if pinned_nats_image and values.get("NATS_SERVER_IMAGE") != pinned_nats_image:
        errors.append(
            "NATS_SERVER_IMAGE must match the qualified pin in deploy/versions.env"
        )
    for key in ("POSTGRES_PASSWORD", "JWT_SECRET"):
        value = values.get(key, "")
        if len(value) < 32 or value.lower().startswith(
            ("change", "replace", "example")
        ):
            errors.append(f"{key} must be a supplied secret of at least 32 characters")
    storage_id = values.get("ARTIFACT_STORAGE_ID", "")
    try:
        UUID(storage_id)
    except ValueError:
        if storage_id:
            errors.append("ARTIFACT_STORAGE_ID must be a UUID")
    database_url = values.get("DATABASE_URL", "")
    if database_url:
        try:
            parsed = urlsplit(database_url)
            database_name = parsed.path.lstrip("/")
            if (
                parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg2"}
                or parsed.port not in (None, 5432)
                or parsed.hostname != "postgres"
                or unquote(parsed.username or "")
                != values.get("POSTGRES_USER", "openship")
                or unquote(parsed.password or "") != values.get("POSTGRES_PASSWORD", "")
                or database_name != values.get("POSTGRES_DB", "openship")
            ):
                errors.append(
                    "DATABASE_URL must match the Compose PostgreSQL user, password, host, and database"
                )
        except ValueError:
            errors.append("DATABASE_URL must be a valid PostgreSQL URL")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=Path(".env.prod"))
    args = parser.parse_args()
    errors = validate(args.file)
    if errors:
        for error in errors:
            print(f"configuration error: {error}", file=sys.stderr)
        return 1
    print("Production configuration checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
