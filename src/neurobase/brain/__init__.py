"""Execution-backend abstraction for LLM steps (decision D9).

Provider-independent brain steps — ``plan_json`` (curator plan) and ``text``
(node synthesis) — with three backends behind a common contract:
``claude_cli`` (spike S5/ADR-0002), ``codex_cli`` (S1/ADR-0001), and
``anthropic_api``. Selection order claude-cli → codex-cli → anthropic-api →
openai-api (D9); ollama is a documented seam. Contract in ``base.py``.
"""

from __future__ import annotations

from neurobase.brain.anthropic_api import AnthropicAPIBrain
from neurobase.brain.base import (
    Brain,
    BrainError,
    BrainUnavailableError,
    RetryableBrainError,
    parse_plan_json,
)
from neurobase.brain.claude_cli import ClaudeCLIBrain
from neurobase.brain.codex_cli import CodexCLIBrain
from neurobase.brain.select import BrainResolution, detect, resolve_brain

__all__ = [
    "AnthropicAPIBrain",
    "Brain",
    "BrainError",
    "BrainResolution",
    "BrainUnavailableError",
    "ClaudeCLIBrain",
    "CodexCLIBrain",
    "RetryableBrainError",
    "detect",
    "parse_plan_json",
    "resolve_brain",
]
