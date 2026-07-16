from __future__ import annotations

from collections.abc import Callable
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4


class RecoveryLock:
    def __init__(
        self,
        path: Path,
        handle: io.BufferedRandom,
        operation_id: str,
        identity: tuple[int, int, int, int],
    ):
        self.path = path
        self.handle = handle
        self.operation_id = operation_id
        self.identity = identity

    @classmethod
    def acquire(cls, path: Path, metadata: dict[str, object]) -> RecoveryLock | None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if not _try_lock_handle(handle):
                handle.close()
                return None
            encoded = json.dumps(metadata).encode("utf-8")
            handle.seek(0)
            handle.truncate()
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            return cls(
                path,
                handle,
                str(metadata["operation_id"]),
                _file_identity(path.stat()),
            )
        except Exception:
            if not handle.closed:
                handle.close()
            raise

    def release(self, *, before_unlink: Callable[[], None] | None = None) -> None:
        try:
            self.handle.seek(0)
            raw = self.handle.read().decode("utf-8")
            payload = json.loads(raw)
            if before_unlink is not None:
                before_unlink()
            unchanged = (
                payload.get("operation_id") == self.operation_id
                and self.path.exists()
                and _file_identity(self.path.stat()) == self.identity
            )
            if unchanged:
                try:
                    self.path.unlink()
                except OSError:
                    pass
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        finally:
            _unlock_handle(self.handle)
            self.handle.close()


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def durable_copy_with_hash(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        source_hash = _sha256(source)
        if _sha256(temporary) != source_hash:
            raise OSError("Forensic candidate temporary checksum mismatch")
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        if _sha256(destination) != source_hash:
            raise OSError("Forensic candidate destination checksum mismatch")
        return source_hash
    finally:
        temporary.unlink(missing_ok=True)


def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _try_lock_handle(handle: io.BufferedRandom) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        elif os.name == "posix":
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB  # type: ignore[attr-defined]
            )
        else:
            return False
    except (ImportError, OSError):
        return False
    return True


def _unlock_handle(handle: io.BufferedRandom) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except (ImportError, OSError):
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
