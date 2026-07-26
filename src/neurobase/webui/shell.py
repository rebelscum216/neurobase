"""The app shell's shared context — the left-rail nav with live counts and the
active-surface label — injected into every template by a Jinja2 context
processor registered in ``app.py`` (app-shell plan, Phase 2a).

Keeping it here means no route handler threads rail state by hand, and a surface
becomes a real nav link the moment its route lands: flip its ``_NAV`` entry to
``enabled=True``. Until then it renders as an inert "soon" stub (never an ``<a>``
that could 404).

Every read is **fail-soft**: this runs on *every* page render, so a broken
project or an unreadable store must degrade the rail's counts, never turn the
whole chrome into a 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request

from neurobase.core.store_handle import StoreMode, open_store
from neurobase.recommender import proposals

# key · label · glyph · href · enabled — order is the rail order (Graph is the
# home surface). Flip ``enabled`` to True as each surface's route is added.
_NAV: list[tuple[str, str, str, str, bool]] = [
    ("graph", "Graph", "◉", "/graph", False),
    ("sessions", "Sessions", "◵", "/sessions", True),
    ("memory", "Memory", "◈", "/memory", False),
    ("suggestions", "Suggestions", "△", "/suggestions", True),
    ("skills", "Skills", "✦", "/skills", False),
    ("status", "Status", "◷", "/status", False),
]


def _counts(root: Path) -> dict[str, int]:
    """Live rail badges: raw captures (Sessions), active curated facts (Memory),
    pending proposals (Suggestions), and accepted proposals (Skills). Fail-soft
    per project — one unreadable project must not blank the whole rail."""
    # Reach the store through a validated READ handle (ADR-0015 chokepoint) — the
    # registry + list accessors live on the handle, never called raw-root here.
    handle = open_store(root, StoreMode.READ)
    sessions = memory = 0
    for slug in handle.load_registry():
        try:
            sessions += len(handle.list_raw(slug, unconsumed_only=False))
            memory += len(handle.list_curated(slug, active_only=True))
        except (OSError, ValueError):
            continue
    pending = skills = 0
    try:
        for doc in proposals.load_all_proposals(root):
            status = str(doc.get("status") or "proposed")
            if status == "proposed":
                pending += 1
            elif status == "accepted":
                skills += 1
    except (OSError, ValueError):
        pass
    return {"sessions": sessions, "memory": memory, "suggestions": pending, "skills": skills}


def _is_active(path: str, href: str) -> bool:
    if href == "/suggestions" and path == "/":
        return True  # home currently redirects to Suggestions (until Graph lands)
    return path == href or path.startswith(href + "/")


def shell_context(request: Request) -> dict[str, Any]:
    """Context processor (registered on the Jinja env in ``app.py``): the rail
    nav with live counts + the active surface's label, merged into every
    template's context."""
    root = request.app.state.root
    assert isinstance(root, Path)
    try:
        counts = _counts(root)
    except Exception:  # noqa: BLE001 - the rail must never 500 the page it decorates
        counts = {}
    path = request.url.path
    nav: list[dict[str, Any]] = []
    active_label = "neurobase"
    for key, label, glyph, href, enabled in _NAV:
        active = enabled and _is_active(path, href)
        if active:
            active_label = label
        nav.append(
            {
                "key": key,
                "label": label,
                "glyph": glyph,
                "href": href,
                "enabled": enabled,
                "active": active,
                "count": counts.get(key),
            }
        )
    return {"nav": nav, "active_label": active_label}
