"""Descriptor-relative local storage for immutable, verified artifact bytes."""

from __future__ import annotations

import codecs
import errno
import hashlib
import json
import os
import stat
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from src.openship.artifacts.contracts import ArtifactRef, StagedArtifact
from src.openship.config import settings


class ArtifactStorageError(RuntimeError):
    """A storage operation could not prove the configured bytes are safe."""


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)


def _safe_parts(key: str) -> tuple[str, ...]:
    if (
        not isinstance(key, str)
        or not key
        or len(key.encode("utf-8", "strict")) > 1024
        or key.startswith("/")
        or "\\" in key
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise ArtifactStorageError("artifact storage key is invalid")
    parts = tuple(key.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ArtifactStorageError("artifact storage key is invalid")
    return parts


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise ArtifactStorageError("artifact storage write was incomplete")
        view = view[written:]


def _strict_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def _validate_json_depth(value) -> None:
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds the supported depth")
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth) for child in children)


class LocalArtifactStore:
    """A POSIX directory tree anchored by an operator-controlled storage ID."""

    def __init__(self, root: Path, storage_id: uuid.UUID):
        self.root = Path(root)
        self.storage_id = storage_id

    def _open_root(self) -> int:
        if os.name != "posix":
            raise ArtifactStorageError("artifact storage requires a local POSIX filesystem")
        try:
            before = os.lstat(self.root)
        except OSError as exc:
            raise ArtifactStorageError("artifact storage root is unavailable") from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise ArtifactStorageError("artifact storage root is not a real directory")
        try:
            fd = os.open(self.root, _DIRECTORY_FLAGS | _NOFOLLOW)
        except OSError as exc:
            raise ArtifactStorageError("artifact storage root is unavailable") from exc
        after = os.fstat(fd)
        if not stat.S_ISDIR(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(fd)
            raise ArtifactStorageError("artifact storage root changed during access")
        return fd

    @staticmethod
    def _read_marker(root_fd: int) -> uuid.UUID:
        try:
            fd = os.open(".storage-id", _FILE_FLAGS, dir_fd=root_fd)
        except OSError as exc:
            raise ArtifactStorageError("artifact storage identity marker is unavailable") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64:
                raise ArtifactStorageError("artifact storage identity marker is invalid")
            data = os.read(fd, 65)
            if len(data) > 64:
                raise ArtifactStorageError("artifact storage identity marker is invalid")
            try:
                return uuid.UUID(data.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError) as exc:
                raise ArtifactStorageError("artifact storage identity marker is invalid") from exc
        finally:
            os.close(fd)

    def initialize(self) -> None:
        """Create the root and marker only when the operator explicitly initializes it."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArtifactStorageError("artifact storage root could not be initialized") from exc
        root_fd = self._open_root()
        try:
            try:
                existing = self._read_marker(root_fd)
            except ArtifactStorageError:
                if os.listdir(root_fd):
                    raise ArtifactStorageError(
                        "refusing to initialize a non-empty artifact root without its identity marker"
                    )
                try:
                    marker_fd = os.open(
                        ".storage-id",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=root_fd,
                    )
                except OSError as exc:
                    raise ArtifactStorageError("artifact storage identity marker could not be created") from exc
                try:
                    _write_all(marker_fd, (str(self.storage_id) + "\n").encode("ascii"))
                    os.fsync(marker_fd)
                finally:
                    os.close(marker_fd)
                os.fsync(root_fd)
            else:
                if existing != self.storage_id:
                    raise ArtifactStorageError("artifact storage identity does not match configuration")
        finally:
            os.close(root_fd)
        self.verify()

    def verify(self) -> None:
        """Check that the existing root is still the configured storage volume."""
        root_fd = self._open_root()
        try:
            if self._read_marker(root_fd) != self.storage_id:
                raise ArtifactStorageError("artifact storage identity does not match configuration")
        finally:
            os.close(root_fd)

    @staticmethod
    def _open_directory(parent_fd: int, name: str, *, create: bool) -> int:
        if name in ("", ".", "..") or "/" in name or "\\" in name:
            raise ArtifactStorageError("artifact storage path component is invalid")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ArtifactStorageError("artifact storage directory could not be created") from exc
        try:
            fd = os.open(name, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            raise ArtifactStorageError("artifact storage path contains a non-directory or link") from exc
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.close(fd)
            raise ArtifactStorageError("artifact storage path contains a non-directory")
        return fd

    def _open_directory_chain(self, root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
        current = os.dup(root_fd)
        try:
            for part in parts:
                next_fd = self._open_directory(current, part, create=create)
                os.close(current)
                current = next_fd
            return current
        except Exception:
            os.close(current)
            raise

    def _open_parent(self, key: str, *, create: bool, missing_ok: bool = False) -> tuple[int | None, str]:
        parts = _safe_parts(key)
        if len(parts) < 2:
            raise ArtifactStorageError("artifact storage key must include a directory")
        root_fd = self._open_root()
        current = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                try:
                    next_fd = self._open_directory(current, part, create=create)
                except ArtifactStorageError as exc:
                    if missing_ok and isinstance(exc.__cause__, FileNotFoundError):
                        return None, parts[-1]
                    raise
                os.close(current)
                current = next_fd
            parent_fd = current
            current = -1
        finally:
            if current >= 0:
                os.close(current)
            os.close(root_fd)
        return parent_fd, parts[-1]

    def _staging_key(self, artifact_id: uuid.UUID, project_id=None, conversation_id=None) -> str:
        if (project_id is None) != (conversation_id is None):
            raise ArtifactStorageError("artifact staging scope is incomplete")
        if project_id is None:
            return f".staging/{artifact_id}/content"
        return f"projects/{project_id}/conversations/{conversation_id}/.staging/{artifact_id}/content"

    def stage(
        self,
        artifact_id: uuid.UUID,
        chunks: Iterable[bytes],
        expected: ArtifactRef,
        *,
        project_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> StagedArtifact:
        """Stream to a private staging file and verify exact size, digest and content shape."""
        self.verify()
        if expected.id != artifact_id:
            raise ArtifactStorageError("artifact ID does not match the expected reference")
        if expected.media_type not in ("application/json", "text/plain"):
            raise ArtifactStorageError("artifact media type is unsupported")
        if expected.size_bytes > settings.artifact_upload_max_bytes:
            raise ArtifactStorageError("artifact exceeds the configured size limit")
        staging_key = self._staging_key(artifact_id, project_id, conversation_id)
        parent_fd, leaf = self._open_parent(staging_key, create=True)
        try:
            try:
                fd = os.open(
                    leaf,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise ArtifactStorageError("artifact staging file could not be created exclusively") from exc
            digest = hashlib.sha256()
            size = 0
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            try:
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise ArtifactStorageError("artifact stream contains a non-byte chunk")
                    data = bytes(chunk)
                    size += len(data)
                    if size > expected.size_bytes or size > settings.artifact_upload_max_bytes:
                        raise ArtifactStorageError("artifact exceeds its declared size")
                    try:
                        decoded = decoder.decode(data, final=False)
                    except UnicodeDecodeError as exc:
                        raise ArtifactStorageError("artifact text must be valid UTF-8") from exc
                    if "\x00" in decoded:
                        raise ArtifactStorageError("artifact text must not contain NUL")
                    _write_all(fd, data)
                    digest.update(data)
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError as exc:
                    raise ArtifactStorageError("artifact text must be valid UTF-8") from exc
                if size != expected.size_bytes or digest.hexdigest() != expected.sha256:
                    raise ArtifactStorageError("artifact size or digest does not match the expected reference")
                os.fsync(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
                    raise ArtifactStorageError("artifact staging file changed while writing")
                if expected.media_type == "application/json":
                    self._validate_json_fd(fd)
                os.fsync(parent_fd)
            except Exception:
                os.close(fd)
                fd = -1
                try:
                    os.unlink(leaf, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError:
                    pass
                raise
            finally:
                if fd >= 0:
                    os.close(fd)
        finally:
            os.close(parent_fd)
        return StagedArtifact(
            artifact_id=artifact_id,
            relative_staging_key=staging_key,
            observed=expected,
        )

    @staticmethod
    def _validate_json_fd(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            chunks = []
            while True:
                piece = os.read(fd, 65536)
                if not piece:
                    break
                chunks.append(piece)
            value = json.loads(
                b"".join(chunks).decode("utf-8", "strict"),
                object_pairs_hook=_strict_object_pairs,
                parse_constant=_reject_constant,
            )
            _validate_json_depth(value)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ArtifactStorageError("artifact JSON is invalid or exceeds the supported depth") from exc

    @staticmethod
    def _verify_open_file(fd: int, expected) -> None:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ArtifactStorageError("artifact is not a private regular file")
        if before.st_size != expected.size_bytes:
            raise ArtifactStorageError("artifact size does not match its metadata")
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            digest.hexdigest() != expected.sha256
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise ArtifactStorageError("artifact bytes changed or do not match their digest")
        os.lseek(fd, 0, os.SEEK_SET)

    def publish_file(self, staged: StagedArtifact, relative_key: str) -> None:
        """Publish with an exclusive same-filesystem link and fsync both directories."""
        self.verify()
        if staged.artifact_id != staged.observed.id:
            raise ArtifactStorageError("staged artifact identity is invalid")
        source_fd, source_name = self._open_parent(staged.relative_staging_key, create=False)
        target_fd = -1
        root_fd = -1
        file_fd = -1
        try:
            target_parts = _safe_parts(relative_key)
            if target_parts[-1] != "content":
                raise ArtifactStorageError("artifact destination key is invalid")
            root_fd = self._open_root()
            target_fd = self._open_directory_chain(root_fd, target_parts[:-1], create=True)
            file_fd = os.open(source_name, _FILE_FLAGS, dir_fd=source_fd)
            self._verify_open_file(file_fd, staged.observed)
            try:
                os.link(
                    source_name,
                    target_parts[-1],
                    src_dir_fd=source_fd,
                    dst_dir_fd=target_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ArtifactStorageError("artifact destination already exists or cannot be published") from exc
            target_info = os.stat(target_parts[-1], dir_fd=target_fd, follow_symlinks=False)
            source_info = os.fstat(file_fd)
            if (target_info.st_dev, target_info.st_ino) != (source_info.st_dev, source_info.st_ino):
                os.unlink(target_parts[-1], dir_fd=target_fd)
                raise ArtifactStorageError("artifact staging file changed during publication")
            os.fsync(target_fd)
            current_source = os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
            if (current_source.st_dev, current_source.st_ino) != (source_info.st_dev, source_info.st_ino):
                raise ArtifactStorageError("artifact staging file changed during publication")
            os.unlink(source_name, dir_fd=source_fd)
            os.fsync(source_fd)
            self._remove_empty_staging_parents(staged.relative_staging_key)
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise ArtifactStorageError("artifact publication failed") from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            if target_fd >= 0:
                os.close(target_fd)
            if root_fd >= 0:
                os.close(root_fd)
            os.close(source_fd)

    def _remove_empty_staging_parents(self, key: str) -> None:
        parts = _safe_parts(key)
        root_fd = self._open_root()
        try:
            try:
                staging_index = parts.index(".staging")
            except ValueError:
                return
            for count in range(len(parts) - 1, staging_index, -1):
                parent_fd = self._open_directory_chain(root_fd, parts[: count - 1], create=False)
                try:
                    os.rmdir(parts[count - 1], dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                        raise ArtifactStorageError("staging directory cleanup failed") from exc
                finally:
                    os.close(parent_fd)
        finally:
            os.close(root_fd)

    def open_verified(self, artifact) -> BinaryIO:
        """Return an open descriptor only after its regular-file bytes and digest verify."""
        self.verify()
        parent_fd, leaf = self._open_parent(artifact.relative_storage_key, create=False)
        try:
            try:
                fd = os.open(leaf, _FILE_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                raise ArtifactStorageError("artifact file is unavailable") from exc
        finally:
            os.close(parent_fd)
        try:
            self._verify_open_file(fd, artifact)
            return os.fdopen(fd, "rb", closefd=True)
        except Exception:
            os.close(fd)
            raise

    def remove(self, artifact) -> bool:
        """Remove only a matching private regular file; missing files are already removed."""
        self.verify()
        parent_fd, leaf = self._open_parent(
            artifact.relative_storage_key, create=False, missing_ok=True
        )
        if parent_fd is None:
            return False
        try:
            try:
                fd = os.open(leaf, _FILE_FLAGS, dir_fd=parent_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ArtifactStorageError("artifact file is not safe to remove") from exc
            try:
                self._verify_open_file(fd, artifact)
                opened = os.fstat(fd)
                current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise ArtifactStorageError("artifact file changed before removal")
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
                return True
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)

    def remove_staging(self, artifact_id: uuid.UUID, project_id=None, conversation_id=None) -> bool:
        """Remove one artifact's deterministic hidden staging file and empty parents."""
        key = self._staging_key(artifact_id, project_id, conversation_id)
        self.verify()
        parent_fd, leaf = self._open_parent(key, create=False, missing_ok=True)
        if parent_fd is None:
            return False
        try:
            try:
                fd = os.open(leaf, _FILE_FLAGS, dir_fd=parent_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ArtifactStorageError("staging file is not safe to remove") from exc
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ArtifactStorageError("staging file is not a private regular file")
                current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    raise ArtifactStorageError("staging file changed before removal")
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
        self._remove_empty_staging_parents(key)
        return True

    def remove_unpublished(
        self,
        relative_key: str,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> bool:
        """Remove an uncommitted publication without following or trusting its path."""
        self.verify()
        parent_fd, leaf = self._open_parent(relative_key, create=False, missing_ok=True)
        if parent_fd is None:
            return False
        try:
            try:
                fd = os.open(leaf, _FILE_FLAGS, dir_fd=parent_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ArtifactStorageError("unpublished artifact is not safe to remove") from exc
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise ArtifactStorageError("unpublished artifact is not a private regular file")
                if expected_size is not None and opened.st_size != expected_size:
                    raise ArtifactStorageError("unpublished artifact size changed")
                if expected_sha256 is not None:
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                    if digest.hexdigest() != expected_sha256:
                        raise ArtifactStorageError("unpublished artifact digest changed")
                current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise ArtifactStorageError("unpublished artifact changed before removal")
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
                return True
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
