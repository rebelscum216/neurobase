"""Neurobase — a local-first, cross-agent memory layer for coding agents.

Captures Claude Code + Codex sessions, curates them into a small durable fact set,
synthesizes a wikilinked markdown wiki, injects memory back into future sessions,
and recommends promotions into SKILL.md / AGENTS.md. See docs/ for the full design.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("neurobase-cli")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = "0+unknown"

__all__ = ["__version__"]
