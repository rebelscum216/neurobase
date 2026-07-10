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
    assert cfg.inject.max_chars == 1000
    assert cfg.inject.sources == ["startup"]
    assert cfg.redact.extra_patterns == ["foo-\\d+"]
    assert cfg.recommend.min_occurrences == 5
    assert cfg.recommend.raw_lookback_days == 7
    assert cfg.recommend.raw_cap_per_project == 50
    assert cfg.recommend.near_duplicate_threshold == 0.8
    assert cfg.recommend.survival_window_days == 30  # untouched key still defaults
