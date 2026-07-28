"""Curate health read from journal records the **real engine** wrote.

Round 3, F3: the round-2 regressions handcrafted journal dicts, so producer and
consumer could drift apart and every test would still pass. These drive
`engine.curate` for each outcome that reaches the journal, then hand the store to
`doctor` — so the classification contract is enforced end to end rather than
against a fixture's idea of what the engine emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neurobase.brain.base import BrainError
from neurobase.cli import diagnostics
from neurobase.core import projects, store
from neurobase.curator import engine


class _Brain:
    """`plan` and `node_text` may each be an exception, to fail either stage."""

    name = "fake"

    def __init__(self, plan: Any = None, node_text: Any = "# Status\n\nbody") -> None:
        self._plan = plan if plan is not None else {"upserts": [], "tombstones": []}
        self._node_text = node_text

    def plan_json(self, system: str, user: str) -> dict:
        if isinstance(self._plan, Exception):
            raise self._plan
        return self._plan

    def text(self, system: str, user: str) -> str:
        if isinstance(self._node_text, Exception):
            raise self._node_text
        return self._node_text


@pytest.fixture()
def project(tmp_path: Path) -> tuple[Path, Path]:
    """An enabled project doctor can resolve, plus its repo path."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = projects.register_project(root, repo)
    store.ensure_tree(slug, root)
    assert slug == "repo"
    return root, repo


def _write_raw(root: Path, name: str, *, when: str = "2026-07-07T12:00:00Z") -> None:
    store.write_doc(
        store.memory_dir("repo", root) / "raw" / name,
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": when,
            "consumed": False,
            "capture_version": 2,
        },
        "raw body",
    )


#: A plan that actually upserts, so `_synthesize` has something to build from.
#: An empty plan short-circuits synthesis, which would make a "synthesis failed"
#: test silently pass for the wrong reason.
PLAN = {"upserts": [{"slug": "f", "body": "b", "from_raw": ["r1.md"]}], "tombstones": []}


def _journal(root: Path) -> list[dict]:
    path = store.memory_dir("repo", root) / store.CURATOR_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_engine_journals_node_refreshed_on_a_successful_pass(
    project: tuple[Path, Path],
) -> None:
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain(PLAN))

    record = _journal(root)[-1]
    assert record["status"] == "ok"
    assert record["node_refreshed"] is True
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_synthesis_failure_journals_a_frozen_node_and_doctor_agrees(
    project: tuple[Path, Path],
) -> None:
    """`partial` is the real frozen-node case: facts land, synthesis dies."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(
        root,
        "repo",
        _Brain(PLAN, node_text=BrainError("synth boom")),
    )

    record = _journal(root)[-1]
    assert record["status"] == "partial"
    assert record["node_refreshed"] is False

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"  # nothing has ever refreshed the node here
    assert "refreshed the node" in check.detail


def test_resynth_journals_a_refresh_and_clears_health(project: tuple[Path, Path]) -> None:
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(
        root,
        "repo",
        _Brain(PLAN, node_text=BrainError("synth boom")),
    )
    assert diagnostics._curate_health_check(root, repo).status == "error"

    engine.curate(root, "repo", _Brain(), resynth=True)

    record = _journal(root)[-1]
    assert record["status"] == "resynth"
    assert record["node_refreshed"] is True
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_noop_is_journaled_and_stays_neutral(project: tuple[Path, Path]) -> None:
    """A project with nothing to curate is idle, not broken — and the engine
    writes no `node_refreshed` for it, so the neutral status set must decide."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain(PLAN))
    engine.curate(root, "repo", _Brain(PLAN))  # nothing unconsumed left

    records = _journal(root)
    assert records[-1]["status"] == "noop"
    assert "node_refreshed" not in records[-1]
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_failed_dry_run_is_journaled_as_a_non_pass(project: tuple[Path, Path]) -> None:
    """A preview that fails logs `error` but attempts no synthesis, so it must
    not count against node health."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain(PLAN))  # a real success first
    _write_raw(root, "r2.md")

    for _ in range(3):
        engine.curate(root, "repo", _Brain(plan=BrainError("plan boom")), dry_run=True)

    record = _journal(root)[-1]
    assert record["status"] == "error"
    assert record["dry_run"] is True
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_skipped_locked_never_reaches_the_journal(project: tuple[Path, Path]) -> None:
    """Documents the contract round 3 corrected: `skipped-locked` is returned by
    the lock wrapper and never logged, so no journal record can carry it."""
    from neurobase.core import lock
    from neurobase.core.store_handle import StoreMode, open_store

    root, _repo = project
    _write_raw(root, "r1.md")
    handle = open_store(root, StoreMode.WRITE)

    with lock.project_lock(handle.memory_dir("repo"), blocking=True):
        summary = engine.curate(root, "repo", _Brain(), blocking_lock=False)

    assert summary["status"] == "skipped-locked"
    assert all(record["status"] != "skipped-locked" for record in _journal(root))


class _SequencedBrain(_Brain):
    """Plans returned in order, so a *later* batch can fail after an earlier one
    has already committed — the producer shape round-2 F3 was about."""

    def __init__(self, plans: list[Any], node_text: Any = "# Status\n\nbody") -> None:
        super().__init__(node_text=node_text)
        self._plans = iter(plans)

    def plan_json(self, system: str, user: str) -> dict:
        plan = next(self._plans)
        if isinstance(plan, Exception):
            raise plan
        return plan


def test_later_batch_failure_journals_error_with_a_refreshed_node(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Round-4 F3 regression — the producer/consumer seam.**

    When a later batch fails, the engine still synthesizes from the batches that
    committed and *then* logs `error`. Classifying on `status` alone called that
    a frozen node. This drives the real engine into that state and asserts both
    the journal fields and doctor's verdict, so producer and consumer cannot
    drift apart silently.
    """
    root, repo = project
    _write_raw(root, "r1.md")
    _write_raw(root, "r2.md")
    # Force one raw per plan batch so the second call is a separate batch.
    monkeypatch.setattr(engine, "_next_plan_batch", _one_raw_per_batch(engine._next_plan_batch))

    engine.curate(
        root,
        "repo",
        _SequencedBrain(
            [
                {
                    "upserts": [{"slug": "landed", "body": "yes", "from_raw": ["r1.md"]}],
                    "tombstones": [],
                },
                BrainError("batch two failed"),
            ]
        ),
    )

    record = _journal(root)[-1]
    assert record["status"] == "error"
    assert record["node_refreshed"] is True, "synthesis ran over the committed batch"

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok", "a refreshed node is not frozen, whatever `status` says"


def _one_raw_per_batch(original):
    """Wrap `_next_plan_batch` so each batch carries exactly one raw."""

    def _wrapped(curated, remaining, max_bytes):
        docs, payload = original(curated, remaining[:1], max_bytes)
        return docs, payload

    return _wrapped
