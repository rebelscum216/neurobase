"""Brain contract (build-plan Phase 2): provider-independent LLM steps.

Two operations, both injectable so the curator's apply pipeline is testable
with fakes and no network (spec §2):

- ``plan_json(system, user) -> dict`` — the curator's plan step; the model
  answers with JSON, parsed leniently (fence-tolerant, spec §2 step 3).
- ``text(system, user) -> str`` — the node-synthesis step; free-form markdown.

Tuned defaults (spec §8): 120s timeout, 1 retry on timeout / 5xx / parse
failure. A parse failure that survives the retry raises ``BrainError`` — the
curator turns that into "leave every raw unconsumed" (decision D9's hard rule).
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_RETRIES = 1
DEFAULT_MAX_TOKENS = 8000

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class BrainError(RuntimeError):
    """A brain call failed in a way the caller must handle (parse failure,
    non-retryable API/CLI error). For the curator, a ``plan_json`` failure
    means the pass aborts and raws stay unconsumed."""


class BrainUnavailableError(BrainError):
    """The requested backend can't run here (not installed, not logged in,
    no API key)."""


class RetryableBrainError(BrainError):
    """A transient failure (timeout, 5xx, parse failure) worth one retry.
    Escapes as a plain ``BrainError`` once retries are exhausted."""


@runtime_checkable
class Brain(Protocol):
    """The provider-independent contract every backend implements."""

    name: str

    def plan_json(self, system: str, user: str) -> dict: ...

    def text(self, system: str, user: str) -> str: ...


def combine_prompt(system: str, user: str) -> str:
    """Fold a system + user prompt into the single prompt string the CLI
    backends accept (the API backend keeps them in separate slots)."""
    return f"{system}\n\n---\n\n{user}"


def parse_plan_json(text: str) -> dict:
    """Lenient, fence-tolerant parse of a model's JSON answer (spec §2 step 3).
    Raises ``RetryableBrainError`` on anything unparseable or non-object, so
    the caller's retry wrapper gives it one more shot before giving up."""
    stripped = _FENCE_RE.sub("", text.strip()).strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RetryableBrainError(f"plan JSON did not parse: {exc}") from exc
    if not isinstance(obj, dict):
        raise RetryableBrainError(f"plan JSON was not an object (got {type(obj).__name__})")
    return obj


def call_with_retry(attempt, *, retries: int = DEFAULT_RETRIES):
    """Run ``attempt`` once; on ``RetryableBrainError`` retry up to ``retries``
    more times, then re-raise the last failure as a plain ``BrainError``.
    Non-retryable ``BrainError``s propagate immediately."""
    last: RetryableBrainError | None = None
    for _ in range(retries + 1):
        try:
            return attempt()
        except RetryableBrainError as exc:
            last = exc
    assert last is not None
    raise BrainError(str(last)) from last
