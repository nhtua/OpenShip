#!/usr/bin/env python3
"""Render and verify the OpenSandbox provider material for the dev stack.

Idempotent: existing credential or config material is verified, never rotated.
The API key and its Nginx map are written only into the protected secrets
directory with mode 0600, owned by the invoking host user (UID 1000), which
rootful Compose bind mounts keep numerically readable for the app UID 1000.
"""

import argparse
import json
import os
import secrets


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config-template", required=True)
    parser.add_argument("--config-output", required=True)
    parser.add_argument("--images-json", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--auth-map-file", required=True)
    parser.add_argument("--key-bytes", type=int, default=32)
    arguments = parser.parse_args()
    if arguments.key_bytes < 16:
        parser.error("--key-bytes must be at least 16")
    return arguments


def private_regular_file(path: str, label: str) -> bool:
    if os.path.exists(path) or os.path.islink(path):
        if not os.path.isfile(path):
            print(f"{label} is not a regular file: {path}")
            return False
    return True


def create_private_file(path: str, content: str) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        os.write(descriptor, content.encode())
    finally:
        os.close(descriptor)


def main() -> int:
    arguments = parse_arguments()
    key_path = arguments.key_file
    map_path = arguments.auth_map_file
    created: list[str] = []

    key_present = os.path.exists(key_path) or os.path.islink(key_path)
    map_present = os.path.exists(map_path) or os.path.islink(map_path)
    if key_present != map_present:
        print(
            "refusing to rotate only half of the gateway credential pair; "
            "restore the missing file or remove both deliberately"
        )
        return 2
    if not private_regular_file(key_path, "sandbox key") or not private_regular_file(
        map_path, "auth map"
    ):
        return 2

    if not key_present:
        secrets_directory = os.path.dirname(key_path)
        if not os.path.isdir(secrets_directory):
            os.mkdir(secrets_directory, 0o700)
        elif (os.stat(secrets_directory).st_mode & 0o777) != 0o700:
            print(f"secrets directory must be private: {secrets_directory}")
            return 2
        api_key = secrets.token_hex(arguments.key_bytes)
        create_private_file(key_path, api_key + "\n")
        create_private_file(map_path, '"~^' + api_key + '$" 1;\n')
        created.append(f"gateway key {key_path} and map {map_path}")

    output_path = arguments.config_output
    if os.path.exists(output_path) or os.path.islink(output_path):
        if not os.path.isfile(output_path):
            print(f"installation config is not a regular file: {output_path}")
            return 2
    else:
        images = json.load(open(arguments.images_json, encoding="utf-8"))
        placeholder = "REPLACE_WITH_PINNED_EXECD_IMAGE"
        rendered = open(
            arguments.config_template, encoding="utf-8"
        ).read().replace(placeholder, images["execd_image"])
        if placeholder in rendered:
            print("the config template must name the execd placeholder exactly once")
            return 2
        parent = os.path.dirname(output_path)
        if parent and not os.path.isdir(parent):
            print(f"missing config parent directory: {parent}")
            return 2
        descriptor = os.open(
            output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
        )
        try:
            os.write(descriptor, rendered.encode())
        finally:
            os.close(descriptor)
        created.append(output_path)

    for entry in created:
        print(f"created: {entry}")
    if not created:
        print("sandbox configuration already prepared; nothing written")
    return 0


raise SystemExit(main())
