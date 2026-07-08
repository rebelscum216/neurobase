"""Claude Code hook installer (spec §7).

Merges Neurobase's SessionEnd (scribe) + SessionStart (recall) hooks into a
Claude Code ``settings.json`` — user (`~/.claude/settings.json`) or project
(`<repo>/.claude/settings.json`). Ownership is fenced: an entry is
Neurobase-owned **iff its command invokes a ``neurobase`` executable's ``hook``
subcommand** (spec §7: contains ``<shim>/neurobase hook``), and only such
entries are ever created, replaced, or removed. Everything else in the file is
preserved.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

# A hook entry is Neurobase-owned **iff its command invokes a ``neurobase``
# executable's ``hook`` subcommand** (spec §7: command contains
# ``<shim>/neurobase hook``). We match the *path component* — ``neurobase``
# (optionally ``.exe``) preceded by a path separator or start-of-string, then
# whitespace + ``hook`` + a word boundary — not a bare ``neurobase hook``
# substring. This (a) excludes prose mentions like ``echo "run neurobase hook
# ..."`` (neurobase is preceded by a space/quote, not a separator), (b) still
# recognizes an entry written by an *older* shim path so init replaces it
# instead of stacking a duplicate, and (c) matches Windows ``\neurobase.exe
# hook`` commands. ``hook(?=\s|$)`` keeps the old anti-``hookX`` guarantee.
_OWNED_RE = re.compile(r"(?:^|[/\\])neurobase(?:\.exe)?\s+hook(?=\s|$)")


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
        if isinstance(entry, dict) and _OWNED_RE.search(str(entry.get("command", ""))):
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


def _remove_owned_event(existing_groups: Any) -> list[Any]:
    """Drop Neurobase-owned groups and keep every foreign group verbatim."""
    return [g for g in (existing_groups or []) if not _is_owned_group(g)]


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


def remove_owned_settings(existing: dict[str, Any]) -> dict[str, Any]:
    """Return ``existing`` with only Neurobase-owned hook groups removed.

    This is the surgical uninstall counterpart to ``build_settings``: unrelated
    top-level keys, events, and hook groups are preserved byte-for-byte after
    JSON round-trip rendering.
    """
    result = copy.deepcopy(existing)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    new_hooks: dict[str, Any] = {}
    for event, groups in hooks.items():
        kept = _remove_owned_event(groups)
        if kept:
            new_hooks[event] = kept
    if new_hooks:
        result["hooks"] = new_hooks
    else:
        result.pop("hooks", None)
    return result


def render(settings: dict[str, Any]) -> str:
    """Canonical on-disk form (2-space JSON + trailing newline)."""
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(settings), encoding="utf-8")
    tmp.replace(path)
