"""Deterministic probe for the per-project store write lock (ADR-0023 / G4).

The mutual-exclusion tests use real OS processes (``multiprocessing``), because
the property under test — cross-process serialization of store mutations — is
exactly what threads-in-one-process cannot demonstrate. Each worker runs a
non-atomic read-modify-write against a shared counter file with a widened race
window; under the lock the final count is exact, and the deliberately-unlocked
control corrupts (proving the probe has teeth).

Ownership is bound to an open OS handle via ``filelock``, so there is no token,
heartbeat, staleness, or takeover machinery to test — that machinery was the
first draft's bug source. What is tested instead is what that draft *failed*: a
holder that dies mid-critical-section must release, a successor's lock must
never be clobbered by a predecessor, and a pre-existing empty lockfile must not
wedge acquire.

``project_lock`` takes the project's **memory directory**, not a root+project
pair — production callers pass ``handle.memory_dir(project)`` so the lock never
becomes a second, unvalidated way to name a store path (ADR-0015 chokepoint).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from pathlib import Path

import pytest

from neurobase.core.lock import (
    LockContended,
    LockTimeout,
    lock_path,
    project_lock,
)
from neurobase.core.store import memory_dir

PROJECT = "probe"
_CTX = mp.get_context("spawn")  # match the macOS/Windows default explicitly


# --- workers (top-level so spawn can pickle them) --------------------------


def _rmw(counter: Path) -> None:
    """One non-atomic increment with a widened window between read and write.
    Tolerant of a torn read (empty/garbage) so the unlocked control undercounts
    rather than crashing — a lost update is the corruption we want to show."""
    try:
        value = int(counter.read_text())
    except (FileNotFoundError, ValueError):
        value = 0
    time.sleep(0.0006)  # widen the lost-update window
    counter.write_text(str(value + 1))


def _locked_worker(mem: str, counter: str, iters: int) -> None:
    for _ in range(iters):
        with project_lock(Path(mem), timeout=30.0):
            _rmw(Path(counter))


def _unlocked_worker(counter: str, iters: int) -> None:
    for _ in range(iters):
        _rmw(Path(counter))


def _suicide_holder(mem: str, ready: str) -> None:
    """Acquire, signal that we hold it, then die *inside* the critical section
    without releasing — the killed-holder case that needs no stale policy."""
    with project_lock(Path(mem), timeout=30.0):
        Path(ready).write_text("held")
        time.sleep(30)  # never reached; the parent kills us


def _run(target, args, procs: int) -> None:
    workers = [_CTX.Process(target=target, args=args) for _ in range(procs)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
        assert w.exitcode == 0, f"worker exited {w.exitcode}"


@pytest.fixture
def mem(tmp_path: Path) -> Path:
    """The project's memory dir — what production passes from its handle."""
    return memory_dir(PROJECT, tmp_path)


# --- mutual exclusion ------------------------------------------------------


def test_lock_serializes_concurrent_writers(mem: Path) -> None:
    """8 processes × 40 increments == 320 exactly, under the lock."""
    counter = mem / "counter"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("0")
    procs, iters = 8, 40
    _run(_locked_worker, (str(mem), str(counter), iters), procs)
    assert int(counter.read_text()) == procs * iters


def test_probe_has_teeth_without_lock(tmp_path: Path) -> None:
    """Control: the SAME workload without the lock loses updates. If this ever
    passes at the exact total, the probe above proves nothing."""
    counter = tmp_path / "counter"
    counter.write_text("0")
    procs, iters = 8, 40
    _run(_unlocked_worker, (str(counter), iters), procs)
    assert int(counter.read_text()) < procs * iters


# --- non-blocking skip vs blocking wait ------------------------------------


def test_non_blocking_acquire_skips_when_held(mem: Path) -> None:
    with (
        project_lock(mem),
        pytest.raises(LockContended),
        project_lock(mem, blocking=False),
    ):
        pass


def test_blocking_acquire_times_out_when_held(mem: Path) -> None:
    with (
        project_lock(mem),
        pytest.raises(LockTimeout),
        project_lock(mem, timeout=0.2),
    ):
        pass


def test_lock_is_released_on_context_exit(mem: Path) -> None:
    with project_lock(mem):
        pass
    with project_lock(mem, blocking=False):
        pass  # no residue blocks a second acquire


def test_lock_is_released_when_the_body_raises(mem: Path) -> None:
    with pytest.raises(RuntimeError), project_lock(mem):
        raise RuntimeError("pass exploded mid-critical-section")
    with project_lock(mem, blocking=False):
        pass  # an exception must not strand the lock


# --- killed holder: the OS releases, no stale policy needed ----------------


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX-only")
def test_killed_holder_releases_the_lock(mem: Path) -> None:
    """A holder killed inside the critical section must not wedge the store.
    With handle-bound ownership the kernel drops the lock — no heartbeat, no
    timeout, no takeover."""
    ready = mem.parent / "ready"
    ready.parent.mkdir(parents=True, exist_ok=True)
    holder = _CTX.Process(target=_suicide_holder, args=(str(mem), str(ready)))
    holder.start()
    for _ in range(600):  # wait for it to actually hold the lock
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.exists(), "holder never acquired"

    # genuinely held while alive
    with pytest.raises(LockContended), project_lock(mem, blocking=False):
        pass

    assert holder.pid is not None
    os.kill(holder.pid, signal.SIGKILL)
    holder.join(30)

    with project_lock(mem, timeout=10.0):
        pass  # reclaimed by the OS, not by us


def test_pre_existing_empty_lockfile_does_not_wedge_acquire(mem: Path) -> None:
    """The first draft spun forever on an empty lockfile, never reaching the
    deadline or the non-blocking raise. An unlocked file on disk is not
    ownership."""
    path = lock_path(mem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")  # crashed-mid-create residue
    with project_lock(mem, timeout=5.0):
        pass


def test_garbage_lockfile_does_not_wedge_acquire(mem: Path) -> None:
    path = lock_path(mem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all")
    with project_lock(mem, timeout=5.0):
        pass


# --- successor safety: a predecessor must never clobber a live holder ------


def _hold_briefly(mem: str, ready: str) -> None:
    with project_lock(Path(mem), timeout=30.0):
        Path(ready).write_text("held")
        time.sleep(1.5)


def test_predecessor_release_cannot_steal_a_successors_lock(mem: Path) -> None:
    """In the first draft, a stale holder's release/heartbeat could unlink or
    overwrite a *successor's* lock, putting two processes in the critical
    section. Ownership bound to a handle makes a predecessor's release affect
    only its own handle."""
    ready = mem.parent / "ready"
    ready.parent.mkdir(parents=True, exist_ok=True)
    successor = _CTX.Process(target=_hold_briefly, args=(str(mem), str(ready)))

    with project_lock(mem):
        pass  # predecessor acquires and releases cleanly

    successor.start()
    for _ in range(600):
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.exists(), "successor never acquired"

    # While the successor holds it, no one else may enter — the predecessor's
    # earlier release must not have left a usable claim behind.
    with pytest.raises(LockContended), project_lock(mem, blocking=False):
        pass

    successor.join(30)
    assert successor.exitcode == 0


# --- placement -------------------------------------------------------------


def test_lockfile_sits_outside_every_store_glob(mem: Path) -> None:
    with project_lock(mem):
        assert (mem / ".lock").exists()
        assert (mem / ".lock").name.startswith(".")
        # not matched by any *.md glob the store walks
        assert list(mem.glob("*.md")) == []
