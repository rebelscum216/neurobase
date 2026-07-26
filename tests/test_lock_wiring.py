"""P2-TEST-GAP-001: pin the write lock at the PRODUCTION ENTRY POINTS.

``test_store_lock.py`` proves the lock *primitive* works and ``test_raw_consume_cas.py``
proves the store-level contended-capture rules. Neither proves the wiring: that
every mutating pass actually *takes* the lock around its writes. Codex flagged
that reverting either scribe to a bare ``store.write_raw``, deleting the
``with project_lock(...)`` from the seed importer or MCP ``memory_remember``, or
dropping the CLI's ``blocking_lock=not if_stale`` would change nothing any
existing test asserts — and that the engine's ``skipped-locked`` return is never
executed under coverage.

These tests drive the **real** entry points and fail if a guard is removed.

Technique: ``_LockSpy`` wraps the real ``core.lock.project_lock`` so a test sees
every acquire (memory-dir + blocking flag) and can prove a curated write happened
*inside* a held lock — the atomic writer is patched to record any curated write
made at lock depth 0. Remove an entry point's ``with project_lock(...)`` and the
spy records the write as unlocked, failing the test.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters.claude import scribe as claude_scribe
from neurobase.adapters.codex import scribe as codex_scribe
from neurobase.cli import app
from neurobase.core import lock, projects, store
from neurobase.core.config import Config, McpConfig
from neurobase.core.store_handle import StoreMode
from neurobase.curator import engine
from neurobase.mcp.server import build_server
from neurobase.recommender import seed

PROJECT = "myrepo"


class _LockSpy:
    """Wraps ``core.lock.project_lock`` and the store's atomic writer. Records
    every acquire as ``(memory_dir, blocking)`` and every curated write made
    while no lock is held. Install once with ``_LockSpy(monkeypatch)``; the real
    lock is still taken, so behaviour is unchanged."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real_lock = lock.project_lock
        self.calls: list[tuple[Path, bool]] = []
        self.depth = 0
        self.unlocked_curated_writes: list[Path] = []
        monkeypatch.setattr(lock, "project_lock", self._spy)

        real_write = store._atomic_write_text

        def _guarded_write(path: Path, content: str) -> None:
            if "curated" in Path(path).parts and self.depth == 0:
                self.unlocked_curated_writes.append(Path(path))
            real_write(path, content)

        monkeypatch.setattr(store, "_atomic_write_text", _guarded_write)

    @contextmanager
    def _spy(
        self, memory_dir: Path, *, blocking: bool = True, timeout: float = lock.DEFAULT_TIMEOUT
    ) -> Iterator[Any]:
        self.calls.append((Path(memory_dir), blocking))
        with self._real_lock(memory_dir, blocking=blocking, timeout=timeout) as held:
            self.depth += 1
            try:
                yield held
            finally:
                self.depth -= 1

    def blocking_acquires(self, mem: Path) -> list[bool]:
        return [b for (m, b) in self.calls if m == mem]


def _enable(tmp_path: Path) -> tuple[Path, Path]:
    """A store with ``myrepo`` registered + its git repo (the shape both scribes
    and the CLI resolve a project from)."""
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug=PROJECT)
    store.ensure_tree(PROJECT, root)
    return root, repo


def _mem(root: Path) -> Path:
    return store.memory_dir(PROJECT, root)


def _codex_rollout(path: Path, cwd: str, agent_msg: str) -> Path:
    """§11.2-shaped rollout with a **stable** session-start timestamp — so the
    codex scribe's ordinary write is session-keyed and a second capture would
    overwrite the first unless the guard routes it elsewhere."""
    events = [
        {
            "type": "session_meta",
            "payload": {
                "session_id": "019fsess",
                "id": "019fsess",
                "timestamp": "2026-07-05T23:21:06Z",
                "cwd": cwd,
                "originator": "codex_cli",
                "git": {"commit_hash": "abc123", "branch": "main"},
            },
        },
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Fix it", "images": []},
        },
        {"type": "event_msg", "payload": {"type": "agent_message", "message": agent_msg}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _claude_transcript(path: Path, cwd: str, summary: str) -> Path:
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": cwd,
            "gitBranch": "main",
            "sessionId": "3fc4beef",
            "message": {"role": "user", "content": "Fix the login bug"},
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": summary}]},
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


# --- the scribes: a contended capture must go through write_raw_guarded --------


def test_codex_scribe_routes_a_contended_capture_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a pass holding the lock, the **real** codex scribe must reach for the
    project lock (non-blocking) and, losing it, write a fresh raw rather than
    overwrite the session-keyed one a curator may be consuming. Revert the scribe
    to a bare ``store.write_raw`` and no ``blocking=False`` acquire is recorded."""
    root, repo = _enable(tmp_path)
    spy = _LockSpy(monkeypatch)
    rollout = _codex_rollout(tmp_path / "rollout.jsonl", str(repo), "did the thing")

    with lock.project_lock(_mem(root)):  # a curate pass holds it
        written = codex_scribe.scribe(root, rollout_path=rollout, cwd=str(repo))

    assert written is not None, "the scribe captured nothing; the test proves nothing"
    assert (_mem(root), False) in spy.calls, (
        "the codex scribe did not take the non-blocking project lock — it bypassed the guard"
    )
    assert "__g" in written.name, "a contended capture must land on a fresh generation-suffixed raw"


def test_claude_scribe_routes_a_contended_capture_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guarantee for the claude scribe (identical ``write_raw_guarded``
    call site, separately revertable)."""
    root, repo = _enable(tmp_path)
    spy = _LockSpy(monkeypatch)
    transcript = _claude_transcript(tmp_path / "t.jsonl", str(repo), "fixed the null check")

    with lock.project_lock(_mem(root)):
        written = claude_scribe.scribe(
            root, transcript_path=transcript, cwd=str(repo), reason="Stop"
        )

    assert written is not None, "the scribe captured nothing; the test proves nothing"
    assert (_mem(root), False) in spy.calls, (
        "the claude scribe did not take the non-blocking project lock — it bypassed the guard"
    )
    assert "__g" in written.name, "a contended capture must land on a fresh generation-suffixed raw"


# --- seed import: blocking lock around a WRITE handle's curated writes ----------


def test_seed_import_serializes_its_curated_writes_under_a_blocking_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed importer is a mutating pass: every curated upsert must sit inside
    a **blocking** lock taken on a **WRITE** handle. Delete its
    ``with project_lock(...)`` and the curated write is recorded as unlocked."""
    root, repo = _enable(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "prefer-uv.md").write_text("Prefer uv over pip for installs.\n", encoding="utf-8")

    modes: list[StoreMode] = []
    real_open = seed.open_store

    def _spy_open(root_: Path, mode: StoreMode) -> Any:
        modes.append(mode)
        return real_open(root_, mode)

    monkeypatch.setattr(seed, "open_store", _spy_open)
    spy = _LockSpy(monkeypatch)

    result = seed.import_from_dir(root, PROJECT, src)

    assert result.imported == ["prefer-uv"], "the import wrote no fact; the guard test is void"
    assert spy.unlocked_curated_writes == [], "a curated fact was written outside the project lock"
    assert spy.blocking_acquires(_mem(root)) == [True], (
        "seed import must take exactly one blocking lock on the project memory dir"
    )
    assert StoreMode.WRITE in modes, "seed import must reach the store through a WRITE handle"


# --- MCP memory_remember: blocking lock around its curated write ---------------


def _call_tool(server: Any, tool: str, **args: Any) -> Any:
    result = anyio.run(server.call_tool, tool, args)
    if isinstance(result, tuple):
        return result[1]["result"]
    return json.loads(result[0].text)


def test_mcp_memory_remember_writes_its_curated_fact_under_the_blocking_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``memory_remember`` is a user-directed mutating pass. Its ``_fresh_slug``
    read and ``upsert_curated`` write must be inside one blocking lock, or a
    concurrent curate pass can land the same slug between them."""
    root, repo = _enable(tmp_path)
    server = build_server(root=root, config=Config(mcp=McpConfig()), cwd=repo)
    spy = _LockSpy(monkeypatch)

    res = _call_tool(server, "memory_remember", fact="Prefer uv over pip", project=PROJECT)

    assert res.get("slug"), "memory_remember wrote nothing; the guard test is void"
    assert spy.unlocked_curated_writes == [], "the fact was written outside the project lock"
    assert spy.blocking_acquires(_mem(root)) == [True], (
        "memory_remember must take exactly one blocking lock on the project memory dir"
    )


# --- CLI --if-stale maps to a non-blocking lock; explicit curate blocks ---------


class _FakeBrain:
    name = "fake"

    def plan_json(self, system: str, user: str) -> dict[str, Any]:
        return {"upserts": [], "tombstones": []}

    def text(self, system: str, user: str) -> str:
        return "# Node\n\nbody"


@pytest.mark.parametrize(
    ("args", "expected_blocking"),
    [(["--if-stale"], False), ([], True)],
)
def test_cli_curate_maps_if_stale_to_a_non_blocking_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_blocking: bool,
) -> None:
    """The detached ``curate --if-stale`` freshness pass must request a
    **non-blocking** lock (skip, don't queue); an explicitly typed ``curate``
    must request a **blocking** one. Pins ``blocking_lock=not if_stale``."""
    root, repo = _enable(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_curate(*a: Any, **kw: Any) -> dict[str, Any]:
        captured.update(kw)
        return {"status": "ok"}

    monkeypatch.setattr(cli, "run_curate", _fake_run_curate)
    monkeypatch.setattr(cli, "is_stale", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "resolve_brain", lambda config: (_FakeBrain(), None))

    result = CliRunner().invoke(app, ["curate", "--root", str(root), "--cwd", str(repo), *args])

    assert result.exit_code == 0, result.output
    assert captured, "run_curate was never reached; the mapping is untested"
    assert captured["blocking_lock"] is expected_blocking


# --- engine: the detached pass skips (does not queue) when contended -----------


def test_detached_curate_returns_skipped_locked_when_another_writer_holds_the_lock(
    tmp_path: Path,
) -> None:
    """``blocking_lock=False`` + a held lock must return ``skipped-locked`` — the
    branch no other test exercises. Under blocking it would queue instead."""
    root, _ = _enable(tmp_path)
    with lock.project_lock(_mem(root)):  # another writer holds it
        result = engine.curate(root, PROJECT, _FakeBrain(), blocking_lock=False)

    assert result["status"] == "skipped-locked"
