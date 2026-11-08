"""Declared-input catalog and deterministic recipe fingerprints.

A recipe fingerprint covers the exact bytes of every declared build input
plus the target platform; shared inventories are projected to the entries a
profile actually consumes so one profile's change cannot invalidate another.
Legacy release-inventory keys are read untouched and never rewritten.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class ImageError(Exception):
    """A safe operator-facing failure naming a setting, profile or action."""


@dataclass(frozen=True)
class ProfileSpec:
    id: str
    dockerfile: Path
    requirements_lock: Path
    os_packages_lock: Path
    smoke: Path
    install_helper: Path
    extra_inputs: tuple


@dataclass(frozen=True)
class Recipe:
    profile: ProfileSpec
    platform: str
    fingerprint: str
    base_fingerprint: str
    image_tag: str
    base_tag: str
    dependency_identity: str
    inputs: tuple


def _inventory(root: Path) -> dict:
    try:
        return json.loads((root / "deploy/sandbox/images.json").read_text())
    except (OSError, ValueError) as exc:
        raise ImageError("unreadable deploy/sandbox/images.json") from exc


def _declared_paths(images: dict, base_entry: dict, entry: dict) -> tuple:
    """Relative paths of every declared build input, base first."""

    def relative(entry: dict, keys: tuple) -> list:
        found = []
        for key in keys:
            rel = entry.get(key)
            if rel is None:
                raise ImageError(f"managed inventory is missing key: {key}")
            if rel.startswith("/") or ".." in rel.split("/"):
                raise ImageError(f"declared input escapes the repository: {rel}")
            found.append(rel)
        return found

    base_rels = relative(
        base_entry,
        ("dockerfile", "pins", "build_requirements_in", "build_requirements_lock", "install_helper"),
    )
    profile_rels = relative(
        entry,
        ("dockerfile", "requirements_in", "requirements_lock", "os_packages_lock", "smoke", "install_helper"),
    )
    return base_rels, profile_rels + list(entry.get("extra_inputs", []))


def load_profile(root: Path, profile_id: str) -> ProfileSpec:
    images = _inventory(root)
    entry = images.get("managed_profiles", {}).get(profile_id)
    if entry is None:
        raise ImageError(f"unknown sandbox profile: {profile_id}")
    return ProfileSpec(
        id=profile_id,
        dockerfile=root / entry["dockerfile"],
        requirements_lock=root / entry["requirements_lock"],
        os_packages_lock=root / entry["os_packages_lock"],
        smoke=root / entry["smoke"],
        install_helper=root / entry["install_helper"],
        extra_inputs=tuple(root / rel for rel in entry.get("extra_inputs", [])),
    )


def resolve_recipe(root: Path, profile_id: str, platform: str) -> Recipe:
    images = _inventory(root)
    base_entry = images.get("managed_base")
    if base_entry is None:
        raise ImageError("deploy/sandbox/images.json declares no managed_base")
    entry = images.get("managed_profiles", {}).get(profile_id)
    if entry is None:
        raise ImageError(f"unknown sandbox profile: {profile_id}")
    base_rels, profile_rels = _declared_paths(images, base_entry, entry)

    pins_path = root / base_entry["pins"]
    try:
        pins = json.loads(pins_path.read_text())
    except (OSError, ValueError) as exc:
        raise ImageError("unreadable pins manifest for the managed base") from exc
    supported = pins.get("supported_platforms", [])
    if platform not in supported:
        raise ImageError(
            f"unsupported platform {platform}; the pins manifest allows: {' '.join(supported)}"
        )

    hashed = {}
    for rel in base_rels + profile_rels:
        path = root / rel
        if path.is_symlink():
            raise ImageError(f"symlinked build input cannot be staged safely: {rel}")
        if not path.is_file():
            raise ImageError(f"declared build input is missing: {rel}")
        hashed[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

    tool_names = entry.get("tools", [])
    if tool_names:
        tools_path = root / "deploy/sandbox/tools.lock.json"
        if tools_path.is_symlink() or not tools_path.is_file():
            raise ImageError("declared tool manifest is missing or unsafe")
        try:
            tools_doc = json.loads(tools_path.read_text())
        except ValueError as exc:
            raise ImageError("unreadable tool manifest") from exc
        projection = {}
        for name in tool_names:
            if name not in tools_doc.get("tools", {}):
                raise ImageError(f"tool {name} is not declared in the tool manifest")
            projection[name] = tools_doc["tools"][name]
        hashed[f"tools.lock.json#{profile_id}"] = hashlib.sha256(
            json.dumps({"tools": projection}, sort_keys=True).encode()
        ).hexdigest()

    def digest(rels) -> str:
        material = [f"platform\t{platform}\n"]
        for rel in sorted(rels):
            material.append(f"{rel}\t{hashed[rel]}\n")
        return hashlib.sha256("".join(material).encode()).hexdigest()

    fingerprint = digest(base_rels + profile_rels + [f"tools.lock.json#{profile_id}"] if tool_names else base_rels + profile_rels)
    base_fingerprint = digest(base_rels)
    spec = load_profile(root, profile_id)
    return Recipe(
        profile=spec,
        platform=platform,
        fingerprint=fingerprint,
        base_fingerprint=base_fingerprint,
        image_tag=f"openship-sandbox-{profile_id}:{fingerprint}",
        base_tag=f"openship-sandbox-base:{base_fingerprint}",
        dependency_identity=f"sha256:{fingerprint}",
        inputs=tuple(root / rel for rel in base_rels + profile_rels),
    )
