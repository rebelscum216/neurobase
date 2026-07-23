"""User config file (spec §10): keys are all optional; defaults are the
tuned values from spec §8. Neurobase never writes this file — it's
hand-edited by the user.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StoreConfig:
    root: str = "~/neurobase"


@dataclass
class BrainConfig:
    backend: str = "auto"
    model: str = "claude-sonnet-5"
    timeout_seconds: int = 120


@dataclass
class CurateConfig:
    stale_hours: int = 12
    tombstone_grace_days: int = 14
    # Final serialized plan request budget, including PLAN_SYSTEM and framing.
    # Bytes (not characters): CLI backends pass the prompt as one argv entry.
    plan_payload_max_bytes: int = 262_144
    # Tier-2 transcript distill (spec §2.0, ADR-0014). `auto` distills a raw
    # when its transcript_path resolves; `off` disables it (skim only).
    distill: str = "auto"
    distill_chunk_chars: int = 200_000
    # --- pass budget (P0, 2026-07-17 runaway incident) ------------------------
    # Hard per-pass ceilings. Exhaustion is a NORMAL bounded result: the pass
    # stops, the remaining raws stay unconsumed, and it is retryable. Flat
    # scalars, not a nested [curate.budget] table, because `load_config` builds
    # `CurateConfig(**data["curate"])` — a nested table would arrive as a plain
    # dict and every attribute access would fail at runtime, invisible to mypy.
    #
    # The `auto_*` tier applies to hook-triggered passes (`curate --if-stale`);
    # the unprefixed tier applies to an explicitly typed `neurobase curate`.
    #
    # Sized against a measurement of this store on 2026-07-20 (1669 raws, 1469
    # consumed, 200 unconsumed), not a guess. Those 200 spread over five days:
    # 165 landed on 2026-07-17 (the incident), the other four days ran 1-18.
    # So a normal day is single-to-low-double digits and the runaway was ~10x
    # that; 40 sits well above the former and decisively below the latter.
    auto_max_raws: int = 40
    # 40 distill + <=4 plan batches + 1 synthesis = 45, plus headroom. Measured
    # bodies are median 1482 / p90 4028 / max 13926 chars against a 200k chunk
    # size, so no raw in this store chunks and each costs exactly one call.
    auto_max_brain_calls: int = 50
    # Worst-case subprocesses, enforced as calls x (DEFAULT_RETRIES + 1) because
    # `call_with_retry` sits inside each backend, below the Brain protocol.
    auto_max_brain_attempts: int = 100
    # Defense-in-depth only: chunk calls are brain calls, so max_brain_calls
    # binds first in every case measured here. This catches transcripts growing
    # far beyond anything currently in the store.
    auto_max_distill_chunks: int = 60
    # A healthy 40-raw pass is estimated at 4-10 min (per-call latency is NOT
    # measured — an open item). 15 min leaves margin without letting a detached
    # background curator run unbounded.
    auto_max_seconds: int = 900
    # Explicit tier: one typed command drains the current 200-raw backlog.
    max_raws: int = 250
    max_brain_calls: int = 280
    max_brain_attempts: int = 560
    max_distill_chunks: int = 320
    max_seconds: int = 3600


@dataclass
class InjectConfig:
    max_chars: int = 6000
    sources: list[str] = field(default_factory=lambda: ["startup", "clear"])


@dataclass
class RedactConfig:
    extra_patterns: list[str] = field(default_factory=list)


@dataclass
class McpConfig:
    # Dual-exposure of nodes as MCP resources (Phase 7, decision D-d). Off by
    # default: the tool baseline is universal; resources are Claude-only sugar.
    # `resources/list` returns [] validly when off — never an error.
    expose_resources: bool = False


@dataclass
class RecommendConfig:
    # Phase 8 recommender (spec §12.11, ADR-0007 D17/D18). Ranker gates, the
    # corpus loader's per-project raw caps, near-duplicate threshold, and the
    # survival window — all tuned defaults, all config-overridable.
    min_occurrences: int = 3  # ranker recurrence gate (§12.6)
    min_breadth_sessions: int = 2  # ranker breadth gate (§12.6)
    recency_halflife_days: int = 30  # recency weight half-life (§12.6)
    raw_lookback_days: int = 30  # corpus loader raw cap by age (§12.4, D17)
    raw_cap_per_project: int = 200  # corpus loader raw cap by count (§12.4, D17)
    near_duplicate_threshold: float = 0.6  # Jaccard threshold (§12.5/§12.6, D18)
    survival_window_days: int = 30  # accepted-artifact survival window (§12.9)


@dataclass
class EnableConfig:
    # Folder-scoped auto-enable (ADR-0019). `neurobase enable` is per-repo and
    # opt-in by design (a hook captures only when the resolved project's memory
    # tree exists). This relocates that consent from per-repo to per-folder: name
    # an `auto_enable_roots` directory once, and any git repo beneath it is
    # registered as its own project — and given its memory tree — the first time a
    # hook fires there. Empty roots = today's behavior (per-repo opt-in only).
    # `denylist` always wins over roots AND is a *live* gate: a denylisted repo
    # stops capturing/injecting even if already enabled, so editing one line
    # revokes capture (ADR-0019 F4). Entries must be absolute or `~`-prefixed —
    # a relative path would resolve against the hook's launch cwd and is skipped.
    # Both lists are hand-edited; Neurobase never writes this file.
    auto_enable_roots: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)


@dataclass
class Config:
    store: StoreConfig = field(default_factory=StoreConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    curate: CurateConfig = field(default_factory=CurateConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    redact: RedactConfig = field(default_factory=RedactConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    recommend: RecommendConfig = field(default_factory=RecommendConfig)
    enable: EnableConfig = field(default_factory=EnableConfig)


def config_path() -> Path:
    """Platform-appropriate config path (spec §10) — may not exist yet."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "neurobase" / "config.toml"
    return Path.home() / ".config" / "neurobase" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load config.toml, applying §8 defaults for any missing/absent keys."""
    target = path if path is not None else config_path()
    data: dict[str, Any] = {}
    if target.exists():
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    return Config(
        store=StoreConfig(**data.get("store", {})),
        brain=BrainConfig(**data.get("brain", {})),
        curate=CurateConfig(**data.get("curate", {})),
        inject=InjectConfig(**data.get("inject", {})),
        redact=RedactConfig(**data.get("redact", {})),
        mcp=McpConfig(**data.get("mcp", {})),
        recommend=RecommendConfig(**data.get("recommend", {})),
        enable=EnableConfig(**data.get("enable", {})),
    )
