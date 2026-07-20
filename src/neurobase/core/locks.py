"""Small cross-process locks for single-flight project operations."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from neurobase.core import store


def _try_lock(handle: BinaryIO) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def try_file_lock(path: Path) -> Iterator[bool]:
    """Yield whether a non-blocking OS lock was acquired for ``path``.

    The lock file remains on disk, but kernel ownership is tied to the open file
    descriptor and is released automatically if the process exits or crashes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = _try_lock(handle)
    try:
        yield acquired
    finally:
        if acquired:
            _unlock(handle)
        handle.close()


def curate_lock_path(root: Path, project: str) -> Path:
    return store.memory_dir(project, root) / ".locks" / "curate.lock"


def try_curate_lock(root: Path, project: str) -> contextlib.AbstractContextManager[bool]:
    """Non-blocking, per-store/project single-flight lock for curation."""
    return try_file_lock(curate_lock_path(root, project))
