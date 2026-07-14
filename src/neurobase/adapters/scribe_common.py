"""Scribe logic shared by every adapter (spec §4/§5; bounds from §8).

§8 is agent-agnostic: the assistant-side bounds are one contract, so the
eviction that enforces them lives here once rather than being reimplemented per
adapter (the same reasoning as ``adapters.recall_common``). Each scribe
re-exports these names so its own module stays the place you look for its
tuned constants.
"""

from __future__ import annotations

# Tuned defaults (spec §8).
MAX_ASSISTANT_MSG_CHARS = 500
MAX_ASSISTANT_TOTAL_CHARS = 6000
SUMMARY_CANDIDATE_WINDOW = 3


def bounded_highlights(messages: list[str]) -> list[str]:
    """The ``## Assistant highlights`` section (spec §4): truncate each message,
    then walk newest→oldest keeping messages until the total budget is spent,
    and restore chronological order. Deterministic — the same transcript always
    yields the same highlights."""
    kept: list[str] = []
    used = 0
    for message in reversed(messages):
        bounded = message[:MAX_ASSISTANT_MSG_CHARS]
        if used + len(bounded) > MAX_ASSISTANT_TOTAL_CHARS:
            break
        kept.append(bounded)
        used += len(bounded)
    return list(reversed(kept))


def bullet(text: str) -> str:
    """Render one list item of a capture body (spec §4 body format).

    Continuation lines are indented so a multi-line value stays *inside* its
    bullet. Without this, a pasted stack trace or a markdown-formatted assistant
    message puts its own ``## heading`` at column 0 — session content would
    forge the raw document's section structure, and the curator reads that
    structure. Bounds make this the common case, not an edge one: prompts run to
    1,200 chars and subagent reports to 1,500.
    """
    return "- " + text.replace("\n", "\n  ")


def final_summary(candidates: list[str]) -> str:
    """The ``## Final assistant summary`` slot (spec §4): the **longest of the
    last 3** non-empty assistant texts. The last message alone is not a reliable
    summary — a long session that ends on a throwaway reply would otherwise
    capture that reply as its entire assistant-side record. Ties keep the
    earlier message, so selection is deterministic."""
    return max(candidates[-SUMMARY_CANDIDATE_WINDOW:], key=len, default="")
