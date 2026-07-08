"""Claude Code recall (spec §3): the SessionStart injection.

Resolves the project from the hook's cwd, assembles the project's status nodes
into ``additionalContext``, and (fail-safe) emits nothing on ANY error / no
project / no nodes. After emitting, spawns a detached ``curate --if-stale``
(decision D8) so the fold stays fresh without delaying session start.

The logic is agent-agnostic and shared with the Codex adapter — it lives in
``adapters.recall_common`` and is re-exported here so the Claude adapter keeps
its own ``recall`` surface.
"""

from __future__ import annotations

from neurobase.adapters.recall_common import (
    HEADER,
    MAX_CONTEXT_CHARS,
    _assemble,
    _node_bodies,
    build_context,
    emit,
    spawn_curate_if_stale,
)

__all__ = [
    "HEADER",
    "MAX_CONTEXT_CHARS",
    "_assemble",
    "_node_bodies",
    "build_context",
    "emit",
    "spawn_curate_if_stale",
]
