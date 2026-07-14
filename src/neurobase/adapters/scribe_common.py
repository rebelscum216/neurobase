"""Scribe logic shared by every adapter (spec §4/§5; bounds from §8).

§8 is agent-agnostic: the assistant-side bounds are one contract, so the
eviction that enforces them lives here once rather than being reimplemented per
adapter (the same reasoning as ``adapters.recall_common``). Each scribe
re-exports these names so its own module stays the place you look for its
tuned constants.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# A D13 redaction pass bound to the caller's config (`[redact].extra_patterns`).
# Scribes hand one to their body renderer so every captured value is scrubbed
# before any markdown prefix can shield it from a line-anchored rule.
Redactor = Callable[[str], str]

# A `#` run opening any line of captured content. Session text is untrusted:
# a prompt, an assistant message, or an IDE context block can contain its own
# markdown headings, and rendered as-is they become *the raw document's own*
# sections — content forging the structure the curator then reads.
_LEADING_HEADING = re.compile(r"^([ \t]*)(#+)", re.MULTILINE)

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


def block(text: str) -> str:
    """Make one captured value safe to place in a capture body (spec §4).

    Escapes the leading ``#`` of every line, so captured content can never
    forge one of the body's own ``##`` sections. Indentation alone does not
    close this: CommonMark still reads a heading indented up to three spaces.
    """
    return _LEADING_HEADING.sub(r"\1\\\2", text)


def bullet(text: str) -> str:
    """Render one list item of a capture body (spec §4 body format).

    ``block()`` for heading safety, then indent continuation lines so a
    multi-line value stays *inside* its bullet. Bounds make multi-line content
    the common case, not an edge one: prompts run to 1,200 chars and subagent
    reports to 1,500.

    Callers MUST redact (D13) the value *before* rendering it — a structural
    prefix like ``"- "`` shifts the text off column 0 and shields it from the
    line-anchored rules in the D13 table.
    """
    return "- " + block(text).replace("\n", "\n  ")


def final_summary(candidates: list[str]) -> str:
    """The ``## Final assistant summary`` slot (spec §4): the **longest of the
    last 3** non-empty assistant texts. The last message alone is not a reliable
    summary — a long session that ends on a throwaway reply would otherwise
    capture that reply as its entire assistant-side record. Ties keep the
    earlier message, so selection is deterministic."""
    return max(candidates[-SUMMARY_CANDIDATE_WINDOW:], key=len, default="")
