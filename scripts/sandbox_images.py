#!/usr/bin/env python3
"""Managed sandbox image commands: list, build, smoke and internal preflight.

Progress and errors go to stderr; stdout carries only safe profile, build,
status and runtime-setting output. Invalid input exits 2, a failed operation
exits 1 and successful operations exit 0.
"""

import os
import sys
from pathlib import Path

from sandbox_image_lib.builds import (
    ensure_image,
    inspect_status,
    run_smoke,
    runtime_values,
)
from sandbox_image_lib.catalog import ImageError, _inventory, resolve_recipe
from sandbox_image_lib.engine import Engine

PLATFORM = "linux/amd64"

USAGE = (
    "usage: sandbox_images.py [--root PATH] --engine docker|podman "
    "list | build PROFILE|--all | smoke PROFILE | preflight [--build]"
)


def _managed_profile_ids(root: Path) -> list:
    images = _inventory(root)
    return sorted(images.get("managed_profiles", {}))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    engine_cli = None
    while args and args[0].startswith("--") and args[0] not in ("list", "build", "smoke", "preflight"):
        token = args.pop(0)
        if token == "--root":
            if not args:
                print(USAGE, file=sys.stderr)
                return 2
            root = Path(args.pop(0))
        elif token.startswith("--root="):
            root = Path(token.split("=", 1)[1])
        elif token == "--engine":
            if not args:
                print(USAGE, file=sys.stderr)
                return 2
            engine_cli = args.pop(0)
        elif token.startswith("--engine="):
            engine_cli = token.split("=", 1)[1]
        else:
            print(USAGE, file=sys.stderr)
            return 2
    if engine_cli not in ("docker", "podman") or not args:
        print(USAGE, file=sys.stderr)
        return 2
    command, args = args[0], args[1:]

    socket = os.environ.get("OPENSHIP_SANDBOX_ENGINE_SOCKET")
    if not socket:
        if engine_cli == "podman":
            xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
            socket = f"{xdg_runtime}/openship-sandbox/podman.sock" if xdg_runtime else None
        else:
            socket = "/var/run/docker.sock"
    if not socket:
        print("no engine socket: set OPENSHIP_SANDBOX_ENGINE_SOCKET or XDG_RUNTIME_DIR", file=sys.stderr)
        return 2

    known = _managed_profile_ids(root)

    def unknown_profile_note(profile_id: str) -> None:
        print(f"unknown sandbox profile: {profile_id} (known: {' '.join(known)})", file=sys.stderr)

    try:
        engine = Engine(engine_cli, socket, PLATFORM)
        if command == "list":
            for profile_id in known:
                try:
                    recipe = resolve_recipe(root, profile_id, PLATFORM)
                    status = inspect_status(root, recipe, engine)
                    print(
                        f"{profile_id} platform={recipe.platform} fingerprint={recipe.fingerprint} "
                        f"status={status['status']} built_at={status['built_at'] or '-'} "
                        f"smoke={status['smoke'] or 'none'}"
                    )
                except ImageError as exc:
                    print(f"{profile_id} error: {exc}")
            return 0
        if command == "build":
            if args == ["--all"]:
                profile_ids = known
            elif len(args) == 1:
                if args[0] not in known:
                    unknown_profile_note(args[0])
                    return 2
                profile_ids = [args[0]]
            else:
                print(USAGE, file=sys.stderr)
                return 2
            failed = False
            for profile_id in profile_ids:
                recipe = resolve_recipe(root, profile_id, PLATFORM)
                result = ensure_image(root, recipe, engine)
                print(f"profile {profile_id}: {result['status']} image_id={result['image_id']} smoke={result['smoke']}")
                for key, value in runtime_values(recipe).items():
                    print(f"{key}={value}")
                failed = failed or result["smoke"] != "passed"
            return 1 if failed else 0
        if command == "smoke":
            if len(args) != 1:
                print(USAGE, file=sys.stderr)
                return 2
            if args[0] not in known:
                unknown_profile_note(args[0])
                return 2
            recipe = resolve_recipe(root, args[0], PLATFORM)
            outcome = run_smoke(root, recipe, engine)
            print(f"profile {recipe.profile.id} smoke={outcome['status']} image_id={outcome['image_id']}")
            return 0 if outcome["status"] == "passed" else 1
        if command == "preflight":
            build_opt_in = args == ["--build"] or args == []
            if args not in ([], ["--build"]):
                print(USAGE, file=sys.stderr)
                return 2
            selected = os.environ.get("SANDBOX_PROFILE_ID")
            if selected not in known:
                print(f"preflight needs SANDBOX_PROFILE_ID naming a managed profile ({' '.join(known)})", file=sys.stderr)
                return 2
            recipe = resolve_recipe(root, selected, PLATFORM)
            status = inspect_status(root, recipe, engine)
            if build_opt_in and status["status"] != "matching":
                result = ensure_image(root, recipe, engine)
                return 0 if result["smoke"] == "passed" else 1
            if status["status"] != "matching":
                print(
                    f"selected image for {selected} is {status['status']}; run "
                    f"sandbox-images build {selected} or start --build-sandbox",
                    file=sys.stderr,
                )
                return 1
            return 0
        print(USAGE, file=sys.stderr)
        return 2
    except ImageError as exc:
        print(f"sandbox-images: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
