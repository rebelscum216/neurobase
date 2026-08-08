"""Intent markers for detached curate passes (TODO item 1, 2026-08-07).

A detached ``curate --if-stale`` is spawned and forgotten. If it dies before it
reaches ``_log_pass`` — killed, OOM, machine slept, a crash in argument parsing —
it leaves **no trace at all**, and the trigger silently degrades into the
fire-and-forget shape that G16 was filed for. The defect G16 named was *silence*,
not detachment, so the fix is visibility: the **spawner** records the intent
*before* the child runs, the child clears it on the way out, and ``doctor``
reports the leftovers.

**The marker is the load-bearing part.** Without it, "the pass logged nothing" and
"no pass was ever spawned" are the same observation.

Lifecycle::

    spawner   write(handle, project, trigger=...) -> <memory>/.curator-spawns/<id>.json
    child     clear(handle, project, spawn_id)     -> unlinked on EVERY exit path
    doctor    abandoned(handle, project)           -> markers older than the grace window
    engine    sweep(handle, project)               -> clears them, count rides the pass record

**The child must clear on every exit path, not just a completed pass.** Three
returns in ``cli.curate`` write no journal record at all — "Not stale", "Curate
already running", and "no brain backend" — so a marker resolved by *looking for a
pass record* would report an abandoned pass every time a hook fired on a fresh
store. Clearing is therefore a ``finally``, and it is the child's own id only.

**No liveness probe, no takeover protocol.** ``core.lock`` explains at length why
this codebase does not hand-roll staleness for *mutual exclusion*; a marker is not
exclusion, it is a report, so a plain age threshold is the whole policy. ``pid``
rides the record for a human reading a report — it is never consulted to decide
whether a marker is abandoned. The grace window sits well above any budgeted pass
(the auto tier stops at ``auto_max_seconds`` = 900s, and a single hung brain call
can add its own timeout on top), so the worst case of a pathologically slow pass
is one self-clearing ``warn``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from neurobase.core.store import InvalidSlugError
from neurobase.core.store_handle import StoreHandle

#: Markers live beside the curator journal, under the project's memory dir: a
#: dotted directory, outside every store glob (``curated/*.md``, ``nodes/*.md``,
#: ``.tombstones/*.md``), so no reader of the store can see them.
SPAWN_DIR = ".curator-spawns"

#: How old a marker must be before anything calls it abandoned. Sized above the
#: auto tier's own ceiling (``auto_max_seconds`` = 900s) plus room for one brain
#: call hanging to its timeout, so a *slow* pass is never reported as a *dead*
#: one. Deliberately not a config knob: it is a reporting threshold derived from
#: another setting, and a second tunable that must stay above the first is an
#: inconsistency waiting to be filed.
ABANDONED_AFTER_SECONDS = 3600


def _dir(handle: StoreHandle, project: str) -> Path:
    """Markers are addressed through the handle, never a raw root.

    ADR-0015 step 5: the store tree is reached through a validated
    ``StoreHandle``, and ``scripts/check_store_chokepoint.py`` fails the gate on
    a raw-root ``store.memory_dir(...)``. Every caller here already holds a
    handle — the spawner opens one to resolve the project, the CLI holds its READ
    guard, doctor its DOCTOR handle, the engine its WRITE handle — so taking one
    costs nothing and keeps this module inside the chokepoint.
    """
    return handle.memory_dir(project) / SPAWN_DIR


def _age_seconds(record: dict, now: datetime) -> float | None:
    """Seconds since the marker was written, or ``None`` if it cannot be read.

    An unparseable timestamp returns ``None`` rather than 0 or infinity: the
    caller decides what an uninterpretable marker means, and neither "brand new"
    nor "ancient" is a fact we have.
    """
    stamp = record.get("at")
    if not isinstance(stamp, str):
        return None
    try:
        written = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return (now - written).total_seconds()


def write(handle: StoreHandle, project: str, *, trigger: str) -> str | None:
    """Record the intent to run a detached pass; returns the spawn id.

    Called by the spawner **before** ``Popen``, which is the whole point — a
    marker written after the fork cannot describe a child that died immediately.
    Best-effort: a store that cannot be written must never stop the pass from
    being attempted, so any ``OSError`` returns ``None`` and the caller spawns
    unmarked.
    """
    spawn_id = uuid.uuid4().hex
    record = {
        "spawn_id": spawn_id,
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trigger": trigger,
        "project": project,
        # Informational only — never consulted to decide abandonment (see module
        # docstring). A recycled pid must not be able to resurrect a dead pass.
        "pid": os.getpid(),
    }
    try:
        target = _dir(handle, project)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{spawn_id}.json").write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        return None
    return spawn_id


def clear(handle: StoreHandle, project: str, spawn_id: str) -> None:
    """Resolve one marker — the child's own, on any exit path. Never raises."""
    if not spawn_id:
        return
    try:
        (_dir(handle, project) / f"{spawn_id}.json").unlink(missing_ok=True)
    except (OSError, InvalidSlugError):
        return


def _read_all(handle: StoreHandle, project: str) -> list[tuple[Path, dict]]:
    try:
        entries = sorted(_dir(handle, project).glob("*.json"))
    except (OSError, InvalidSlugError):
        return []
    found: list[tuple[Path, dict]] = []
    for path in entries:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # An unreadable marker is still evidence that a spawn happened, so it
            # is reported rather than dropped; the empty dict ages via mtime below.
            parsed = {}
        found.append((path, parsed if isinstance(parsed, dict) else {}))
    return found


def abandoned(
    handle: StoreHandle,
    project: str,
    *,
    older_than_seconds: float = ABANDONED_AFTER_SECONDS,
    now: datetime | None = None,
) -> list[dict]:
    """Markers old enough that the pass they describe never came back.

    A marker whose own timestamp is missing or unparseable falls back to the
    file's mtime, so a corrupt record cannot make an abandoned pass invisible —
    which is exactly the silence this mechanism exists to end.
    """
    moment = now or datetime.now(UTC)
    stale: list[dict] = []
    for path, record in _read_all(handle, project):
        age = _age_seconds(record, moment)
        if age is None:
            try:
                age = moment.timestamp() - path.stat().st_mtime
            except OSError:
                continue
        if age >= older_than_seconds:
            stale.append({**record, "age_seconds": int(age), "path": str(path)})
    return stale


def sweep(
    handle: StoreHandle,
    project: str,
    *,
    older_than_seconds: float = ABANDONED_AFTER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Clear abandoned markers, returning how many were cleared.

    Called by a pass that is itself running: its success is the evidence that
    curation recovered, so the warning should not outlive it. The **count** rides
    that pass's journal record (``abandoned_spawns``), which is what keeps the
    evidence durable — a swept marker leaves a permanent trace in the log rather
    than vanishing, so this is a clearing of the *alarm*, never of the *record*.
    """
    cleared = 0
    for record in abandoned(handle, project, older_than_seconds=older_than_seconds, now=now):
        try:
            Path(record["path"]).unlink(missing_ok=True)
        except (OSError, KeyError):
            continue
        cleared += 1
    return cleared
