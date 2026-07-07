"""Backend selection + detection (decision D9).

Config ``[brain].backend``: ``auto`` resolves in the order
claude-cli → codex-cli → anthropic-api → openai-api; an explicit value pins one
backend. Detection runs at ``doctor``/run time. ``doctor`` reports which
backend resolved and why (build-plan Phase 2 demo).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from neurobase.brain.anthropic_api import AnthropicAPIBrain, resolve_api_key
from neurobase.brain.base import Brain
from neurobase.brain.claude_cli import ClaudeCLIBrain
from neurobase.brain.codex_cli import CodexCLIBrain
from neurobase.core.config import Config

# The D9 auto-detection order.
AUTO_ORDER = ("claude-cli", "codex-cli", "anthropic-api", "openai-api")


@dataclass
class BrainResolution:
    """The outcome of resolving a backend: which one, whether it's usable, a
    human-readable reason, and (best-effort) a version string."""

    backend: str
    available: bool
    reason: str
    version: str | None = None


def _cli_version(binary: str) -> str | None:
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _detect_claude_cli(config: Config) -> BrainResolution:
    if shutil.which("claude") is None:
        return BrainResolution("claude-cli", False, "claude CLI not on PATH")
    version = _cli_version("claude")
    return BrainResolution("claude-cli", True, "claude CLI on PATH", version)


def _detect_codex_cli(config: Config) -> BrainResolution:
    if shutil.which("codex") is None:
        return BrainResolution("codex-cli", False, "codex CLI not on PATH")
    version = _cli_version("codex")
    return BrainResolution("codex-cli", True, "codex CLI on PATH", version)


def _detect_anthropic_api(config: Config) -> BrainResolution:
    if resolve_api_key() is None:
        return BrainResolution(
            "anthropic-api",
            False,
            "no API key (set NEUROBASE_API_KEY or ANTHROPIC_API_KEY)",
        )
    return BrainResolution("anthropic-api", True, f"API key present, model {config.brain.model}")


def _detect_openai_api(config: Config) -> BrainResolution:
    # Config enum + D9 order include openai-api, but Phase 2 ships only the
    # three above; be honest rather than pretend it's ready.
    return BrainResolution("openai-api", False, "not implemented yet (planned post-Phase 2)")


_DETECTORS = {
    "claude-cli": _detect_claude_cli,
    "codex-cli": _detect_codex_cli,
    "anthropic-api": _detect_anthropic_api,
    "openai-api": _detect_openai_api,
}


def detect(backend: str, config: Config) -> BrainResolution:
    detector = _DETECTORS.get(backend)
    if detector is None:
        return BrainResolution(backend, False, f"unknown backend {backend!r}")
    return detector(config)


def _build(backend: str, config: Config) -> Brain:
    timeout = config.brain.timeout_seconds
    if backend == "claude-cli":
        return ClaudeCLIBrain(timeout=timeout)
    if backend == "codex-cli":
        return CodexCLIBrain(timeout=timeout)
    if backend == "anthropic-api":
        return AnthropicAPIBrain(model=config.brain.model, timeout=timeout)
    raise ValueError(f"no builder for backend {backend!r}")


def resolve_brain(config: Config) -> tuple[Brain | None, BrainResolution]:
    """Resolve a usable backend from config. Returns ``(brain, resolution)``;
    ``brain`` is ``None`` when nothing resolved."""
    configured = config.brain.backend
    if configured == "auto":
        for name in AUTO_ORDER:
            resolution = detect(name, config)
            if resolution.available:
                return _build(name, config), resolution
        return None, BrainResolution("auto", False, "no backend available")
    resolution = detect(configured, config)
    if resolution.available:
        return _build(configured, config), resolution
    return None, resolution
