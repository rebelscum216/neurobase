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

## Three design rules, all load-bearing

1. **Name behavioral invariants, never modules.** `"contains core/locks.py"`
   rots the moment a guard moves file, and a module's presence is not evidence
   that its behavior holds. Each name below is a *contract* bound to a test that
   exercises the behavior, so the manifest cannot drift from reality without the
   suite failing (AGENTS.md principle #1).

2. **The requirement comes from the diagnosing build, never the artifact under
   test.** A stale build asked whether it is safe will answer yes: it cannot
   report a guard it has never heard of. So `doctor` takes
   :data:`REQUIRED_FOR_STARTUP_HOOK` from *itself* and reads only what the other
   install *provides*. A build predating this module ships no manifest at all,
   which is the signal — absence is unfakeable in the safe direction.

3. **Read the artifact; never run it.** Capability evidence is a static JSON
   file inside the installed package, resolved from the executable's path. An
   earlier revision executed ``<exe> capabilities`` and was a genuine security
   defect: `doctor` would run any command named in a repo-local
   ``.codex/hooks.json``, including one Codex itself never discovers, breaking
   doctor's read-only contract (spec §6). Reading a file cannot have side
   effects; spawning a process can, and validation after the fact is too late.

Bumping the profile is deliberate: when a new guard becomes necessary to satisfy
the minimum safe contract, add its name to ``capability_manifest.json``, bump
``profile`` there, and add the test that proves the behavior.
"""

from __future__ import annotations

import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from neurobase import __version__

#: The packaged manifest filename, read from an install's ``neurobase/``
#: package directory. Its **absence** is how a pre-profile build is recognized.
MANIFEST_NAME = "capability_manifest.json"

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

#: What an executable named by an **enabled startup hook** must provide before
#: automatic curation is safe. This is *policy*, so it lives in code rather than
#: the manifest: it is what this build demands of others, not what it offers.
#: Startup hooks carry the gate because they are the only surface that spawns
#: work with no human in the loop; capture hooks never spawn a curator.
REQUIRED_FOR_STARTUP_HOOK: frozenset[str] = frozenset(
    {
        HOOK_REENTRANCY_SUPPRESSION,
        CURATE_SINGLE_FLIGHT,
        AUTOMATIC_PASS_BUDGET,
    }
)


def _load_own_manifest() -> dict[str, Any]:
    """This build's manifest, or an empty profile if it is somehow unreadable.

    Never raises: `doctor` must report on a damaged install rather than crash on
    one, and every other consumer degrades to "provides nothing", which is the
    safe direction.
    """
    try:
        raw = (files("neurobase") / MANIFEST_NAME).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError, ModuleNotFoundError):
        return {"profile": "unknown", "provides": []}
    return parsed if isinstance(parsed, dict) else {"profile": "unknown", "provides": []}


_OWN = _load_own_manifest()

#: Bump in ``capability_manifest.json`` when a newly-required guard changes what
#: "safe to run automatically" means. Consumers compare profiles, not versions —
#: the package version is deliberately held constant across many source changes,
#: so it cannot carry this signal (2026-07-27 review, R2-F1).
PROFILE: str = str(_OWN.get("profile", "unknown"))

#: Everything this build implements, from the packaged manifest.
PROVIDES: frozenset[str] = frozenset(
    item for item in _OWN.get("provides", []) if isinstance(item, str)
)


def describe() -> dict[str, Any]:
    """This build's capability report — the payload of ``neurobase capabilities``.

    A stable, additive JSON shape: a *newer* doctor must be able to read an
    *older* build's manifest to decide it is unsafe, so keys are only ever added.
    """
    return {"profile": PROFILE, "version": __version__, "provides": sorted(PROVIDES)}


def _site_packages_candidates(executable: Path) -> list[Path]:
    """Plausible ``neurobase/`` package directories for a console script.

    Covers the layouts a shim actually lands in — uv tool, venv, and pipx all
    place ``bin/<script>`` beside ``lib/pythonX.Y/site-packages/`` (``Lib/`` on
    Windows). Returned as candidates rather than one answer because the Python
    minor version is unknown from the path alone.
    """
    root = executable.parent.parent
    candidates = [*root.glob("lib/python*/site-packages/neurobase")]
    candidates += [*root.glob("Lib/site-packages/neurobase")]  # Windows
    return candidates


def read_manifest(executable: str | Path) -> dict[str, Any] | None:
    """The capability manifest of the install that ``executable`` belongs to.

    ``None`` means *no evidence of safety*: the path is missing, its package
    directory cannot be located, or it ships no manifest (every build predating
    the profile). Callers must treat ``None`` as unsafe rather than unknown.

    Purely a filesystem read — resolving symlinks, globbing, and parsing JSON.
    It never imports or executes the target install, which is the whole point:
    the executable named in a hook file is not necessarily trustworthy, and a
    subprocess's side effects land before any validation could reject them.
    """
    try:
        resolved = Path(executable).resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved.exists():
        return None
    for package_dir in _site_packages_candidates(resolved):
        manifest = package_dir / MANIFEST_NAME
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def provided_by(executable: str | Path) -> frozenset[str]:
    """Capabilities the install behind ``executable`` advertises; empty if none.

    Empty covers every failure mode — missing binary, unlocatable package, no
    manifest, malformed manifest — because they all mean the same thing: this
    build cannot confirm that install is safe.
    """
    manifest = read_manifest(executable)
    if manifest is None:
        return frozenset()
    provides = manifest.get("provides")
    if not isinstance(provides, list):
        return frozenset()
    return frozenset(item for item in provides if isinstance(item, str))


def missing_for_startup_hook(provided: frozenset[str] | set[str]) -> list[str]:
    """Required capabilities absent from ``provided``, sorted.

    The requirement side is always *this* build's
    :data:`REQUIRED_FOR_STARTUP_HOOK`; callers pass only what the install under
    test advertises. Empty list ⇒ safe to run as a startup hook, as far as this
    build knows how to ask.
    """
    return sorted(REQUIRED_FOR_STARTUP_HOOK - set(provided))


def own_executable() -> Path:
    """The running interpreter's own console script, for self-comparison."""
    return Path(sys.argv[0]).resolve()
