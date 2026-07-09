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
class Config:
    store: StoreConfig = field(default_factory=StoreConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    curate: CurateConfig = field(default_factory=CurateConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    redact: RedactConfig = field(default_factory=RedactConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


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
    )
