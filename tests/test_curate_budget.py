"""Tests for the curation pass budget (P0, 2026-07-17 runaway incident) —
``curator/budget.py`` and its enforcement inside ``curator/engine.py``.

Covers the incident note's required regression item 5, "a large raw backlog
cannot exceed the configured automatic call, raw, chunk, or time budget", plus
the properties that make a bounded stop *safe* rather than merely bounded:
remaining raws stay unconsumed, the pass is retryable and makes forward
progress, and exhaustion never reports an error status (which the CLI would
turn into a nonzero exit, breaking the hooks-always-exit-zero guarantee).

Brain calls are counted with the existing `FakeBrain`, which already tracks
`plan_calls`/`text_calls`; the wall-clock ceiling uses an injected fake clock so
nothing here depends on real elapsed time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from neurobase.core import store
from neurobase.core.config import CurateConfig
from neurobase.curator import budget as budget_mod
from neurobase.curator import distill as distill_mod
from neurobase.curator import engine

# Flat cross-test import, as `test_redact_audit` does: ruff's `src` includes
# `tests`, so this is first-party and sorts with the neurobase imports.
from test_curator import FakeBrain, SequencedBrain, _write_raw


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store"


def _seed(root: Path, project: str, n: int) -> None:
    """`n` unconsumed raws, oldest first by filename timestamp."""
    for i in range(n):
        _write_raw(root, project, f"2026-07-07T12-00-{i:02d}Z_claude_s{i:04d}.md", body=f"raw {i}")


def _budget(**overrides: int) -> budget_mod.PassBudget:
    """A permissive budget with one ceiling lowered per test, so each test
    proves the dimension it names and not an unrelated one."""
    base: dict[str, int] = {
        "max_raws": 1000,
        "max_brain_calls": 1000,
        "max_brain_attempts": 10_000,
        "max_distill_chunks": 1000,
        "max_seconds": 10_000,
    }
    base.update(overrides)
    return budget_mod.PassBudget(**base)  # type: ignore[arg-type]


# --- the ceilings actually bind ---------------------------------------------


def test_max_raws_caps_the_backlog_a_pass_considers(root: Path) -> None:
    """Required regression item 5. 25 raws behind a ceiling of 10: exactly 10
    are considered, and the 15 deferred are reported rather than silently
    dropped."""
    _seed(root, "proj", 25)
    brain = FakeBrain()

    summary = engine.curate(root, "proj", brain, pass_budget=_budget(max_raws=10))

    assert summary["raw"] == 10
    assert summary["backlog"] == 25
    assert summary["budget_deferred_raws"] == 15
    assert summary["budget_stopped_by"] == "max_raws"
    assert summary["unconsumed_left"] == 15


def test_deferred_raws_are_left_unconsumed_on_disk(root: Path) -> None:
    """The load-bearing safety property: raws past the ceiling are dropped
    before the batch loop, so they cannot reach `mark_consumed`. Verified on
    disk, not just in the summary."""
    _seed(root, "proj", 25)

    engine.curate(root, "proj", FakeBrain(), pass_budget=_budget(max_raws=10))

    still_unconsumed = store.list_raw(root, "proj", unconsumed_only=True)
    assert len(still_unconsumed) == 15


# The deterministic call generator these ceilings are measured against. The
# seeded raws carry no `transcript_path`, so distillation makes no calls of its
# own, and `_synthesize` only calls the brain when active facts exist — so a
# default pass is a single plan call and would not exercise a ceiling at all.
# Capping the plan payload splits the backlog one raw per batch, making N raws
# cost exactly N plan calls.
#
# Measured for these fixtures: one raw serializes to 1260 bytes and two to 1323,
# so 1300 admits exactly one. Not a magic constant to copy elsewhere — if the
# payload shape changes, the exact call-count assertions below fail loudly
# rather than silently testing nothing.
ONE_RAW_PER_BATCH = 1300


def test_max_brain_calls_stops_the_pass(root: Path) -> None:
    """A call ceiling below what the backlog needs stops the pass, and the fake
    brain proves the ceiling was honoured: total calls never exceed it."""
    _seed(root, "proj", 40)
    brain = FakeBrain()

    engine.curate(
        root,
        "proj",
        brain,
        plan_payload_max_bytes=ONE_RAW_PER_BATCH,
        pass_budget=_budget(max_brain_calls=8),
    )

    assert brain.plan_calls + brain.text_calls == 8


def test_wall_clock_ceiling_stops_the_pass_deterministically(root: Path) -> None:
    """The time ceiling, driven by an injected clock — no real sleeping. The
    clock reads under the deadline once, then jumps past it, so the pass stops
    mid-flight on the second call rather than at a raw boundary by luck."""
    _seed(root, "proj", 20)
    ticks = iter([0.0, 0.0] + [999.0] * 500)
    pass_budget = budget_mod.PassBudget(
        max_raws=1000,
        max_brain_calls=1000,
        max_brain_attempts=10_000,
        max_distill_chunks=1000,
        max_seconds=60,
        clock=lambda: next(ticks),
    )
    brain = FakeBrain()

    summary = engine.curate(
        root, "proj", brain, plan_payload_max_bytes=ONE_RAW_PER_BATCH, pass_budget=pass_budget
    )

    assert pass_budget.stopped_by == "max_seconds"
    assert brain.plan_calls == 1  # stopped on the second debit
    assert summary["status"] != "error"


def test_attempt_ceiling_accounts_for_retries_below_the_protocol(root: Path) -> None:
    """`call_with_retry` lives inside each backend, below the Brain protocol, so
    one logical call can be two subprocesses. The attempt ceiling is therefore
    charged as calls x (retries + 1): a ceiling of 6 permits 3 calls, not 6."""
    _seed(root, "proj", 20)
    brain = FakeBrain()

    engine.curate(
        root,
        "proj",
        brain,
        plan_payload_max_bytes=ONE_RAW_PER_BATCH,
        pass_budget=_budget(max_brain_attempts=6),
    )

    assert brain.plan_calls + brain.text_calls == 3


# --- a bounded stop is not an error -----------------------------------------


def test_budget_stop_never_reports_error_status(root: Path) -> None:
    """Exhaustion must not surface as `status: error` — `cli/__init__.py` turns
    that into exit 1, which would break the hooks-always-exit-zero guarantee
    for what is a normal bounded outcome."""
    _seed(root, "proj", 40)

    summary = engine.curate(root, "proj", FakeBrain(), pass_budget=_budget(max_raws=5))

    assert summary["status"] != "error"
    assert "error" not in summary


def test_a_bounded_pass_still_makes_progress_and_is_retryable(root: Path) -> None:
    """The anti-livelock property. A bounded pass must consume what it did
    process, so repeated passes drain the backlog instead of replaying the same
    prefix forever."""
    _seed(root, "proj", 12)

    first = engine.curate(root, "proj", FakeBrain(), pass_budget=_budget(max_raws=5))
    assert first["raw"] == 5
    assert len(store.list_raw(root, "proj", unconsumed_only=True)) == 7

    second = engine.curate(root, "proj", FakeBrain(), pass_budget=_budget(max_raws=5))
    assert second["raw"] == 5
    assert len(store.list_raw(root, "proj", unconsumed_only=True)) == 2

    third = engine.curate(root, "proj", FakeBrain(), pass_budget=_budget(max_raws=5))
    assert third["raw"] == 2
    assert store.list_raw(root, "proj", unconsumed_only=True) == []


def test_distill_exhaustion_still_leaves_calls_for_planning(root: Path, tmp_path: Path) -> None:
    """The reserve, and the anti-livelock property it exists for.

    If distillation could spend the whole call budget, planning would never run,
    nothing would be consumed, and every later pass would replay the same prefix
    — a backlog that silently never drains. Distill is capped at
    `max_brain_calls - reserve_calls`, so a batch still commits.

    These raws carry real transcripts so distillation actually calls the brain;
    without one it is a no-op and the reserve would never be under pressure.
    """
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"}}\n', encoding="utf-8"
    )
    store.ensure_tree("proj", root)
    for i in range(10):
        store.write_doc(
            store.memory_dir("proj", root) / "raw" / f"2026-07-07T12-00-{i:02d}Z_claude_s{i}.md",
            {
                "agent": "claude",
                "session_id": f"s{i}",
                "cwd": "/x",
                "branch": "main",
                "captured_at": "2026-07-07T12:00:00Z",
                "consumed": False,
                "transcript_path": str(transcript),
                "capture_version": 2,
            },
            f"skim {i}",
        )

    pass_budget = budget_mod.PassBudget(
        max_raws=1000,
        max_brain_calls=8,
        max_brain_attempts=10_000,
        max_distill_chunks=1000,
        max_seconds=10_000,
        reserve_calls=6,
    )
    brain = FakeBrain()

    engine.curate(root, "proj", brain, pass_budget=pass_budget)

    # Distill was held to the allowance (8 - 6 = 2), not allowed to eat all 8.
    assert pass_budget.distill_calls <= 2
    # Planning still ran on the reserve despite the tight overall ceiling...
    assert brain.plan_calls >= 1
    # ...so the pass committed and the backlog actually drains.
    assert len(store.list_raw(root, "proj", unconsumed_only=True)) < 10


# --- misconfiguration fails closed ------------------------------------------


@pytest.mark.parametrize(
    "knob",
    ["max_raws", "max_brain_calls", "max_brain_attempts", "max_distill_chunks", "max_seconds"],
)
@pytest.mark.parametrize("value", [0, -1])
def test_a_nonpositive_ceiling_is_rejected(knob: str, value: int) -> None:
    """A zero or negative ceiling is a misconfiguration, not a request for "no
    bound". Silently treating it as unlimited would disable the very guard this
    module exists to provide."""
    with pytest.raises(ValueError, match=knob):
        _budget(**{knob: value})


def test_reserve_must_leave_room_for_distillation() -> None:
    """A reserve at or above the call ceiling would leave a zero distill
    allowance, silently reducing every pass to skim-only."""
    with pytest.raises(ValueError, match="reserve_calls"):
        budget_mod.PassBudget(
            max_raws=10,
            max_brain_calls=6,
            max_brain_attempts=100,
            max_distill_chunks=10,
            max_seconds=60,
            reserve_calls=6,
        )


# --- tier selection ----------------------------------------------------------


def test_automatic_tier_is_much_smaller_than_the_explicit_one() -> None:
    """Hook-triggered passes get the small ceilings; an explicitly typed
    command gets the permissive ones. The asymmetry is the point: automatic
    spending is what burned the usage window."""
    cfg = CurateConfig()
    auto = budget_mod.from_config(cfg, automatic=True)
    explicit = budget_mod.from_config(cfg, automatic=False)

    assert auto.max_raws < explicit.max_raws
    assert auto.max_brain_calls < explicit.max_brain_calls
    assert auto.max_seconds < explicit.max_seconds


def test_curate_without_an_explicit_budget_is_still_bounded(root: Path) -> None:
    """There is no unbounded path: a caller that passes no budget gets the
    explicit tier rather than no ceiling at all."""
    _seed(root, "proj", 3)
    summary = engine.curate(root, "proj", FakeBrain())

    assert summary["status"] != "error"
    assert "budget_calls" in summary


# --- regressions for Codex round-1 findings (2026-07-20) ---------------------
#
# Both reproduce the reviewer's exact repro steps from
# docs/reviews/2026-07-20-curate-pass-budget.md rather than a paraphrase of
# them, so a regression here means the finding is genuinely back.


def _seed_one_curated_fact(root: Path, project: str) -> None:
    """`_synthesize` only calls the brain when at least one active curated fact
    exists — with none, it writes a fixed placeholder body and never touches
    the brain at all. The resynth-budget tests need a real brain call to
    charge (or fail to charge), so they seed one fact first."""
    store.ensure_tree(project, root)
    store.upsert_curated(root, project, "some-fact", "A durable fact.", provenance=["raw/x.md"])


def test_resynth_brain_call_is_budgeted(root: Path) -> None:
    """F1: `resynth=True` used to return before the `BudgetedBrain` rebind, so
    the single `_synthesize` call it makes was never charged to the ledger —
    the one path that contradicted "every brain call must debit". A budgeted
    resynth must show exactly one spent call, not zero."""
    _seed_one_curated_fact(root, "proj")
    pass_budget = _budget()

    engine.curate(root, "proj", FakeBrain(), resynth=True, pass_budget=pass_budget)

    assert pass_budget.calls == 1


def test_resynth_exhaustion_reports_partial_not_a_silent_success(root: Path) -> None:
    """A budget too small for even the one resynth call must not report
    `status: "resynth"` (which claims the node was refreshed) or `error`
    (which the CLI turns into exit 1) — `partial` is the honest outcome: the
    node was not refreshed and the caller should not treat it as done."""
    _seed_one_curated_fact(root, "proj")
    pass_budget = _budget(max_brain_calls=1, reserve_calls=0)
    pass_budget.debit(distilling=False)  # pre-spend the only call, via the same
    # public method the code itself uses — not a private-field poke.

    summary = engine.curate(root, "proj", FakeBrain(), resynth=True, pass_budget=pass_budget)

    assert summary["status"] == "partial"
    assert summary["status"] != "error"


def test_synthesis_exhaustion_after_committed_batches_reports_partial(
    root: Path, tmp_path: Path
) -> None:
    """F2: reproduces the reviewer's exact repro. `max_brain_calls=7`,
    `reserve_calls=6`, one-raw plan batches: 7 raws each cost one plan call,
    exactly exhausting the budget with nothing left for synthesis. The first
    batch's plan upserts a real fact, so an active curated fact exists by the
    time synthesis runs and `_synthesize` genuinely attempts a brain call
    rather than taking its no-active-facts shortcut — otherwise this would not
    reproduce the reviewer's "no status node was written" observation at all.

    Before the fix this was swallowed and reported `status: "ok"` even though
    the node was never resynthesized against the newly committed fact — the
    exact D22 hazard the surrounding comment warns about ("derived state must
    never lag committed facts"). It must report `partial`, and the raws must
    still be consumed (committed batches stand; only synthesis was skipped)."""
    _seed(root, "proj", 7)
    plans: list[Any] = [{"upserts": [{"slug": "a-fact", "body": "A fact."}], "tombstones": []}]
    plans += [{"upserts": [], "tombstones": []}] * 6
    pass_budget = budget_mod.PassBudget(
        max_raws=1000,
        max_brain_calls=7,
        max_brain_attempts=10_000,
        max_distill_chunks=1000,
        max_seconds=10_000,
        reserve_calls=6,
    )

    summary = engine.curate(
        root,
        "proj",
        SequencedBrain(plans),
        plan_payload_max_bytes=1300,
        pass_budget=pass_budget,
    )

    assert summary["batches"] == 7  # every batch committed...
    assert summary["upserts"] == 1  # ...including the fact the first one wrote
    assert summary["status"] == "partial"  # ...but the node is honestly stale
    assert summary["status"] != "ok"
    assert len(store.list_raw(root, "proj", unconsumed_only=True)) == 0


# --- Codex round-2 finding (2026-07-20) --------------------------------------


_GOOD_DIGEST = "## Decisions\n- Chose X over Y."


class _OneShotDistillBrain:
    """Returns a valid digest once; only used for the one raw whose distill
    call is meant to succeed before the budget stops the rest. `plan_json` is
    unused here but required to satisfy the `Brain` protocol."""

    name = "fake"

    def plan_json(self, system: str, user: str) -> dict:
        raise AssertionError("plan_json should not be called in this distill-only test")

    def text(self, system: str, user: str) -> str:
        return _GOOD_DIGEST


def _seed_transcript_raw(root: Path, project: str, name: str, transcript: Path) -> None:
    store.ensure_tree(project, root)
    store.write_doc(
        store.memory_dir(project, root) / "raw" / name,
        {
            "agent": "claude",
            "session_id": name,
            "cwd": "/x",
            "branch": "main",
            "captured_at": "2026-07-07T12:00:00Z",
            "consumed": False,
            "transcript_path": str(transcript),
            "capture_version": 2,
        },
        f"skim {name}",
    )


def test_distill_budget_exhaustion_stops_the_loop_immediately(root: Path, tmp_path: Path) -> None:
    """F3: `_distill_one` has its own broad `except Exception` (for
    document-local failures), and `BudgetExhausted` is an `Exception` but NOT a
    `BrainError` — so the pre-fix code's `except BrainError: raise` never saw
    it, and the generic handler silently swallowed it, returning `None` (skim)
    for that one raw and letting `distill_docs`'s loop carry on to the next raw
    instead of its intended loop-level breaker firing.

    The bug is invisible in the returned digests/counts: buggy and fixed code
    both end up skimming the same two raws. What differs is how much work
    happened getting there, so this counts DEBIT ATTEMPTS via the injected
    clock (`_check_clock()` runs at the top of every `debit()` call, success or
    failure) rather than asserting on `out`/`counts`. With the loop-level
    breaker firing correctly, raw 3 must never be attempted at all once raw 2's
    debit already failed — one fewer clock read than the buggy version."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"}}\n', encoding="utf-8"
    )
    for i in range(3):
        _seed_transcript_raw(root, "proj", f"2026-07-07T12-00-{i:02d}Z_claude_s{i}.md", transcript)
    docs = store.list_raw(root, "proj", unconsumed_only=True)
    assert len(docs) == 3

    clock_calls: list[float] = []

    def counting_clock() -> float:
        clock_calls.append(0.0)
        return 0.0

    pass_budget = budget_mod.PassBudget(
        max_raws=1000,
        max_brain_calls=1000,
        max_brain_attempts=10_000,
        max_distill_chunks=1,  # only the FIRST distill call is allowed
        max_seconds=10_000,
        clock=counting_clock,
    )
    budgeted = budget_mod.BudgetedBrain(_OneShotDistillBrain(), pass_budget).for_distill()

    out, counts = distill_mod.distill_docs(root, "proj", docs, budgeted, write_cache=False)

    assert counts == {"distilled": 1, "fallback": 2}
    assert len(out) == 3
    # 1 (PassBudget.__post_init__) + 1 (raw 1's successful debit) + 1 (raw 2's
    # failing debit). Raw 3 must never be reached: no 4th clock read.
    assert len(clock_calls) == 3
