"""The safety-capability profile a build advertises (2026-07-27 recurrence).

## Why this exists

On 2026-07-27 both `SessionStart` hooks invoked a shim built 2026-07-09 that
contained none of the hardening added after the 2026-07-17 runaway — no
single-flight curate lock, no reentrancy guard, no automatic-pass budget. The
runaway recurred, and `neurobase doctor` reported all-green throughout, because
it verified that the hook command *string* matched the shim path and never that
the shim contained the safety code it depends on.

**Source-tree hardening is not deployed hardening.** This module lets `doctor`
tell the two apart.

## Two design rules, both load-bearing

1. **Name behavioral invariants, never modules.** `"contains core/locks.py"`
   rots the moment a guard moves file, and a module's presence is not evidence
   that its behavior holds. Each name below is a *contract*, and each is bound to
   a test that exercises the behavior — so `PROVIDES` cannot drift from reality
   without the suite failing (AGENTS.md principle #1: spec is law, tests enforce
   it).

2. **The requirement comes from the diagnosing build, never the executable under
   test.** A stale binary asked whether it is safe will answer yes: it cannot
   report a capability it has never heard of. So `doctor` takes
   :data:`REQUIRED_FOR_STARTUP_HOOK` from *itself* and asks each hook executable
   only what it *provides*. A build predating this module has no ``capabilities``
   command at all, which is the signal — absence is unfakeable in the safe
   direction.

Bumping the profile is a deliberate act: when a new guard becomes necessary to
satisfy the minimum safe contract, add its name, bump :data:`PROFILE`, and add
the test that proves it.
"""

from __future__ import annotations

from typing import Any

from neurobase import __version__

#: Bump when a newly-required guard changes what "safe to run automatically"
#: means. Consumers compare profiles, not versions — the package version is
#: deliberately held constant across many source changes, so it cannot carry
#: this signal (2026-07-27 review, R2-F1).
PROFILE = "automatic-curation-safety-v1"

# --- capability names (behavioral contracts) --------------------------------

#: A hook invoked from inside Neurobase's own subprocess captures nothing,
#: injects nothing, and spawns nothing. Without it, a headless brain call
#: re-enters the hook that launched it (the 2026-07-17 amplification loop).
HOOK_REENTRANCY_SUPPRESSION = "hook-reentrancy-suppression"

#: At most one curate pass per project makes progress at a time. Losers exit
#: *before* the staleness check and *before* brain resolution, so concurrent
#: session starts cost one lock probe each rather than one LLM call each. This
#: is the correctness boundary for both runaways; spawn-side debounce is not.
CURATE_SINGLE_FLIGHT = "curate-single-flight"

#: A hook-triggered pass is bounded in raws and in brain calls, so a systemic
#: provider failure cannot turn a backlog into one call per raw.
AUTOMATIC_PASS_BUDGET = "automatic-pass-budget"

#: Concurrent writers to one project's store are serialized by an advisory
#: lock, so a partially-applied fold cannot interleave with another (ADR-0023).
PROJECT_STORE_WRITE_LOCK = "project-store-write-lock"

#: Everything this build implements.
PROVIDES: frozenset[str] = frozenset(
    {
        HOOK_REENTRANCY_SUPPRESSION,
        CURATE_SINGLE_FLIGHT,
        AUTOMATIC_PASS_BUDGET,
        PROJECT_STORE_WRITE_LOCK,
    }
)

#: What an executable named by an **enabled startup hook** must provide before
#: automatic curation is safe. A startup hook is the only surface that spawns
#: work without a human in the loop, which is why it carries the hard gate;
#: capture-only hooks (``SessionEnd`` / ``Stop``) never spawn a curator.
REQUIRED_FOR_STARTUP_HOOK: frozenset[str] = frozenset(
    {
        HOOK_REENTRANCY_SUPPRESSION,
        CURATE_SINGLE_FLIGHT,
        AUTOMATIC_PASS_BUDGET,
    }
)


def describe() -> dict[str, Any]:
    """This build's capability report — the payload of ``neurobase capabilities``.

    Deliberately a plain, stable, additive JSON shape: a *newer* doctor must be
    able to read an *older* build's report to decide it is unsafe. Keys are only
    ever added, never renamed or repurposed.
    """
    return {
        "profile": PROFILE,
        "version": __version__,
        "provides": sorted(PROVIDES),
    }


def missing_for_startup_hook(provided: frozenset[str] | set[str]) -> list[str]:
    """Required capabilities absent from ``provided``, sorted.

    The requirement side is always *this* build's
    :data:`REQUIRED_FOR_STARTUP_HOOK` — callers pass only what the executable
    under test claims. Empty list ⇒ that executable is safe to run as a startup
    hook, as far as this build knows how to ask.
    """
    return sorted(REQUIRED_FOR_STARTUP_HOOK - set(provided))
