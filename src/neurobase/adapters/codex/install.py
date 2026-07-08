"""Codex hook installer (spec §7).

Codex is wired differently from Claude (spec §7, ADR-0005/0006). ``init --agent
codex`` writes **two** files:

1. A ``hooks.json`` — user (``~/.codex/hooks.json``) or project
   (``<repo>/.codex/hooks.json``) — with **CamelCase** event names
   (``SessionStart``, ``Stop``; Codex's own canonical on-disk form) whose
   handlers are ``type:"command"`` invoking the absolute shim
   (``<shim>/neurobase hook codex session-start`` / ``... stop``). Codex
   tokenizes a command *string* with its args (ADR-0006), so we use the same
   string form as the Claude installer.
2. For **project** scope only, the ``[projects."<repo-abs-path>"]`` table in
   ``~/.codex/config.toml`` — a project ``hooks.json`` is **not** discovered by
   existing on disk; that table must set ``hooks = ".codex/hooks.json"`` and
   ``trust_level = "trusted"`` or the hook never fires (live-verified, spec §7).
   User scope (``~/.codex/hooks.json``) is global and auto-discovered, so it
   needs no config edit.

Ownership is fenced exactly like the Claude installer: a hooks.json handler is
Neurobase-owned **iff its command invokes a ``neurobase`` executable's
``hook codex`` subcommand**, and only such handlers are ever created, replaced,
or removed. The ``config.toml`` edit is **surgical** — it adds/updates only the
two keys in that one project table and preserves every comment, other table, and
other key (a full ``tomllib``→``tomli_w`` round-trip would strip comments and
reorder the user's real Codex config).
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

# A hooks.json handler is Neurobase-owned **iff its command invokes a
# ``neurobase`` executable's ``hook codex`` subcommand** (spec §7). Same
# path-anchored discipline as the Claude installer's ``_OWNED_RE``: match
# ``neurobase`` (optionally ``.exe``) as a path component — preceded by a
# separator or start-of-string — then ``hook codex`` + a word boundary. This
# (a) excludes prose mentions like ``echo "run neurobase hook codex ..."``,
# (b) still recognizes an *older* shim path so init replaces rather than stacks,
# (c) matches Windows ``\neurobase.exe hook codex`` commands, and (d) does not
# match the Claude ``hook claude`` handler (different subcommand).
_OWNED_RE = re.compile(r"(?:^|[/\\])neurobase(?:\.exe)?\s+hook\s+codex(?=\s|$)")

# The relative hooks.json path recorded in the project's config.toml table
# (resolved by Codex relative to the project dir).
PROJECT_HOOKS_REL = ".codex/hooks.json"


class HooksParseError(RuntimeError):
    """The target hooks.json exists but isn't valid JSON — refuse to clobber."""


class ConfigParseError(RuntimeError):
    """~/.codex/config.toml exists but isn't valid TOML — refuse to clobber."""


def shim_path() -> str:
    """Absolute path to the ``neurobase`` executable (spec D4: hooks reference
    the absolute shim, never a bare name). Mirrors the Claude installer."""
    found = shutil.which("neurobase")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


# --- hooks.json -----------------------------------------------------------


def hooks_json_path(*, user: bool, cwd: Path) -> Path:
    """User scope → ``~/.codex/hooks.json``; project scope →
    ``<cwd>/.codex/hooks.json``."""
    base = Path.home() if user else cwd
    return base / ".codex" / "hooks.json"


def config_path() -> Path:
    """Codex's global config — always ``~/.codex/config.toml`` regardless of
    scope (the ``[projects.*]`` trust/hooks tables live only here)."""
    return Path.home() / ".codex" / "config.toml"


def load_hooks(path: Path) -> dict[str, Any]:
    """Parse an existing hooks.json (``{}`` if absent). Raises
    ``HooksParseError`` rather than clobber a malformed file."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HooksParseError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HooksParseError(f"{path} is not a JSON object")
    return data


def _is_owned_group(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    for entry in group.get("hooks", []) or []:
        if isinstance(entry, dict) and _OWNED_RE.search(str(entry.get("command", ""))):
            return True
    return False


def _start_group(shim: str) -> dict[str, Any]:
    return {"hooks": [{"type": "command", "command": f"{shim} hook codex session-start"}]}


def _stop_group(shim: str) -> dict[str, Any]:
    return {"hooks": [{"type": "command", "command": f"{shim} hook codex stop"}]}


def _merge_event(existing_groups: Any, owned_group: dict[str, Any]) -> list[Any]:
    """Drop any Neurobase-owned groups, keep everything else, then append the
    fresh owned group. Idempotent."""
    kept = [g for g in (existing_groups or []) if not _is_owned_group(g)]
    kept.append(owned_group)
    return kept


def _remove_owned_event(existing_groups: Any) -> list[Any]:
    """Drop Neurobase-owned groups and keep every foreign group verbatim."""
    return [g for g in (existing_groups or []) if not _is_owned_group(g)]


def build_hooks(existing: dict[str, Any], shim: str) -> dict[str, Any]:
    """Return the hooks.json dict with Neurobase's Codex hooks installed —
    preserving every non-owned key and handler. The whole file is wrapped in a
    top-level ``"hooks"`` key (spec §7)."""
    result = copy.deepcopy(existing)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks = dict(hooks)
    hooks["SessionStart"] = _merge_event(hooks.get("SessionStart"), _start_group(shim))
    hooks["Stop"] = _merge_event(hooks.get("Stop"), _stop_group(shim))
    result["hooks"] = hooks
    return result


def remove_owned_hooks(existing: dict[str, Any]) -> dict[str, Any]:
    """Return ``existing`` with only Neurobase-owned hook groups removed."""
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


def render_hooks(hooks_doc: dict[str, Any]) -> str:
    """Canonical on-disk form (2-space JSON + trailing newline)."""
    return json.dumps(hooks_doc, indent=2, ensure_ascii=False) + "\n"


def write_hooks(path: Path, hooks_doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render_hooks(hooks_doc), encoding="utf-8")
    tmp.replace(path)


# --- config.toml (surgical) -----------------------------------------------

_TABLE_HEADER_RE = re.compile(r"^\s*\[(?!\[)\s*(.+?)\s*\]\s*(?:#.*)?$")
_ANY_HEADER_RE = re.compile(r"^\s*\[")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def _toml_basic_string(value: str) -> str:
    """Render ``value`` as a TOML basic (double-quoted) string, escaped."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _parse_dotted_key(text: str) -> list[str] | None:
    """Tokenize a TOML dotted key (bare / basic-string / literal-string
    segments, dot-separated) into its decoded components, or ``None`` if it
    isn't a well-formed dotted key. Used to identify the target table header
    regardless of how its path segment is quoted/escaped."""
    parts: list[str] = []
    i, n = 0, len(text)
    expect_key = True
    while i < n:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        if expect_key:
            ch = text[i]
            if ch == '"':
                j, buf = i + 1, []
                while j < n:
                    if text[j] == '"':
                        break
                    if text[j] == "\\" and j + 1 < n:
                        esc = text[j + 1]
                        if esc in _ESCAPES:
                            buf.append(_ESCAPES[esc])
                            j += 2
                            continue
                        if esc in ("u", "U"):
                            width = 4 if esc == "u" else 8
                            hexs = text[j + 2 : j + 2 + width]
                            if len(hexs) != width:
                                return None
                            try:
                                buf.append(chr(int(hexs, 16)))
                            except ValueError:
                                return None
                            j += 2 + width
                            continue
                        return None
                    buf.append(text[j])
                    j += 1
                if j >= n:
                    return None  # unterminated
                parts.append("".join(buf))
                i = j + 1
            elif ch == "'":
                j = text.find("'", i + 1)
                if j == -1:
                    return None
                parts.append(text[i + 1 : j])
                i = j + 1
            else:
                j = i
                while j < n and (text[j].isalnum() or text[j] in "_-"):
                    j += 1
                if j == i:
                    return None
                parts.append(text[i:j])
                i = j
            expect_key = False
        else:
            if text[i] != ".":
                return None
            expect_key = True
            i += 1
    if expect_key or not parts:
        return None  # empty or trailing dot
    return parts


def _parse_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(f"config.toml is not valid TOML: {exc}") from exc


def load_config_text(path: Path) -> str:
    """Read config.toml text (``""`` if absent), validating it parses. Raises
    ``ConfigParseError`` rather than clobber a malformed file."""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    _parse_toml(text)  # refuse to touch a file we can't parse
    return text


def _find_table_header(lines: list[str], target: list[str]) -> int | None:
    for idx, line in enumerate(lines):
        m = _TABLE_HEADER_RE.match(line)
        if m and _parse_dotted_key(m.group(1)) == target:
            return idx
    return None


def _assigns_key(line: str, key: str) -> bool:
    esc = re.escape(key)
    return re.match(rf"^\s*(?:{esc}|\"{esc}\"|'{esc}')\s*=", line) is not None


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _append_table(text: str, project_key: str, key_lines: list[str]) -> str:
    header = f"[projects.{_toml_basic_string(project_key)}]"
    block = "\n".join([header, *key_lines])
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"  # blank line before the new table
    return text + block + "\n"


def _update_table(lines: list[str], header_idx: int, updates: dict[str, str]) -> str:
    body_end = len(lines)
    for j in range(header_idx + 1, len(lines)):
        if _ANY_HEADER_RE.match(lines[j]):
            body_end = j
            break
    result = list(lines)
    remaining = dict(updates)
    for j in range(header_idx + 1, body_end):
        for key in list(remaining):
            if _assigns_key(lines[j], key):
                result[j] = _leading_ws(lines[j]) + remaining.pop(key)
                break
    to_insert = [remaining[k] for k in ("trust_level", "hooks") if k in remaining]
    result[header_idx + 1 : header_idx + 1] = to_insert
    return "\n".join(result)


def _remove_table_key(lines: list[str], header_idx: int, key: str) -> str:
    body_end = len(lines)
    for j in range(header_idx + 1, len(lines)):
        if _ANY_HEADER_RE.match(lines[j]):
            body_end = j
            break
    result = [
        line
        for j, line in enumerate(lines)
        if not (header_idx < j < body_end and _assigns_key(line, key))
    ]
    return "\n".join(result)


def merge_config(existing_text: str, project_key: str, hooks_rel: str = PROJECT_HOOKS_REL) -> str:
    """Surgically ensure ``[projects."<project_key>"]`` sets
    ``trust_level = "trusted"`` and ``hooks = "<hooks_rel>"``, preserving all
    other content. Returns the (possibly unchanged) full config.toml text.

    Idempotent: if the table already has both correct values the input is
    returned verbatim. The result is re-parsed and re-checked before return, so
    a corrupt or ineffective edit raises ``ConfigParseError`` rather than being
    written."""
    parsed = _parse_toml(existing_text)
    projects = parsed.get("projects")
    entry = projects.get(project_key) if isinstance(projects, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("trust_level") == "trusted"
        and entry.get("hooks") == hooks_rel
    ):
        return existing_text

    trust_line = 'trust_level = "trusted"'
    hooks_line = f"hooks = {_toml_basic_string(hooks_rel)}"
    lines = existing_text.split("\n")
    header_idx = _find_table_header(lines, ["projects", project_key])
    if header_idx is None:
        if isinstance(projects, dict) and project_key in projects:
            # tomllib sees the table but we can't locate its header line (e.g. an
            # inline `projects = { "<key>" = {...} }` form). Appending would
            # duplicate the key and corrupt the file — refuse instead.
            raise ConfigParseError(
                f'cannot surgically edit [projects."{project_key}"] '
                "(defined in a form this installer does not rewrite)"
            )
        new_text = _append_table(existing_text, project_key, [trust_line, hooks_line])
    else:
        new_text = _update_table(
            lines, header_idx, {"trust_level": trust_line, "hooks": hooks_line}
        )

    check = _parse_toml(new_text)  # never emit unparseable TOML
    projects_out = check.get("projects", {})
    got = projects_out.get(project_key, {}) if isinstance(projects_out, dict) else {}
    if not (
        isinstance(got, dict)
        and got.get("trust_level") == "trusted"
        and got.get("hooks") == hooks_rel
    ):
        raise ConfigParseError("surgical config edit did not produce the expected keys")
    return new_text


def remove_project_hooks_config(
    existing_text: str, project_key: str, hooks_rel: str = PROJECT_HOOKS_REL
) -> str:
    """Remove Neurobase's project ``hooks`` setting from config.toml.

    ``trust_level`` and unrelated project keys are intentionally left alone:
    directory trust can be user-owned, while ``hooks = ".codex/hooks.json"`` is
    the Neurobase discovery edge created by init.
    """
    parsed = _parse_toml(existing_text)
    projects = parsed.get("projects")
    entry = projects.get(project_key) if isinstance(projects, dict) else None
    if not isinstance(entry, dict) or entry.get("hooks") != hooks_rel:
        return existing_text

    lines = existing_text.split("\n")
    header_idx = _find_table_header(lines, ["projects", project_key])
    if header_idx is None:
        raise ConfigParseError(
            f'cannot surgically edit [projects."{project_key}"] '
            "(defined in a form this installer does not rewrite)"
        )
    new_text = _remove_table_key(lines, header_idx, "hooks")
    check = _parse_toml(new_text)
    projects_out = check.get("projects", {})
    got = projects_out.get(project_key, {}) if isinstance(projects_out, dict) else {}
    if isinstance(got, dict) and got.get("hooks") == hooks_rel:
        raise ConfigParseError("surgical config edit did not remove the hooks key")
    return new_text


def write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
