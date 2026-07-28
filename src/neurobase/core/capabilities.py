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
import re
import sys
from enum import Enum
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


#: Hard ceiling on a manifest read. The file this build ships is ~200 bytes; a
#: generous multiple still refuses to stream something unbounded that happens to
#: sit at the manifest path.
MAX_MANIFEST_BYTES = 64 * 1024


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


#: Interpreter basenames we accept as "this is a Python". Deliberately a shape
#: match rather than a fixed list, so `python3.15` works the day it exists, but
#: narrow enough that `sh`, `ruby`, or `python-not-really` do not qualify.
_PYTHON_NAME = re.compile(r"^python(?:w)?(?:\d+(?:\.\d+)*)?(?:\.exe)?$", re.IGNORECASE)


def _is_python_interpreter(name: str) -> bool:
    """Does this basename name a CPython console interpreter?"""
    return bool(_PYTHON_NAME.match(name))


class _Shebang(Enum):
    """What a console script's first line told us about its environment."""

    #: No shebang to read — a Windows ``.exe`` wrapper, or an unreadable file.
    #: Directory convention is the only evidence available; see `read_manifest`.
    ABSENT = "absent"
    #: A shebang we cannot resolve to an environment without running something
    #: (``#!/usr/bin/env python``, a relative path). Refused rather than guessed.
    AMBIGUOUS = "ambiguous"


def _interpreter_root(executable: Path) -> Path | _Shebang:
    """The environment root named by a console script's shebang.

    pip and uv write ``#!<env>/bin/python`` atop every console script, so the
    shebang says which environment — and therefore which ``site-packages`` —
    backs this command.

    The root is taken **lexically**, without resolving symlinks. That is not a
    shortcut, it is the whole point: a venv's ``bin/python`` is normally a
    symlink to some base interpreter, so resolving it discards the venv root
    whose ``site-packages`` is actually imported. Resolving here rejected the
    ordinary uv-tool layout — `…/tools/<name>/bin/python` → uv's managed CPython —
    and marked a correctly installed shim unsafe (review round 3, F1).

    Returns :attr:`_Shebang.AMBIGUOUS` for a shebang that cannot be tied to an
    environment without executing something, and :attr:`_Shebang.ABSENT` when
    there is no shebang at all. The two are distinct because only the first is a
    reason to withhold evidence: an absent shebang is the documented Windows
    case, while an ambiguous one is a layout we decline to vouch for.
    """
    try:
        with executable.open("rb") as fh:
            first = fh.readline(512)
    except OSError:
        return _Shebang.ABSENT
    if not first.startswith(b"#!"):
        return _Shebang.ABSENT
    try:
        raw = first[2:].strip().split()[0].decode()
    except (IndexError, UnicodeDecodeError):
        return _Shebang.AMBIGUOUS
    interpreter = Path(raw)
    if not interpreter.is_absolute():
        return _Shebang.AMBIGUOUS
    # `#!/usr/bin/env python` names the *launcher*, not the interpreter: which
    # Python it selects depends on PATH at run time, so `/usr/bin` is not an
    # environment root and treating it as one would certify anything under /usr.
    if interpreter.name == "env":
        return _Shebang.AMBIGUOUS
    # The shebang must name a *Python*. Without this, `#!<env>/bin/sh` beside a
    # planted manifest passed the root check and was certified — the binding is
    # to "the Python that imports the attesting package", and any other
    # interpreter tells us nothing about that (review round 4, F1).
    if not _is_python_interpreter(interpreter.name):
        return _Shebang.AMBIGUOUS
    return interpreter.parent.parent


def _read_bounded_json(path: Path) -> dict[str, Any] | None:
    """Parse ``path`` as a JSON object, refusing anything that is not a bounded
    regular file.

    ``is_file()`` follows symlinks — which is wanted, a packaged manifest may
    legitimately be one — but rejects a FIFO, device, or directory. Without that
    check a FIFO at the manifest path blocks ``doctor`` forever, turning a
    read-only diagnostic into blocking IPC (review round 2, F1).
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        with path.open("rb") as fh:
            raw = fh.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_MANIFEST_BYTES:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def read_manifest(executable: str | Path) -> dict[str, Any] | None:
    """The capability manifest of the install that ``executable`` belongs to.

    ``None`` means *no evidence of safety*: the path is missing, its package
    directory cannot be located, it ships no manifest (every build predating the
    profile), or the manifest is not a bounded regular file. Callers must treat
    ``None`` as unsafe rather than unknown.

    Purely a filesystem read — no import, no subprocess. The executable named in
    a hook file is not necessarily trustworthy, and a subprocess's side effects
    land before any validation could reject them (round-1 blocker).

    .. rubric:: What this does and does not prove

    **This is a staleness gate, not a security boundary.** It answers "is the
    install behind this hook new enough to be safe to run automatically?" — the
    2026-07-09-shim question. It does **not** authenticate the install:

    * When the console script has a shebang, the manifest must live under the
      interpreter's own root, so a manifest planted beside an unrelated binary
      named ``neurobase`` is rejected.
    * When it does not — a Windows ``.exe`` wrapper, or a script whose shebang is
      unreadable — directory convention is the only available evidence, and a
      planted manifest would be believed.

    Closing that fully needs signing, and it would buy little: anyone able to
    write ``~/.claude/settings.json`` already has arbitrary code execution the
    next time an agent starts a session, whatever ``doctor`` reports. The gate
    exists to catch a stale install, and it should not imply more than it can
    deliver (review round 2, F1; user decision 2026-07-27).
    """
    try:
        resolved = Path(executable).resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None

    interpreter_root = _interpreter_root(resolved)
    if interpreter_root is _Shebang.AMBIGUOUS:
        return None  # a layout we decline to vouch for; see _interpreter_root
    # Exact identity, not containment. "Somewhere beneath the claimed root"
    # certified a nested foreign install whose interpreter imports from the
    # *outer* environment (review round 3, F1).
    if isinstance(interpreter_root, Path) and resolved.parent.parent != interpreter_root:
        return None

    for package_dir in _site_packages_candidates(resolved):
        parsed = _read_bounded_json(package_dir / MANIFEST_NAME)
        if parsed is not None:
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
