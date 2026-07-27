"""Per-project advisory store write lock (ADR-0023 / known-gaps G4).

A lock at ``memory_dir(project, root)/.lock`` serializes every *mutating* pass
over a single project's store (curator, seed importer, MCP ``memory_remember``).
**Readers never take it.** The lockfile is a dotfile at the memory-dir root,
outside every store glob (``curated/*.md``, ``.tombstones/*.md``, ``nodes/*.md``),
so it is invisible to readers by construction.

**Ownership is bound to an open OS handle** — ``filelock.FileLock`` selects
``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows. This is the whole
design: the kernel, not this module, decides who holds the lock, and it releases
automatically when the holding process dies.

That property is load-bearing, and it is why the first draft of this module was
rejected. That draft hand-rolled ownership as an ``O_EXCL`` lockfile carrying a
token, with heartbeats and a stale-takeover protocol. Codex's review
demonstrated that a *check the token, then mutate the path* sequence is not
mutual exclusion: a stale holder's heartbeat could ``os.replace`` over a
successor's lock, a stale ``release()`` could unlink a successor's lock, and an
empty lockfile left by a crash in the create/write window wedged ``acquire()``
in an infinite loop that never reached its deadline. Binding ownership to a
handle deletes that entire class of bug along with the token, heartbeat,
staleness, and takeover machinery that produced it.

**Killed holders need no policy.** The OS drops the lock when the process dies,
so there is no stale-lock timeout, no PID liveness probe, and no takeover race.

**Why this module takes a memory-dir path, not a ``root``+``project`` pair.**
The ADR-0015 chokepoint requires production code to reach the store through a
validated ``StoreHandle``. A lock module that resolved ``memory_dir(project,
root)`` itself would be a second, unvalidated way to name a store path — exactly
what the chokepoint exists to prevent — and would need a CI-guard exemption to
survive. Instead the caller passes the directory it already got from its handle
(``handle.memory_dir(project)``), and ``write_raw_guarded`` takes the handle
itself. The lock stays a path-level primitive with no opinion about store layout,
and the guard needs no new exemption.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock, Timeout

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from neurobase.core.store_handle import StoreHandle

# Ceiling for a user-intent blocking acquire before it surfaces LockTimeout.
DEFAULT_TIMEOUT = 30.0
LOCK_NAME = ".lock"


class LockContended(RuntimeError):
    """A non-blocking acquire failed: another process holds the lock."""


class LockTimeout(RuntimeError):
    """A blocking acquire exceeded its timeout without acquiring."""


def lock_path(memory_dir: Path) -> Path:
    """The project's lockfile, given that project's memory directory."""
    return memory_dir / LOCK_NAME


@contextmanager
def project_lock(
    memory_dir: Path,
    *,
    blocking: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> Iterator[FileLock]:
    """Hold the per-project write lock for the duration of a mutating pass.

    ``memory_dir`` is the project's memory directory — obtain it from a validated
    handle (``handle.memory_dir(project)``), never by resolving a raw root.

    ``blocking=True`` (user-intent ops: explicit curate, ``memory_remember``,
    seed import) waits up to ``timeout`` then raises ``LockTimeout``.
    ``blocking=False`` (detached ``curate --if-stale``) raises ``LockContended``
    immediately so the background freshness pass **skips** rather than queues.

    The lock is re-entrant per ``FileLock`` instance but each call here builds a
    fresh instance, so a nested acquire in the same process blocks like any
    other contender — passes must not nest.
    """
    path = lock_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path))
    try:
        lock.acquire(timeout=timeout if blocking else 0)
    except Timeout as exc:
        if not blocking:
            raise LockContended(f"{path} is held by another process") from exc
        raise LockTimeout(f"could not acquire {path} within {timeout}s") from exc
    try:
        yield lock
    finally:
        lock.release()


def write_raw_guarded(handle: StoreHandle, project: str, **kwargs: Any) -> Path:
    """The scribe capture path (ADR-0023, P1-DATA-INTEGRITY-002).

    Scribes run inside agent hooks, which must stay within the ADR-0003 latency
    budget and exit 0 — so they can neither block on the project lock nor fail
    when it is held. But they must also never overwrite the raw a curator pass
    is mid-consume on: ``mark_consumed`` reads a raw, then writes it back, and a
    scribe landing between those two steps loses its capture outright.

    So: try the lock **non-blocking**. Winning it proves no curate pass is in
    flight, and the ordinary session-keyed overwrite is safe. Losing it means a
    pass may be consuming this very file, so write a *fresh* filename instead
    (``unique=True``) and leave the contended path untouched. Either way the
    hook returns immediately and the capture survives.

    This is the same shape the Codex scribe already used for
    ``RawConsumedError`` — a fresh capture under a new filename, per spec §1's
    mutability rule — extended to the contended case. Callers keep their own
    ``RawConsumedError`` handling; this wrapper only adds the lock guard.

    Takes the WRITE ``handle`` the caller already opened, so the write still
    goes through the ADR-0015 chokepoint.
    """
    try:
        with project_lock(handle.memory_dir(project), blocking=False):
            return handle.write_raw(project, **kwargs)
    except LockContended:
        # A contended capture is a *fresh* capture: stamp it now rather than
        # reusing the caller's key. Codex passes its session-start time, so
        # reusing it would name a brand-new capture with an hours-old timestamp
        # and sort it in among stale raws, breaking list_raw's oldest-first
        # contract corpus-wide (P1-CORRECTNESS-006). This matches spec §1's
        # existing `RawConsumedError` rule — "retry with captured_at=now".
        fresh = {**kwargs, "captured_at": datetime.now(UTC)}
        return handle.write_raw(project, **fresh, unique=True)
