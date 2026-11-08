"""Build orchestration and engine-local build records.

Records live in gitignored local state under the repository root, keyed by
engine identity and platform. A record is trusted only when its fingerprint,
engine identity and a fresh engine inspection agree; corrupted records,
symlinked state paths, changed image IDs and missing images never count as
matching. Raw engine output is never persisted.
"""

import fcntl
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .catalog import ImageError, _declared_paths, _inventory

RECORD_FIELDS = (
    "schema_version",
    "profile_id",
    "platform",
    "engine_identity",
    "fingerprint",
    "image_tag",
    "image_id",
    "dependency_identity",
    "built_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _engine_key(engine) -> str:
    return f"{engine.cli}_" + re.sub(r"[^a-z0-9]+", "_", engine.endpoint.lower()).strip("_")


def _record_dir(root: Path, engine) -> Path:
    return root / ".openship" / "sandbox" / "images" / _engine_key(engine) / engine.platform.replace("/", "-")


def _read_record(root: Path, engine, name: str) -> dict | None:
    directory = _record_dir(root, engine)
    if directory.is_symlink():
        return None
    record_path = directory / f"{name}.json"
    if record_path.is_symlink() or not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text())
    except ValueError:
        return None
    if not all(key in record for key in RECORD_FIELDS):
        return None
    if record["schema_version"] != 1 or record["engine_identity"] != engine.identity:
        return None
    return record


def _write_record(root: Path, engine, name: str, payload: dict) -> None:
    directory = _record_dir(root, engine)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ImageError("local build state path is unsafe")
    lock_path = directory / f".{name}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            temporary = directory / f".{name}.json.tmp.{os.getpid()}"
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(temporary, directory / f"{name}.json")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _stage(root: Path, rels) -> Path:
    """Copy only the declared inputs into a throwaway build context."""
    context = Path(tempfile.mkdtemp(prefix="openship-build-context-"))
    for rel in rels:
        source = root / rel
        target = context / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return context


def inspect_status(root: Path, recipe, engine) -> dict:
    record = _read_record(root, engine, recipe.profile.id)
    inspected = engine.inspect_image(recipe.image_tag)
    if record is None:
        status = "missing"
    elif (
        record["fingerprint"] == recipe.fingerprint
        and record["image_tag"] == recipe.image_tag
        and inspected is not None
        and inspected["Id"] == record["image_id"]
    ):
        status = "matching"
    else:
        status = "stale"
    smoke = record.get("smoke") if record and isinstance(record.get("smoke"), dict) else None
    return {
        "status": status,
        "image_id": inspected["Id"] if inspected else None,
        "fingerprint": recipe.fingerprint,
        "dependency_identity": recipe.dependency_identity,
        "built_at": record.get("built_at") if record else None,
        "smoke": smoke["status"] if smoke else None,
    }


def _tool_build_args(root: Path, recipe, entry) -> dict:
    """Map the profile's tool list to <TOOL>_URL/<TOOL>_SHA256 build args.

    The tool manifest is hashed through the projected catalog input but never
    staged; the archive values reach the Dockerfile only as build args."""
    args = {}
    for name in entry.get("tools", []):
        try:
            tools_doc = json.loads((root / "deploy/sandbox/tools.lock.json").read_text())
            archived = tools_doc["tools"][name][recipe.platform]
            args[f"{name.upper()}_URL"] = archived["url"]
            args[f"{name.upper()}_SHA256"] = archived["sha256"]
        except (KeyError, ValueError, TypeError):
            raise ImageError(f"tool {name} has no archive entry for {recipe.platform}")
    return args


def _profile_rels(root: Path, recipe) -> tuple:
    images = _inventory(root)
    base_entry = images["managed_base"]
    entry = images["managed_profiles"][recipe.profile.id]
    return _declared_paths(images, base_entry, entry)


def ensure_base(root: Path, recipe, engine) -> None:
    record = _read_record(root, engine, "base")
    inspected = engine.inspect_image(recipe.base_tag)
    if (
        record is not None
        and record["fingerprint"] == recipe.base_fingerprint
        and record["image_tag"] == recipe.base_tag
        and inspected is not None
        and inspected["Id"] == record["image_id"]
    ):
        return
    base_rels, _ = _profile_rels(root, recipe)
    pins = json.loads((root / _inventory(root)["managed_base"]["pins"]).read_text())
    python_base_image = pins.get("python_base_image")
    if python_base_image is None:
        raise ImageError("pins manifest declares no python_base_image")
    build_args = {"PYTHON_BASE_IMAGE": python_base_image}
    for key in ("uv_url", "uv_sha256"):
        value = pins.get(key)
        if value is not None:
            build_args[key.upper()] = value
    context = _stage(root, base_rels)
    image_id = engine.build(
        context / _inventory(root)["managed_base"]["dockerfile"],
        context,
        recipe.base_tag,
        build_args,
        {"openship.role": "base"},
    )
    _write_record(
        root,
        engine,
        "base",
        {
            "schema_version": 1,
            "profile_id": "base",
            "platform": recipe.platform,
            "engine_identity": engine.identity,
            "fingerprint": recipe.base_fingerprint,
            "image_tag": recipe.base_tag,
            "image_id": image_id,
            "dependency_identity": f"sha256:{recipe.base_fingerprint}",
            "built_at": _now(),
        },
    )


def ensure_image(root: Path, recipe, engine) -> dict:
    status = inspect_status(root, recipe, engine)
    if status["status"] == "matching":
        return {"status": "matching", "image_id": status["image_id"], "smoke": status["smoke"]}
    ensure_base(root, recipe, engine)
    _base_rels, profile_rels = _profile_rels(root, recipe)
    context = _stage(root, profile_rels)
    entry = _inventory(root)["managed_profiles"][recipe.profile.id]
    build_args = {"WORKLOAD_BASE_IMAGE": recipe.base_tag}
    build_args.update(_tool_build_args(root, recipe, entry))
    image_id = engine.build(
        context / entry["dockerfile"],
        context,
        recipe.image_tag,
        build_args,
        {"openship.profile": recipe.profile.id, "openship.fingerprint": recipe.fingerprint},
    )
    try:
        run = engine.smoke(image_id)
        smoke_status = "passed" if run.returncode == 0 else "failed"
    except ImageError:
        smoke_status = "failed"
    _write_record(
        root,
        engine,
        recipe.profile.id,
        {
            "schema_version": 1,
            "profile_id": recipe.profile.id,
            "platform": recipe.platform,
            "engine_identity": engine.identity,
            "fingerprint": recipe.fingerprint,
            "image_tag": recipe.image_tag,
            "image_id": image_id,
            "dependency_identity": recipe.dependency_identity,
            "built_at": _now(),
            "smoke": {"status": smoke_status, "image_id": image_id, "fingerprint": recipe.fingerprint, "at": _now()},
        },
    )
    return {"status": "built", "image_id": image_id, "smoke": smoke_status}


def run_smoke(root: Path, recipe, engine) -> dict:
    status = inspect_status(root, recipe, engine)
    if status["status"] != "matching":
        raise ImageError(
            f"image for profile {recipe.profile.id} is missing or stale; "
            "run sandbox-images build before smoke"
        )
    image_id = status["image_id"]
    record = _read_record(root, engine, recipe.profile.id)
    try:
        run = engine.smoke(image_id)
        outcome = "passed" if run.returncode == 0 else "failed"
    except ImageError:
        outcome = "failed"
    record["smoke"] = {"status": outcome, "image_id": image_id, "fingerprint": recipe.fingerprint, "at": _now()}
    _write_record(root, engine, recipe.profile.id, record)
    return {"status": outcome, "image_id": image_id}


def runtime_values(recipe) -> dict:
    return {
        "SANDBOX_IMAGE": recipe.image_tag,
        "SANDBOX_PROFILE_ID": recipe.profile.id,
        "SANDBOX_DEPENDENCY_IDENTITY": recipe.dependency_identity,
    }
