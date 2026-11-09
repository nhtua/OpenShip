"""Merge verified build records into the host profile snapshot safely.

The host image builder stays stdlib-only. YAML validation is delegated to the
API-owned codec over a bounded stdin/stdout envelope. Publication rereads the
destination under a stable sibling lock and atomically replaces it, so a
competing writer or a failure can never truncate the previous snapshot.
"""

import base64
import binascii
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from .catalog import ImageError
from .config import DEFAULT_PROFILES_FILE, MANAGED

PROFILE_IDS = frozenset(MANAGED)
CODEC_TIMEOUT_SECONDS = 10
NEW_FILE_MODE = 0o644
MAX_PROFILE_BYTES = 256 * 1024


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _codec_base(root: Path) -> Path:
    candidates = (Path(root) / "apps" / "api", Path(__file__).resolve().parents[2] / "apps" / "api")
    for candidate in candidates:
        if (candidate / ".venv" / "bin" / "python").is_file():
            return candidate
    return candidates[0]


def _codec_environment() -> dict[str, str]:
    # Deliberately minimal: the bridge only needs UTF-8 and an isolated import path.
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
    }


def _run_codec(root: Path, request: dict) -> dict:
    base = _codec_base(root)
    python = base / ".venv" / "bin" / "python"
    if not python.is_file():
        raise ImageError(
            "profile codec interpreter is missing; run `uv sync --project apps/api`"
        )
    try:
        process = subprocess.run(
            [str(python), "-m", "src.openship.sandboxes.profile_codec"],
            input=_canonical_json(request),
            cwd=base,
            capture_output=True,
            timeout=CODEC_TIMEOUT_SECONDS,
            check=False,
            env=_codec_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ImageError("profile codec exceeded its 10 second deadline") from exc
    except OSError as exc:
        raise ImageError("profile codec could not be started; run `uv sync --project apps/api`") from exc
    if process.returncode != 0:
        raise ImageError("profile codec rejected the profile catalog")
    try:
        response = json.loads(process.stdout)
    except ValueError as exc:
        raise ImageError("profile codec returned invalid output") from exc
    if not isinstance(response, dict):
        raise ImageError("profile codec returned invalid output")
    return response


def _decode_catalog(root: Path, data: bytes) -> dict:
    response = _run_codec(
        root, {"action": "decode", "yaml_base64": base64.b64encode(data).decode("ascii")}
    )
    catalog = response.get("catalog")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ImageError("existing profile snapshot is malformed; refusing to replace it")
    if not isinstance(catalog.get("profiles"), dict):
        raise ImageError("existing profile snapshot is malformed; refusing to replace it")
    return catalog


def _encode_catalog(root: Path, catalog: dict) -> bytes:
    response = _run_codec(root, {"action": "encode", "catalog": catalog})
    try:
        return base64.b64decode(response["yaml_base64"], validate=True)
    except (KeyError, TypeError, binascii.Error) as exc:
        raise ImageError("profile codec returned invalid snapshot bytes") from exc


def _entry_from_update(update: dict) -> dict:
    try:
        metadata = update["metadata"]
        smoke = update["smoke"]
        runtime = {
            "image_tag": update["image_tag"],
            "image_id": update["image_id"],
            "dependency_identity": update["dependency_identity"],
            "platform": update["platform"],
            "fingerprint": update["fingerprint"],
            "engine_identity": update["engine_identity"],
            "smoke_image_id": smoke["image_id"],
            "smoke_fingerprint": smoke["fingerprint"],
            "smoke_passed": smoke["status"] == "passed",
            "smoke_evidence_digest": _digest(
                {
                    "image_id": update["image_id"],
                    "fingerprint": update["fingerprint"],
                    "status": smoke["status"],
                }
            ),
        }
    except (KeyError, TypeError) as exc:
        raise ImageError("successful build record is incomplete and cannot be published") from exc
    if not isinstance(metadata, dict):
        raise ImageError("profile metadata is malformed and cannot be published")
    if runtime["smoke_passed"] is not True:
        raise ImageError("only a passed smoke check can be published")
    if runtime["smoke_image_id"] != runtime["image_id"]:
        raise ImageError("smoke image does not match the built image")
    if runtime["smoke_fingerprint"] != runtime["fingerprint"]:
        raise ImageError("smoke fingerprint does not match the built image")
    return {
        "enabled": True,
        "extensions": {},
        "metadata": metadata,
        "metadata_digest": _digest(metadata),
        "runtime": runtime,
    }


def _require_safe_directory(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ImageError(f"profile destination directory is missing or unsafe: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ImageError(f"profile destination directory is unsafe: {path}")
    if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
        raise ImageError(
            f"profile destination directory is group or world writable without the sticky bit: {path}"
        )


def _check_destination(destination: Path) -> None:
    # Walk every ancestor with lstat (never following symlinks). A sticky
    # group/world-writable directory such as /tmp is permitted because its
    # sticky bit prevents other users from replacing our file or lock; any
    # non-sticky group/world-writable ancestor is rejected.
    current = destination.parent
    while True:
        _require_safe_directory(current)
        if current == current.parent:
            break
        current = current.parent
    try:
        info = os.lstat(destination)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ImageError(f"profile destination is unsafe: {destination}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ImageError("profile destination must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ImageError("profile destination must be a regular file")
    if info.st_mode & 0o022:
        raise ImageError("profile destination must not be group or world writable")


def _git_ignored(root: Path, path: Path) -> bool:
    try:
        process = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(path)],
            cwd=root,
            capture_output=True,
            timeout=CODEC_TIMEOUT_SECONDS,
            check=False,
            env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageError(
            "custom in-repository profile destination requires a Git ignore rule"
        ) from exc
    return process.returncode == 0


def _require_git_ignore(root: Path, destination: Path) -> None:
    default = Path(os.path.abspath(root / DEFAULT_PROFILES_FILE))
    if destination == default:
        return
    lock_path = destination.parent / f".{destination.name}.lock"
    if not _git_ignored(root, destination) or not _git_ignored(root, lock_path):
        raise ImageError(
            "custom in-repository profile destination and its lock file must be "
            "ignored by Git before writing"
        )


@contextmanager
def _profile_lock(destination: Path):
    lock_path = destination.parent / f".{destination.name}.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise ImageError("profile destination lock is unsafe") from exc
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read_existing(destination: Path) -> tuple[bytes, int] | None:
    try:
        descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ImageError("profile destination cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ImageError("profile destination must be a regular file")
        if info.st_mode & 0o022:
            raise ImageError("profile destination must not be group or world writable")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PROFILE_BYTES:
                raise ImageError("existing profile snapshot exceeds its size limit")
        return b"".join(chunks), stat.S_IMODE(info.st_mode)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(temporary: str, destination: Path) -> None:
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def _publish(destination: Path, data: bytes, mode: int) -> None:
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, destination)
        temporary = None
    except OSError as exc:
        raise ImageError("profile snapshot could not be published atomically") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def merge_profile_updates(
    root: Path, destination: Path, updates: Mapping[str, dict]
) -> tuple[str, ...]:
    """Publish successful profile updates, preserving unrelated operator state."""
    root = Path(os.path.abspath(root))
    destination = Path(destination)
    if not destination.is_absolute():
        destination = root / destination
    destination = Path(os.path.abspath(destination))
    if not updates:
        return ()

    in_repository = destination == root or root in destination.parents
    if in_repository:
        _require_git_ignore(root, destination)
    _check_destination(destination)

    with _profile_lock(destination):
        _check_destination(destination)
        existing = _read_existing(destination)
        if existing is None:
            catalog = {"schema_version": 1, "profiles": {}}
            mode = NEW_FILE_MODE
        else:
            catalog = _decode_catalog(root, existing[0])
            mode = existing[1]
        merged_profiles = dict(catalog["profiles"])
        for profile_id, update in updates.items():
            if profile_id not in PROFILE_IDS:
                raise ImageError(f"unknown sandbox profile: {profile_id}")
            entry = _entry_from_update(update)
            previous = merged_profiles.get(profile_id)
            if isinstance(previous, dict):
                entry["enabled"] = previous.get("enabled", True)
                entry["extensions"] = previous.get("extensions", {})
            merged_profiles[profile_id] = entry
        data = _encode_catalog(root, {"schema_version": 1, "profiles": merged_profiles})
        _publish(destination, data, mode)
    return tuple(sorted(updates))
