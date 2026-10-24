"""Shared, fail-closed managed-dispatch lock for rotation and restore work."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ...config import settings


def _path(value: str | Path | None) -> Path:
    return Path(value if value is not None else settings.connector_dispatch_lock_file)


def managed_dispatch_is_locked(path: str | Path | None = None) -> bool:
    """Return false only when the lock is positively absent or a regular file is absent."""
    descriptor = None
    try:
        descriptor = os.open(
            _path(path), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    finally:
        if descriptor is not None:
            os.close(descriptor)


def set_managed_dispatch_lock(*, locked: bool, path: str | Path | None = None) -> None:
    """Create/remove the shared marker without following symlinks or overwriting data."""
    selected = _path(path)
    if locked:
        try:
            descriptor = os.open(
                selected,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            if not managed_dispatch_is_locked(selected):
                raise RuntimeError("managed_dispatch_lock_invalid") from None
            return
        with os.fdopen(descriptor, "wb") as output:
            output.write(b"managed dispatch locked\n")
            output.flush()
            os.fsync(output.fileno())
        return

    try:
        info = selected.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("managed_dispatch_lock_invalid")
    selected.unlink()
