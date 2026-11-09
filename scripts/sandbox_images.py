#!/usr/bin/env python3
"""Explicit image management; stdout contains safe status/runtime assignments only."""

import os
import sys
from pathlib import Path

from sandbox_image_lib.builds import (
    ensure_image,
    inspect_status,
    publication_record,
    run_smoke,
    runtime_values,
)
from sandbox_image_lib.catalog import ImageError, _inventory, resolve_recipe
from sandbox_image_lib.config import (
    MANAGED,
    read_settings,
    resolve_endpoint,
    resolve_profiles_source,
    validate_selection,
)
from sandbox_image_lib.engine import Engine
from sandbox_image_lib.profiles import merge_profile_updates

PLATFORM = "linux/amd64"
USAGE = ("usage: sandbox_images.py [--root PATH] --engine docker|podman "
         "list | build PROFILE|--all [--overwrite-profiles] | smoke PROFILE | preflight [--build]")


def _interactive_stdin() -> bool:
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, OSError):
        return False


def _confirm_write(destination: Path, profile_ids) -> bool:
    print(f"write sandbox profiles {', '.join(profile_ids)} to {destination}? [y/N] ",
          end="", flush=True)
    try:
        answer = sys.stdin.readline()
    except (AttributeError, OSError, UnicodeError):
        return False
    return answer.strip().lower() in ("y", "yes")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    cli = None
    while args and args[0].startswith("--"):
        token = args.pop(0)
        if token not in ("--root", "--engine") or not args:
            print(USAGE, file=sys.stderr)
            return 2
        value = args.pop(0)
        if token == "--root":
            root = Path(value)
        else:
            cli = value
    if cli not in ("docker", "podman") or not args:
        print(USAGE, file=sys.stderr)
        return 2
    command, args = args[0], args[1:]
    overwrite_profiles = False
    target = None
    if command == "build":
        if args and args[0] == "--all":
            rest = args[1:]
        elif args and not args[0].startswith("-"):
            target = args[0]
            rest = args[1:]
        else:
            rest = None
        if rest == []:
            build_valid = True
        elif rest == ["--overwrite-profiles"]:
            build_valid = True
            overwrite_profiles = True
        else:
            build_valid = False
    else:
        build_valid = False
    valid = ((command == "list" and not args)
             or (command == "smoke" and len(args) == 1)
             or (command == "build" and build_valid)
             or (command == "preflight" and args in ([], ["--build"])))
    if not valid:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        known = sorted(_inventory(root).get("managed_profiles", {}))
        selected_target = target if command == "build" else (args[0] if command == "smoke" else None)
        if selected_target is not None and selected_target not in known:
            print(f"unknown sandbox profile: {selected_target} (known: {' '.join(known)})", file=sys.stderr)
            return 2
        settings = read_settings(root, os.environ)
        if command == "preflight":
            build_requested = args == ["--build"]
            enabled = settings["SANDBOX_ENABLED"].lower() in ("1", "true", "yes", "on")
            selected = settings["SANDBOX_PROFILE_ID"]
            if not build_requested and (not enabled or selected not in MANAGED):
                return 0
            if selected not in known:
                raise ImageError("--build-sandbox requires an explicitly selected managed SANDBOX_PROFILE_ID")
            if build_requested and not enabled:
                # Parse the managed literals, without enabling the actual environment.
                settings = read_settings(root, {**os.environ, "SANDBOX_ENABLED": "true"})
            recipe = resolve_recipe(root, selected, PLATFORM)
            validate_selection(recipe, settings)
        endpoint = resolve_endpoint(cli, settings)
    except (ImageError, OSError) as exc:
        print(f"sandbox-images: {exc}", file=sys.stderr)
        return 2

    try:
        engine = Engine(cli, endpoint, PLATFORM)
        engine.info()
        if command == "preflight":
            status = inspect_status(root, recipe, engine)
            if build_requested:
                result = ensure_image(root, recipe, engine)
                if result["smoke"] != "passed":
                    raise ImageError(f"smoke failed for {selected}; run sandbox-images smoke {selected}")
            elif status["status"] != "matching" or status["smoke"] != "passed":
                raise ImageError(f"selected image for {selected} is missing/stale or has no passed smoke; "
                                 f"run sandbox-images build {selected}, then sandbox-images smoke {selected}")
            return 0
        if command == "list":
            failed = False
            for name in known:
                try:
                    recipe = resolve_recipe(root, name, PLATFORM)
                    status = inspect_status(root, recipe, engine)
                    print(f"{name} platform={recipe.platform} fingerprint={recipe.fingerprint} "
                          f"status={status['status']} image_id={status['image_id'] or '-'} "
                          f"built_at={status['built_at'] or '-'} smoke={status['smoke'] or 'none'}")
                except ImageError as exc:
                    print(f"{name}: {exc}", file=sys.stderr)
                    failed = True
            return int(failed)
        if command == "build":
            failed = False
            updates = {}
            for name in known if target is None else [target]:
                try:
                    recipe = resolve_recipe(root, name, PLATFORM)
                    result = ensure_image(root, recipe, engine)
                    print(f"profile {name}: {result['status']} image_id={result['image_id']} "
                          f"smoke={result['smoke']}")
                    for key, value in runtime_values(recipe).items():
                        print(f"{key}={value}")
                    if result["smoke"] != "passed":
                        print(f"sandbox-images: profile {name}: smoke did not pass; not publishing",
                              file=sys.stderr)
                        failed = True
                        continue
                    updates[name] = publication_record(root, recipe, engine)
                except ImageError as exc:
                    print(f"sandbox-images: profile {name}: {exc}", file=sys.stderr)
                    failed = True
            if updates:
                try:
                    destination, _explicit = resolve_profiles_source(root, os.environ)
                except ImageError as exc:
                    print(f"sandbox-images: {exc}", file=sys.stderr)
                    failed = True
                else:
                    interactive = _interactive_stdin()
                    authorized = overwrite_profiles or (
                        interactive and _confirm_write(destination, sorted(updates))
                    )
                    if authorized:
                        try:
                            published = merge_profile_updates(root, destination, updates)
                            print(f"profiles written: {' '.join(published)} -> {destination}")
                        except ImageError as exc:
                            print(f"sandbox-images: {exc}", file=sys.stderr)
                            failed = True
                    elif interactive:
                        print(f"profile snapshot not written: {destination} (declined)")
                    else:
                        print(f"profile snapshot not written: {destination} "
                              "(no interactive terminal; rerun with --overwrite-profiles)")
            return int(failed)
        recipe = resolve_recipe(root, args[0], PLATFORM)
        result = run_smoke(root, recipe, engine)
        print(f"profile {recipe.profile.id} smoke={result['status']} image_id={result['image_id']}")
        return int(result["status"] != "passed")
    except ImageError as exc:
        print(f"sandbox-images: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
