"""Pass budget — P0 "bound every automatic curation pass" from the 2026-07-17
Claude usage runaway (``docs/notes/2026-07-17-claude-usage-runaway-incident.md``).

The lock, the internal-call marker, and distill's systemic-failure breaker all
bound *pathological* behaviour. None of them bounds a *healthy* pass: one
curator, holding the lock legitimately, with no errors at all, will process the
entire unconsumed backlog. This module supplies the missing ceiling.

**Exhaustion is a normal outcome, not a failure.** ``BudgetExhausted`` is
deliberately NOT a ``BrainError``: a ``BrainError`` from ``plan_json`` means
"abort, leave every raw unconsumed" (D9), and it surfaces as ``status: error``,
which ``cli/__init__.py`` turns into exit 1 — breaking the hooks-always-exit-0
guarantee. A budget stop instead leaves the remaining raws unconsumed and
reports a bounded, retryable result with an unchanged status.

**Enforcement is structural, not conventional.** ``Brain`` is a two-method
Protocol, so wrapping it means every brain call — including any call site a
future contributor adds — must debit the ledger. There is no path around it
short of deliberately unwrapping.

**Reserved calls prevent a livelock.** If distillation were allowed to spend the
whole call budget, planning would never run, nothing would be consumed, and the
next pass would replay the same prefix forever — a backlog that silently never
drains. Distill is therefore capped at ``max_brain_calls - reserve_calls`` and
degrades to deterministic skims when it hits that, which is exactly how distill
already handles a systemic backend failure (D16: distill never aborts a pass).
Planning and synthesis always keep their reserve.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from neurobase.brain.base import DEFAULT_RETRIES, Brain

# Not PEP 695 syntax: the CI matrix includes Python 3.11, where `def f[T](...)`
# is a syntax error.
_T = TypeVar("_T")

# Held back from distillation so planning + synthesis can always run. Sized for
# a worst-case handful of D22 plan batches plus the single synthesis call.
DEFAULT_RESERVE_CALLS = 6


class BudgetExhausted(RuntimeError):
    """A pass-budget ceiling was reached.

    Not a ``BrainError`` — see the module docstring. Callers translate this into
    "stop cleanly, leave the rest unconsumed", never into an error status.
    """

    def __init__(self, dimension: str, limit: int) -> None:
        super().__init__(f"curation pass budget exhausted: {dimension} limit of {limit} reached")
        self.dimension = dimension
        self.limit = limit


def _positive(name: str, value: int) -> int:
    """Reject a ceiling that would silently disable the budget.

    A zero or negative limit is a misconfiguration, not a request for "no
    bound" — failing closed here is the whole point of the module.
    """
    if value <= 0:
        raise ValueError(f"curate budget {name} must be positive (got {value})")
    return value


@dataclass
class PassBudget:
    """A single pass's ledger. Constructed once per ``curate`` call.

    ``clock`` is injectable so the wall-clock ceiling is deterministically
    testable; it defaults to ``time.monotonic`` (monotonic, so a system clock
    adjustment mid-pass cannot extend or collapse the budget).
    """

    max_raws: int
    max_brain_calls: int
    max_brain_attempts: int
    max_distill_chunks: int
    max_seconds: int
    reserve_calls: int = DEFAULT_RESERVE_CALLS
    clock: Callable[[], float] = time.monotonic

    calls: int = field(default=0, init=False)
    distill_calls: int = field(default=0, init=False)
    deferred_raws: int = field(default=0, init=False)
    stopped_by: str | None = field(default=None, init=False)
    _started: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        _positive("max_raws", self.max_raws)
        _positive("max_brain_calls", self.max_brain_calls)
        _positive("max_brain_attempts", self.max_brain_attempts)
        _positive("max_distill_chunks", self.max_distill_chunks)
        _positive("max_seconds", self.max_seconds)
        if self.reserve_calls < 0:
            raise ValueError(
                f"curate budget reserve_calls must not be negative (got {self.reserve_calls})"
            )
        if self.reserve_calls >= self.max_brain_calls:
            # Otherwise the distill allowance is zero or negative and every pass
            # would skim-only — a silent, total loss of distillation.
            raise ValueError(
                f"curate budget reserve_calls ({self.reserve_calls}) must be less than "
                f"max_brain_calls ({self.max_brain_calls})"
            )
        self._started = self.clock()

    # --- selection ------------------------------------------------------

    def select_raws(self, docs: list[_T]) -> list[_T]:
        """The oldest-first prefix this pass may consider.

        Deferred raws are dropped *before* the pass begins, so they never enter
        the batch loop and therefore can never be marked consumed. That makes
        "the rest stays unconsumed" a structural property rather than something
        the error paths have to remember.
        """
        if len(docs) <= self.max_raws:
            return docs
        self.deferred_raws = len(docs) - self.max_raws
        self.stopped_by = self.stopped_by or "max_raws"
        return docs[: self.max_raws]

    # --- accounting -----------------------------------------------------

    @property
    def distill_allowance(self) -> int:
        """Calls distillation may spend, holding back the planning reserve."""
        return self.max_brain_calls - self.reserve_calls

    def _check_clock(self) -> None:
        if self.clock() - self._started >= self.max_seconds:
            self.stopped_by = "max_seconds"
            raise BudgetExhausted("max_seconds", self.max_seconds)

    def debit(self, *, distilling: bool) -> None:
        """Charge one logical brain call, or raise ``BudgetExhausted``.

        ``max_brain_attempts`` is enforced as a *worst-case* bound rather than
        an observed count: ``call_with_retry`` lives inside each backend, below
        the ``Brain`` protocol, so a wrapper at this level cannot see a retry.
        One logical call can therefore become ``DEFAULT_RETRIES + 1``
        subprocesses, and that is what is charged against the attempt ceiling.
        """
        self._check_clock()

        if distilling:
            if self.distill_calls >= self.max_distill_chunks:
                self.stopped_by = "max_distill_chunks"
                raise BudgetExhausted("max_distill_chunks", self.max_distill_chunks)
            if self.calls >= self.distill_allowance:
                self.stopped_by = "max_brain_calls"
                raise BudgetExhausted("max_brain_calls", self.distill_allowance)
        elif self.calls >= self.max_brain_calls:
            self.stopped_by = "max_brain_calls"
            raise BudgetExhausted("max_brain_calls", self.max_brain_calls)

        if (self.calls + 1) * (DEFAULT_RETRIES + 1) > self.max_brain_attempts:
            self.stopped_by = "max_brain_attempts"
            raise BudgetExhausted("max_brain_attempts", self.max_brain_attempts)

        self.calls += 1
        if distilling:
            self.distill_calls += 1

    def summary(self) -> dict[str, int | str]:
        """The budget's contribution to the pass summary."""
        out: dict[str, int | str] = {
            "budget_calls": self.calls,
            "budget_deferred_raws": self.deferred_raws,
        }
        if self.stopped_by is not None:
            out["budget_stopped_by"] = self.stopped_by
        return out


def from_config(curate_cfg: object, *, automatic: bool) -> PassBudget:
    """Build the budget for one pass from ``config.curate``.

    ``automatic`` selects the tier: hook-triggered passes (``curate --if-stale``)
    get the small ``auto_*`` ceilings, an explicitly typed ``neurobase curate``
    gets the permissive ones.
    """
    prefix = "auto_" if automatic else ""

    def knob(name: str) -> int:
        return int(getattr(curate_cfg, f"{prefix}{name}"))

    return PassBudget(
        max_raws=knob("max_raws"),
        max_brain_calls=knob("max_brain_calls"),
        max_brain_attempts=knob("max_brain_attempts"),
        max_distill_chunks=knob("max_distill_chunks"),
        max_seconds=knob("max_seconds"),
    )


def explicit_budget() -> PassBudget:
    """The permissive default tier, for callers that pass no budget."""
    from neurobase.core.config import CurateConfig

    return from_config(CurateConfig(), automatic=False)


class BudgetedBrain:
    """A ``Brain`` that debits a ``PassBudget`` before delegating.

    Satisfies the ``Brain`` protocol structurally, so it drops in wherever a
    brain is passed. ``distilling`` selects which ceiling applies — distill
    calls are additionally held under the reserve.
    """

    def __init__(self, inner: Brain, budget: PassBudget, *, distilling: bool = False) -> None:
        self._inner = inner
        self._budget = budget
        self._distilling = distilling
        self.name = inner.name

    def for_distill(self) -> BudgetedBrain:
        """The same ledger, charged against the distill allowance."""
        return BudgetedBrain(self._inner, self._budget, distilling=True)

    def plan_json(self, system: str, user: str) -> dict:
        self._budget.debit(distilling=self._distilling)
        return self._inner.plan_json(system, user)

    def text(self, system: str, user: str) -> str:
        self._budget.debit(distilling=self._distilling)
        return self._inner.text(system, user)
