"""Claude Code hook installer (spec §7).

Merges Neurobase's SessionEnd (scribe) + SessionStart (recall) hooks into a
Claude Code ``settings.json`` — user (`~/.claude/settings.json`) or project
(`<repo>/.claude/settings.json`). Ownership is fenced: an entry is
Neurobase-owned **iff its command contains ``neurobase hook``**, and only such
entries are ever created, replaced, or removed. Everything else in the file is
preserved.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

# A hook entry is Neurobase-owned iff its command contains this (spec §7). The
# trailing space avoids matching an unrelated command like ".../neurobase hookX".
_OWNED_MARKER = "neurobase hook "


class SettingsParseError(RuntimeError):
    """The target settings.json exists but isn't valid JSON — refuse to clobber."""


def shim_path() -> str:
    """Absolute path to the ``neurobase`` executable (spec D4: hooks reference
    the absolute shim, never a bare name)."""
    found = shutil.which("neurobase")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


def settings_path(*, user: bool, cwd: Path) -> Path:
    """User scope → ``~/.claude/settings.json``; project scope →
    ``<cwd>/.claude/settings.json``."""
    base = Path.home() if user else cwd
    return base / ".claude" / "settings.json"


def load_settings(path: Path) -> dict[str, Any]:
    """Parse an existing settings.json (``{}`` if absent). Raises
    ``SettingsParseError`` rather than clobber a malformed file."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SettingsParseError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsParseError(f"{path} is not a JSON object")
    return data


def _is_owned_group(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    for entry in group.get("hooks", []) or []:
        if isinstance(entry, dict) and _OWNED_MARKER in str(entry.get("command", "")):
            return True
    return False


def _end_group(shim: str) -> dict[str, Any]:
    return {"hooks": [{"type": "command", "command": f"{shim} hook claude session-end"}]}


def _start_group(shim: str, sources: list[str]) -> dict[str, Any]:
    return {
        "matcher": "|".join(sources),
        "hooks": [{"type": "command", "command": f"{shim} hook claude session-start"}],
    }


def _merge_event(existing_groups: Any, owned_group: dict[str, Any]) -> list[Any]:
    """Drop any Neurobase-owned groups, keep everything else, then append the
    fresh owned group. Idempotent."""
    kept = [g for g in (existing_groups or []) if not _is_owned_group(g)]
    kept.append(owned_group)
    return kept


def build_settings(existing: dict[str, Any], shim: str, sources: list[str]) -> dict[str, Any]:
    """Return the settings dict with Neurobase's Claude hooks installed —
    preserving every non-owned key and hook."""
    result = copy.deepcopy(existing)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks = dict(hooks)
    hooks["SessionEnd"] = _merge_event(hooks.get("SessionEnd"), _end_group(shim))
    hooks["SessionStart"] = _merge_event(hooks.get("SessionStart"), _start_group(shim, sources))
    result["hooks"] = hooks
    return result


def render(settings: dict[str, Any]) -> str:
    """Canonical on-disk form (2-space JSON + trailing newline)."""
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(settings), encoding="utf-8")
    tmp.replace(path)
