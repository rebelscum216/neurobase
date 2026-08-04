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

import dataclasses
import json
import re
from typing import Any, Protocol, runtime_checkable

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


@dataclasses.dataclass
class BrainUsage:
    """Token/cost counters accumulated across a brain's calls.

    Optional by design: a brain MAY expose ``usage``, and readers use
    ``getattr(brain, "usage", None)`` — so backends that report nothing (the Codex
    CLI's JSON stream carries no token fields) and the test fakes both stay valid
    without implementing it. That keeps this off the ``Brain`` protocol, which every
    fake in the suite would otherwise have to satisfy.

    ``models`` counts calls per model id because CLI backends do not pin a model:
    two `claude -p` probes with identical flags reported different ones, so "both
    calls used the same model" is a claim that needs evidence, not an assumption.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    models: dict[str, int] = dataclasses.field(default_factory=dict)

    def record(self, envelope: dict[str, Any]) -> None:
        """Fold one CLI envelope's usage in. Tolerant by intent: a missing or
        malformed ``usage`` block must never fail a brain call that otherwise
        succeeded — accounting is observability, not correctness."""
        self.calls += 1
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                value = usage.get(field)
                if isinstance(value, int):
                    setattr(self, field, getattr(self, field) + value)
        cost = envelope.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self.cost_usd += float(cost)
        model_usage = envelope.get("modelUsage")
        if isinstance(model_usage, dict):
            for model in model_usage:
                self.models[str(model)] = self.models.get(str(model), 0) + 1

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["cost_usd"] = round(self.cost_usd, 6)
        return out
