"""Collect declared files after all untrusted workload processes are stopped."""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class CollectionError(ValueError):
    """An output cannot be safely captured."""


@dataclass(frozen=True)
class CollectedFile:
    content: bytes
    sha256: str


def collect_outputs(directory: Path, names: list[str]) -> dict[str, CollectedFile]:
    if len(names) > 8 or len(set(names)) != len(names):
        raise CollectionError("invalid output count")
    for name in names:
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or len(name.encode("utf-8")) > 255):
            raise CollectionError("invalid output name")
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise CollectionError("invalid output directory") from None
    result = {}
    total = 0
    try:
        for name in names:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4 * 1024 * 1024:
                        raise CollectionError("invalid output file")
                    chunks = []
                    remaining = 4 * 1024 * 1024 + 1
                    while remaining:
                        chunk = os.read(fd, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    content = b"".join(chunks)
                    total += len(content)
                    if len(content) > 4 * 1024 * 1024 or total > 8 * 1024 * 1024:
                        raise CollectionError("output limit exceeded")
                    result[name] = CollectedFile(content, hashlib.sha256(content).hexdigest())
                finally:
                    os.close(fd)
            except OSError:
                raise CollectionError("output unavailable") from None
    finally:
        os.close(directory_fd)
    return result
