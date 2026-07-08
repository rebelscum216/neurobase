"""Codex recall (spec §5 → mirrors §3): the SessionStart injection.

Codex's `SessionStart` hook output reaches the model as a `developer`-role input
message (live-verified, ADR-0005), so injection is identical to the Claude
adapter — the shared logic lives in ``adapters.recall_common`` and is re-exported
here. `AGENTS.override.md` remains a documented fallback (spec §5) for a future
Codex version that stops forwarding hook output; it is not the primary path.
"""

from __future__ import annotations

from neurobase.adapters.recall_common import (
    HEADER,
    MAX_CONTEXT_CHARS,
    build_context,
    emit,
    spawn_curate_if_stale,
)

__all__ = [
    "HEADER",
    "MAX_CONTEXT_CHARS",
    "build_context",
    "emit",
    "spawn_curate_if_stale",
]
