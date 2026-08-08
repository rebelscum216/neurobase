"""Tests for config loading (spec §10 keys, §8 defaults)."""

from __future__ import annotations

from pathlib import Path

from neurobase.core.config import load_config


def test_missing_file_returns_all_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.store.root == "~/neurobase"
    assert cfg.brain.backend == "auto"
    assert cfg.brain.timeout_seconds == 120
    assert cfg.curate.stale_hours == 12
    assert cfg.curate.tombstone_grace_days == 14
    assert cfg.curate.plan_payload_max_bytes == 262_144
    assert cfg.inject.max_chars == 6000
    assert cfg.inject.sources == ["startup", "clear"]
    assert cfg.redact.extra_patterns == []
    # Phase 8 recommender defaults (spec §12.11, ADR-0007 D17/D18).
    assert cfg.recommend.min_occurrences == 3
    assert cfg.recommend.min_breadth_sessions == 2
    assert cfg.recommend.recency_halflife_days == 30
    assert cfg.recommend.raw_lookback_days == 30
    assert cfg.recommend.raw_cap_per_project == 200
    assert cfg.recommend.near_duplicate_threshold == 0.6
    assert cfg.recommend.survival_window_days == 30


def test_partial_overrides_keep_other_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[store]\nroot = "/custom/root"\n')
    cfg = load_config(path)
    assert cfg.store.root == "/custom/root"
    assert cfg.brain.backend == "auto"  # untouched section still defaults


def test_full_override(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[store]
root = "/x"

[brain]
backend = "codex-cli"
model = "custom-model"
timeout_seconds = 30

[curate]
stale_hours = 6
tombstone_grace_days = 7
plan_payload_max_bytes = 123456

[inject]
max_chars = 1000
sources = ["startup"]

[redact]
extra_patterns = ["foo-\\\\d+"]

[recommend]
min_occurrences = 5
raw_lookback_days = 7
raw_cap_per_project = 50
near_duplicate_threshold = 0.8
"""
    )
    cfg = load_config(path)
    assert cfg.brain.backend == "codex-cli"
    assert cfg.brain.timeout_seconds == 30
    assert cfg.curate.stale_hours == 6
    assert cfg.curate.plan_payload_max_bytes == 123456
    assert cfg.inject.max_chars == 1000
    assert cfg.inject.sources == ["startup"]
    assert cfg.redact.extra_patterns == ["foo-\\d+"]
    assert cfg.recommend.min_occurrences == 5
    assert cfg.recommend.raw_lookback_days == 7
    assert cfg.recommend.raw_cap_per_project == 50
    assert cfg.recommend.near_duplicate_threshold == 0.8
    assert cfg.recommend.survival_window_days == 30  # untouched key still defaults


def test_auto_tier_admits_a_full_pass() -> None:
    """The auto tier must be able to COMPLETE the work it admits.

    `plan_max_raws` (G19) turned batch count into a function of raw count —
    ceil(raws/3) — which silently invalidated the sizing that produced
    `auto_max_brain_calls`. Before this guard, `auto_max_raws = 40` needed ~55
    calls against a ceiling of 50, and nothing failed: a pass just stopped early
    and reported `partial`, which is a legitimate outcome and therefore invisible
    as a *sizing* defect. The rationale lived only in a comment, and a comment
    cannot fail a gate.

    The bound is deliberately the OPTIMISTIC one — one distill call per raw. The
    real mixture can be cheaper (five zero-call paths) or dearer (a rendered
    transcript that chunks), so a tier that cannot fit even this floor cannot fit
    anything.
    """
    from math import ceil

    cfg = load_config(Path("/does-not-exist"))
    curate = cfg.curate

    plan_batches = ceil(curate.auto_max_raws / curate.plan_max_raws)
    floor = curate.auto_max_raws + plan_batches + 1  # + synthesis

    assert floor <= curate.auto_max_brain_calls, (
        f"the auto tier admits {curate.auto_max_raws} raws, which needs at least "
        f"{floor} brain calls ({curate.auto_max_raws} distill + {plan_batches} plan "
        f"+ 1 synthesis), but auto_max_brain_calls is {curate.auto_max_brain_calls}. "
        "Re-derive the ceiling or lower auto_max_raws — see core/config.py."
    )


def test_the_explicit_tier_admits_a_full_pass() -> None:
    """Same rule for the typed-command tier, which admits far more raws and so
    fails this arithmetic sooner, not later."""
    from math import ceil

    curate = load_config(Path("/does-not-exist")).curate

    floor = curate.max_raws + ceil(curate.max_raws / curate.plan_max_raws) + 1

    assert floor <= curate.max_brain_calls, (
        f"the explicit tier admits {curate.max_raws} raws, needing at least {floor} "
        f"calls, but max_brain_calls is {curate.max_brain_calls}."
    )
