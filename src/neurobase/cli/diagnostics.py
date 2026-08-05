"""Lifecycle diagnostics for ``neurobase doctor`` (build-plan Phase 6).

Doctor is intentionally read-only: unlike normal store entry points, it does
not create ``store.toml`` just to report on store health.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.brain import resolve_brain
from neurobase.brain import select as brain_select
from neurobase.core import capabilities, projects, store
from neurobase.core.config import Config
from neurobase.core.store_handle import StoreHandle, StoreMode, open_store

Status = str

#: Consecutive failed passes at which curate health stops being a warning. Three
#: is the point where "the provider hiccuped" stops explaining it and the node is
#: reliably frozen.
_CURATE_FAILURE_ERROR_THRESHOLD = 3

# --- the curator's status vocabulary (curator/engine.py) --------------------
# Classified explicitly rather than "anything that isn't ok is a failure": the
# engine emits seven statuses, and three of them are *not* failures. Treating a
# `noop` as a failed pass reported an idle project as broken.

#: Node was (re)generated. `resynth` is a successful regeneration from existing
#: facts, so it clears node staleness exactly as `ok` does.
_CURATE_SUCCESS = frozenset({"ok", "resynth"})

#: Nothing was attempted. Neither breaks a healthy streak nor counts against it:
#: `noop` means no unconsumed raw, `skipped-locked` means another writer held the
#: project lock (the single-flight guard working as designed), `dry-run` means
#: the user asked for a plan and no write.
_CURATE_NEUTRAL = frozenset({"noop", "skipped-locked", "dry-run"})

#: A pass that meant to produce a node and did not.
#:
#: `status` alone is not sufficient here and must not be used alone: the engine
#: logs `error` for a *plan* failure, after synthesis has already refreshed the
#: node from whatever committed, and for a failed `--dry-run`, which never
#: attempts synthesis at all. Records carrying `node_refreshed` are classified on
#: that journaled fact; this set is the fallback for records written before the
#: field existed (review round 2, F3).
_CURATE_FAILURE = frozenset({"error", "partial"})


class LogState(Enum):
    """Why a curator-log read produced the entries it did.

    Kept separate from the entries themselves because *no history* and *history
    we cannot vouch for* are different diagnoses. An earlier revision collapsed
    them into one cheerful "no curate passes recorded", so a permissions failure
    restored exactly the all-green blindness this check exists to prevent.
    """

    #: No file at all — the only state that *proves* nothing has ever run.
    MISSING = "missing"
    #: The file exists but holds nothing. The producer only ever appends whole
    #: records, so an empty log is truncation, not a fresh install (round 3, F2).
    EMPTY = "empty"
    UNREADABLE = "unreadable"
    UNPARSEABLE = "unparseable"
    #: Readable, but some record we cannot excuse is malformed. Only an
    #: *incomplete* final line may be torn (the writer appends), so anything else
    #: means the history is damaged in a way we cannot characterize.
    DAMAGED = "damaged"
    OK = "ok"


def _is_wellformed_record(entry: object) -> bool:
    """Does this decode to a record doctor can reason about?

    Container type alone is not enough: ``{"status": "ok"}`` with no ``at`` was
    accepted as proof of a refresh and printed ``last refresh None`` (round 3,
    F2). The producer always writes both fields, so a record missing either is
    not one of ours.
    """
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("status"), str):
        return False
    if not isinstance(entry.get("at"), str):
        return False
    for optional, expected in (("node_refreshed", bool), ("dry_run", bool)):
        value = entry.get(optional)
        if value is not None and not isinstance(value, expected):
            return False
    return True


def _read_curator_log(path: Path) -> tuple[list[dict], LogState]:
    """Curator-log entries plus the state of the read.

    An **incomplete** trailing line stays fail-soft: the writer appends, so the
    last record can legitimately be mid-write. Everything else is damage. Note
    the exception is narrower than "the last line" — a *complete* trailing JSON
    value that is not a well-formed record (``[]``, or a dict missing ``at``)
    cannot be a half-written dict, so it does not qualify (round 3, F2).
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], LogState.MISSING
    except OSError:
        return [], LogState.UNREADABLE

    if not raw.strip():
        return [], LogState.EMPTY

    # The producer appends `json.dumps(...) + "\n"` as one write, so a **missing
    # trailing newline** is the byte-level signature of an interrupted append —
    # and the only thing that earns the fail-soft exception. Excusing "the last
    # line failed to parse" instead let a complete garbage line read as healthy
    # (review round 4, F2).
    torn_tail = not raw.endswith(b"\n")
    byte_lines = [line for line in raw.split(b"\n") if line.strip()]

    entries: list[dict] = []
    damaged = False
    last_index = len(byte_lines) - 1
    for index, blob in enumerate(byte_lines):
        excusable = torn_tail and index == last_index
        try:
            line = blob.decode("utf-8")
        except UnicodeDecodeError:
            # A multi-byte character cut mid-sequence by an interrupted write is
            # the same torn-append case; anywhere else it is damage. Either way
            # doctor reports — decoding must never abort a diagnostic.
            if not excusable:
                damaged = True
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            if not excusable:
                damaged = True
            continue
        if _is_wellformed_record(parsed):
            entries.append(parsed)
        else:
            # Complete, decodable, well-formed JSON that is not one of our
            # records cannot be a half-written dict — never excusable.
            damaged = True

    if not entries:
        return [], LogState.UNPARSEABLE
    if damaged:
        return entries, LogState.DAMAGED
    return entries, LogState.OK


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    remedy: str | None = None


def _check(name: str, status: Status, detail: str, remedy: str | None = None) -> Check:
    return Check(name=name, status=status, detail=detail, remedy=remedy)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, str(exc)


def _read_toml(path: Path) -> tuple[dict | None, str | None]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, str(exc)


def _commands_for_event(doc: object, event: str) -> list[str]:
    if not isinstance(doc, dict):
        return []
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return []
    commands: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("command"), str):
                commands.append(entry["command"])
    return commands


def _has_command(doc: object, event: str, expected: str) -> bool:
    return expected in _commands_for_event(doc, event)


def _agent_check(binary: str, which: Callable[[str], str | None]) -> Check:
    path = which(binary)
    if path is None:
        return _check(
            f"{binary} cli",
            "warn",
            f"{binary} not found on PATH",
            f"Install/log into {binary} before enabling its hooks.",
        )
    version = brain_select._cli_version(binary)
    label = f"{path} ({version})" if version else path
    return _check(f"{binary} cli", "ok", label)


def _store_checks(root: Path, cwd: Path) -> list[Check]:
    checks: list[Check] = []
    # Sanctioned raw-root call #1 of doctor's THREE (the other two are resolve_project
    # and registry_is_contained in _project_check): the store.toml path is the report
    # *label*, needed even when the store is corrupt and no handle can open. Allow-listed
    # by (file, name) in the step-5 chokepoint guard and named in spec §10; see
    # _project_check's docstring.
    meta = store.store_toml_path(root)
    # D26: doctor inspects store health through a DOCTOR handle. DOCTOR never
    # writes store.toml and — unlike READ/WRITE — carries a schema *newer* than we
    # support instead of raising, so doctor can *report* an unsupported schema
    # rather than refuse. A genuinely-corrupt store.toml (unreadable / invalid
    # TOML / missing-or-non-int schema) still raises UnsupportedSchemaError from
    # open_store; doctor catches it and reports, never crashing — doctor stays a
    # read-only reporting surface (spec §6, ADR-0015 D26).
    handle: StoreHandle | None
    try:
        handle = open_store(root, StoreMode.DOCTOR)
    except store.UnsupportedSchemaError as exc:
        # exc already embeds the store.toml path and a specific reason (unreadable
        # TOML, or a missing/non-int schema) — report it verbatim, don't crash.
        handle = None
        checks.append(_check("store", "error", str(exc)))
    else:
        if handle.schema is None:
            checks.append(
                _check(
                    "store",
                    "warn",
                    f"{root} is not initialized yet",
                    "Run `neurobase enable` or interactive `neurobase init`.",
                )
            )
        elif handle.schema > store.STORE_SCHEMA_VERSION:
            checks.append(
                _check(
                    "store",
                    "error",
                    f"{meta}: unsupported schema {handle.schema}",
                    "Upgrade neurobase-cli before operating on this store.",
                )
            )
        else:
            checks.append(_check("store", "ok", f"{root} (schema {handle.schema})"))

    checks.append(_project_check(handle, root, cwd))
    return checks


def _project_check(handle: StoreHandle | None, root: Path, cwd: Path) -> Check:
    """Project resolution is a registry concern, independent of store.toml health.
    Resolve through the DOCTOR handle when we have one; if store.toml was corrupt
    (no handle), fall back to the registry directly so a broken store.toml does
    not also mask an otherwise-healthy project check.

    The no-handle branch holds doctor's sanctioned raw-root registry reads (the
    other sanctioned read is ``store.store_toml_path`` in ``_store_checks``),
    allow-listed in the step-5 chokepoint guard
    (``scripts/check_store_chokepoint.py``) by (file, name). It is legitimate here
    because ``registry.toml`` is a separate concern from the store-schema chokepoint
    (ADR-0015 registry carve-out, F1), so resolving a project without a validated
    handle must still work when ``store.toml`` is corrupt. The guard checks the
    enumerated store/registry **accessor** spellings + metadata literals in ``src/``
    (spec §10); it is not an exhaustive raw-root detector.

    **Containment is checked before resolution** (Codex P2-SAFETY-SECURITY-010,
    round 5). An out-of-store ``registry.toml`` selects nothing, so resolution
    returns ``None`` — indistinguishable, here, from an ordinary unregistered repo.
    Reporting that as "not enabled" would send the operator to run ``neurobase
    enable``, which fails closed against the same registry, while the real problem
    (a selector pointing outside the store) went unreported. Empty is the correct
    *operational* posture; it must not read as healthy, and this check is where that
    distinction belongs. The fallback branch asks the same question without a
    handle, so a corrupt ``store.toml`` cannot mask a hostile registry either."""
    contained = (
        handle.registry_is_contained()
        if handle is not None
        # Sanctioned raw-root fallback — see the docstring above and the guard's ALLOW.
        else projects.registry_is_contained(root)
    )
    if not contained:
        return _check(
            "project",
            "error",
            f"the registry for {root} resolves outside the store — it selects no projects",
            "Remove or replace the registry symlink so it points inside the store.",
        )
    try:
        slug = (
            handle.resolve_project(cwd)
            if handle is not None
            # Sanctioned raw-root fallback — see the docstring above and the guard's ALLOW.
            else projects.resolve_project(root, cwd)
        )
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return _check("project", "error", f"registry is unreadable or invalid: {exc}")
    if slug is None:
        return _check(
            "project",
            "warn",
            f"{cwd} is not enabled",
            "Run `neurobase enable` in this repo.",
        )
    return _check("project", "ok", f"{cwd} resolves to {slug!r}")


def _brain_check(config: Config) -> Check:
    brain, resolution = resolve_brain(config)
    label = resolution.backend
    if resolution.version:
        label += f" ({resolution.version})"
    if brain is not None:
        return _check("brain", "ok", f"{label} — {resolution.reason}")
    return _check(
        "brain",
        "error",
        f"none — {resolution.reason} (configured backend: {config.brain.backend})",
        "Install/login to Claude or Codex CLI, or configure an API backend.",
    )


def _shim_check(which: Callable[[str], str | None]) -> Check:
    found = which("neurobase")
    if found is None:
        return _check(
            "shim",
            "warn",
            "neurobase is not on PATH",
            "Install with `uv tool install .` or add the shim directory to PATH.",
        )
    return _check("shim", "ok", str(Path(found).resolve()))


def _claude_hook_check_for_path(path: Path, shim: str, scope: str) -> Check:
    if not path.exists():
        return _check(
            "claude hooks",
            "warn",
            f"{path} does not exist",
            "Run `neurobase init --agent claude`.",
        )
    doc, error = _read_json(path)
    if error is not None:
        return _check("claude hooks", "error", f"{path} is invalid JSON: {error}")
    expected_end = f"{shim} hook claude session-end"
    expected_start = f"{shim} hook claude session-start"
    if _has_command(doc, "SessionEnd", expected_end) and _has_command(
        doc, "SessionStart", expected_start
    ):
        return _check("claude hooks", "ok", f"{scope} {path} points at {shim}")
    return _check(
        "claude hooks",
        "warn",
        f"{path} is missing Neurobase hooks for the current shim",
        "Run `neurobase init --agent claude`.",
    )


def _claude_hook_check(cwd: Path, shim: str) -> Check:
    project = _claude_hook_check_for_path(
        claude_install.settings_path(user=False, cwd=cwd), shim, "project"
    )
    if project.status in {"ok", "error"}:
        return project
    user = _claude_hook_check_for_path(
        claude_install.settings_path(user=True, cwd=cwd), shim, "user"
    )
    if user.status in {"ok", "error"}:
        return user
    return project


def _codex_hooks_state(parsed_config: dict) -> dict:
    hooks = parsed_config.get("hooks")
    if not isinstance(hooks, dict):
        return {}
    state = hooks.get("state")
    return state if isinstance(state, dict) else {}


def _has_trusted_hash_for(state: dict, hooks_rel: str, events: set[str]) -> bool:
    found: set[str] = set()
    prefix = f"{hooks_rel}:"
    for key, value in state.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if not value.get("trusted_hash") or not key.startswith(prefix):
            continue
        event = key[len(prefix) :].split(":", 1)[0]
        if event in events:
            found.add(event)
    return events.issubset(found)


def _codex_hooks_file_check(hooks_path: Path, shim: str, scope: str) -> tuple[Check, bool]:
    if not hooks_path.exists():
        return (
            _check(
                "codex hooks",
                "warn",
                f"{hooks_path} does not exist",
                "Run `neurobase init --agent codex`.",
            ),
            False,
        )
    hooks_doc, error = _read_json(hooks_path)
    if error is not None:
        return _check("codex hooks", "error", f"{hooks_path} is invalid JSON: {error}"), False
    expected_start = f"{shim} hook codex session-start"
    expected_stop = f"{shim} hook codex stop"
    if _has_command(hooks_doc, "SessionStart", expected_start) and _has_command(
        hooks_doc, "Stop", expected_stop
    ):
        return _check("codex hooks", "ok", f"{scope} {hooks_path} points at {shim}"), True
    return (
        _check(
            "codex hooks",
            "warn",
            f"{hooks_path} is missing Neurobase hooks for the current shim",
            "Run `neurobase init --agent codex`.",
        ),
        False,
    )


def _codex_trust_check(parsed: dict | None, hooks_refs: tuple[str, ...]) -> Check:
    state = _codex_hooks_state(parsed) if parsed is not None else {}
    events = {"session_start", "stop"}
    for hooks_ref in hooks_refs:
        if _has_trusted_hash_for(state, hooks_ref, events):
            return _check("codex trust", "ok", f"trusted_hash present for {hooks_ref}")
    hooks_ref = hooks_refs[0]
    return _check(
        "codex trust",
        "warn",
        f"no trusted_hash recorded for {hooks_ref}",
        "Launch Codex in this repo and approve the hook prompt.",
    )


def _codex_hook_checks(cwd: Path, shim: str) -> list[Check]:
    project_root = projects.git_common_root(cwd) or cwd
    project_hooks_path = codex_install.hooks_json_path(user=False, cwd=project_root)
    user_hooks_path = codex_install.hooks_json_path(user=True, cwd=project_root)
    cfg_path = codex_install.config_path()
    checks: list[Check] = []

    parsed: dict | None = None
    project_config_ok = False
    if not cfg_path.exists():
        checks.append(
            _check(
                "codex config",
                "warn",
                f"{cfg_path} does not exist",
                "Run `neurobase init --agent codex`.",
            )
        )
    else:
        parsed_candidate, error = _read_toml(cfg_path)
        if error is not None or parsed_candidate is None:
            checks.append(_check("codex config", "error", f"{cfg_path} is invalid TOML: {error}"))
            return checks
        parsed = parsed_candidate
        projects_doc = parsed.get("projects")
        project_entry = (
            projects_doc.get(str(project_root)) if isinstance(projects_doc, dict) else None
        )
        if (
            isinstance(project_entry, dict)
            and project_entry.get("hooks") == codex_install.PROJECT_HOOKS_REL
            and project_entry.get("trust_level") == "trusted"
        ):
            project_config_ok = True
            checks.append(_check("codex config", "ok", f"{cfg_path} wires {project_root}"))
        else:
            checks.append(
                _check(
                    "codex config",
                    "warn",
                    f"{cfg_path} does not wire {project_root}",
                    "Run `neurobase init --agent codex`.",
                )
            )

    if project_config_ok:
        hook_check, hooks_ok = _codex_hooks_file_check(project_hooks_path, shim, "project")
        checks.insert(0, hook_check)
        if hooks_ok:
            checks.append(
                _codex_trust_check(
                    parsed,
                    (str(project_hooks_path), codex_install.PROJECT_HOOKS_REL),
                )
            )
        return checks

    hook_check, hooks_ok = _codex_hooks_file_check(user_hooks_path, shim, "user")
    if hooks_ok or hook_check.status == "error":
        checks.insert(0, hook_check)
        if hooks_ok:
            checks = [check for check in checks if check.name != "codex config"]
            checks.append(_check("codex config", "ok", "user hooks are auto-discovered"))
        if hooks_ok:
            checks.append(_codex_trust_check(parsed, (str(user_hooks_path),)))
        return checks

    project_hook_check, project_hooks_ok = _codex_hooks_file_check(
        project_hooks_path, shim, "project"
    )
    checks.insert(0, project_hook_check)
    if project_hooks_ok:
        checks.append(
            _codex_trust_check(
                parsed,
                (str(project_hooks_path), codex_install.PROJECT_HOOKS_REL),
            )
        )
    return checks


def _claude_mcp_check(shim: str) -> Check:
    """Is the neurobase MCP server registered in ~/.claude.json (spec §13)?"""
    path = claude_install.mcp_config_path()
    if not path.exists():
        return _check(
            "claude mcp", "warn", f"{path} does not exist", "Run `neurobase init --agent claude`."
        )
    try:
        existing = claude_install.load_mcp_config(path)
    except claude_install.SettingsParseError as exc:
        return _check("claude mcp", "error", f"{path} is invalid JSON: {exc}")
    if claude_install.is_mcp_registered(existing, shim):
        return _check("claude mcp", "ok", f"{path} registers neurobase → {shim}")
    if claude_install.is_mcp_registered(existing):
        return _check(
            "claude mcp",
            "warn",
            f"{path} registers neurobase with an unexpected command or args",
            "Run `neurobase init --agent claude` to repair it.",
        )
    return _check(
        "claude mcp",
        "warn",
        f"{path} has no neurobase MCP server",
        "Run `neurobase init --agent claude`.",
    )


def _codex_mcp_check(shim: str) -> Check:
    """Is the neurobase MCP server registered in ~/.codex/config.toml (spec §13)?"""
    path = codex_install.config_path()
    if not path.exists():
        return _check(
            "codex mcp", "warn", f"{path} does not exist", "Run `neurobase init --agent codex`."
        )
    try:
        text = codex_install.load_config_text(path)
    except codex_install.ConfigParseError as exc:
        return _check("codex mcp", "error", f"{path} is invalid TOML: {exc}")
    if codex_install.is_mcp_registered(text, shim):
        return _check("codex mcp", "ok", f"{path} registers neurobase → {shim}")
    if codex_install.is_mcp_registered(text):
        return _check(
            "codex mcp",
            "warn",
            f"{path} registers neurobase with an unexpected command or args",
            "Run `neurobase init --agent codex` to repair it.",
        )
    return _check(
        "codex mcp",
        "warn",
        f"{path} has no neurobase MCP server",
        "Run `neurobase init --agent codex`.",
    )


def _codex_project_hooks_are_wired(project_root: Path) -> bool:
    """Would Codex actually discover ``<project_root>/.codex/hooks.json``?

    Codex only reads a project hooks file that ``~/.codex/config.toml`` wires *and*
    marks trusted. A hooks file sitting in a repo with no such entry is inert — so
    the capability gate must not treat it as an enabled hook. An earlier revision
    did, which is how ``doctor`` came to inspect a command Codex would never run.
    """
    cfg_path = codex_install.config_path()
    if not cfg_path.exists():
        return False
    parsed, error = _read_toml(cfg_path)
    if error is not None or parsed is None:
        return False
    projects_doc = parsed.get("projects")
    entry = projects_doc.get(str(project_root)) if isinstance(projects_doc, dict) else None
    return (
        isinstance(entry, dict)
        and entry.get("hooks") == codex_install.PROJECT_HOOKS_REL
        and entry.get("trust_level") == "trusted"
    )


def _startup_hook_executables(cwd: Path) -> list[tuple[str, str]]:
    """``(scope label, executable)`` for every **enabled, Neurobase-owned**
    startup hook, one entry per scope.

    Three filters, each closing a hole found in review:

    * **Ownership.** The command must satisfy the installer's own
      ``is_owned_command`` predicate. A foreign command in a settings file is
      untrusted input, not a hook of ours, and must never be inspected.
    * **Enablement.** A Codex project hooks file counts only when
      ``~/.codex/config.toml`` actually wires and trusts it; otherwise Codex
      never runs it.
    * **Every scope.** Claude runs matching hooks from *both* user and project
      settings, so a stale user-scope hook alongside a current project-scope one
      must be reported. Keying by agent alone hid the stale executable behind a
      green check.

    Only startup hooks are collected: they are the sole surface that spawns a
    curator with no human in the loop. ``SessionEnd``/``Stop`` never spawn one.
    """
    found: list[tuple[str, str]] = []

    def _collect(label: str, doc: object, owned: Callable[[str], bool]) -> None:
        for command in _commands_for_event(doc, "SessionStart"):
            if not owned(command):
                continue
            # Split on " hook " rather than whitespace so a shim path containing
            # a space still resolves; ownership above already proved the shape.
            head, sep, _tail = command.partition(" hook ")
            if sep and head:
                found.append((label, head))

    # Resolve the project root once and use it for *both* agents' project scope.
    # Claude project settings live at `<repo>/.claude/settings.json`, so keying
    # them off the literal cwd let an enabled repo-root hook escape the gate
    # whenever doctor ran from a subdirectory (review round 2, F2).
    project_root = projects.git_common_root(cwd) or cwd

    for user_scope in (False, True):
        scope = "user" if user_scope else "project"
        base = cwd if user_scope else project_root
        doc, error = _read_json(claude_install.settings_path(user=user_scope, cwd=base))
        if error is None:
            _collect(f"claude SessionStart ({scope})", doc, claude_install.is_owned_command)

    doc, error = _read_json(codex_install.hooks_json_path(user=True, cwd=project_root))
    if error is None:
        _collect("codex SessionStart (user)", doc, codex_install.is_owned_command)
    if _codex_project_hooks_are_wired(project_root):
        doc, error = _read_json(codex_install.hooks_json_path(user=False, cwd=project_root))
        if error is None:
            _collect("codex SessionStart (project)", doc, codex_install.is_owned_command)

    return found


# File-based enterprise-managed **policy**, per Claude Code's settings-precedence
# and memory contracts: a base settings file, a `managed-settings.d/` drop-in
# directory, and a standalone organization-wide `CLAUDE.md` — all per OS. The
# last is a separate channel from the `claudeMd` key inside the settings JSON:
# it is loaded into every session, cannot be excluded, and its content reaches
# the model as a user message after the system prompt. That makes it the most
# G5-relevant source of the three, because organization-authored instructions
# are exactly what can turn a plan call into a refusal (review F8).
# The macOS/Linux locations and the Windows `Program Files` location are the
# current ones; `C:/ProgramData/ClaudeCode` was the pre-2.1.75 path and is
# deliberately NOT probed, because finding nothing there proves nothing.
_MANAGED_SETTINGS_DIRS = (
    Path("/Library/Application Support/ClaudeCode"),  # macOS
    Path("/etc/claude-code"),  # Linux / WSL
    Path("C:/Program Files/ClaudeCode"),  # Windows (current; not ProgramData)
)

# Managed policy Claude Code can apply that a filesystem probe cannot observe.
# Named in the check's own output so a green line is never read as "no managed
# policy exists" — only as "none of the files below are present" (review F7).
_UNINSPECTABLE_MANAGED_SOURCES = (
    "server-delivered/cached settings",
    "macOS managed preferences",
    "Windows registry policy",
)


def _managed_policy_files() -> list[Path]:
    """Every file-based managed-policy source present on this machine.

    Three shapes, all of which reach a `claude -p` call: the base settings file,
    `managed-settings.d/*.json` fragments, and the organization-wide `CLAUDE.md`.
    Missing the last one meant a machine whose only managed policy was
    org-authored instructions reported green (review F8).
    """
    found: list[Path] = []
    for base in _MANAGED_SETTINGS_DIRS:
        for name in ("managed-settings.json", "CLAUDE.md"):
            candidate = base / name
            if candidate.is_file():
                found.append(candidate)
        dropin = base / "managed-settings.d"
        if dropin.is_dir():
            found.extend(sorted(p for p in dropin.glob("*.json") if p.is_file()))
    return found


def _brain_isolation_check(config: Config) -> Check:
    """Report what narrows ADR-0025's brain-call isolation on this machine.

    ADR-0025 strips the harness from every `claude -p` brain call so the curator
    call *is* what its prompt claims. Those flags select among the
    user/project/local setting sources only; enterprise-**managed** settings
    outrank all three, cannot be overridden from the command line, and may carry
    hooks, force-enabled plugins and a policy CLAUDE.md regardless.

    Two deliberate limits, both from review F7:

    **This check reports evidence, never a conclusion.** Managed policy also
    arrives by channels no filesystem probe can see (:data:`_UNINSPECTABLE_MANAGED_SOURCES`),
    so the absence of these files does *not* prove the absence of managed policy.
    The `ok` detail therefore says what was inspected and what could not be, and
    never claims "fully isolated". Anything stronger would be the same class of
    overclaim this check exists to correct.

    **It only applies to the Claude CLI backend.** ADR-0025's contract is about
    `claude -p` argv. When Codex or an API backend is resolved, the question is
    not merely green — it is irrelevant, and saying so is more useful than a
    reassuring line about a code path that will not run.

    It warns rather than errors: a managed policy is usually harmless, and
    refusing every brain call on a managed machine would take curation offline to
    prevent a hazard that may not be present (spec §10/D26 — doctor reports).
    """
    _, resolution = resolve_brain(config)
    if resolution.backend != "claude-cli":
        return _check(
            "brain isolation",
            "ok",
            f"not applicable — resolved brain is {resolution.backend}, "
            "and ADR-0025's isolation contract covers the claude CLI only",
        )

    found = _managed_policy_files()
    if not found:
        return _check(
            "brain isolation",
            "ok",
            "no managed policy file (settings, drop-in, or org CLAUDE.md) in "
            f"{', '.join(str(d) for d in _MANAGED_SETTINGS_DIRS)} "
            f"(not inspected: {'; '.join(_UNINSPECTABLE_MANAGED_SOURCES)} — "
            "see ADR-0025 §Scope)",
        )
    return _check(
        "brain isolation",
        "warn",
        f"managed Claude policy at {', '.join(str(p) for p in found)} — "
        "this outranks --setting-sources and may add hooks, plugins or "
        "organization instructions to every brain call",
        "ADR-0025's isolation is scoped to unmanaged installs. Curation still "
        "runs; treat G5's refusal fix as unverified here, and read that file "
        "before trusting a curate pass on this machine.",
    )


def _capability_checks(cwd: Path) -> list[Check]:
    """Does every enabled startup hook's install satisfy *this* build's profile?

    The requirement is always :data:`capabilities.REQUIRED_FOR_STARTUP_HOOK` from
    the diagnosing build — never the install under test, which cannot report a
    guard it has never heard of. Evidence is read statically from that install's
    packaged manifest; nothing is executed (see ``core.capabilities``).

    Distinct executables are reported separately, because two enabled hooks may
    point at different installs and only one of them may be current.
    """
    hooks = _startup_hook_executables(cwd)
    if not hooks:
        return [
            _check(
                "hook capabilities",
                "ok",
                f"no startup hooks enabled — {capabilities.PROFILE} not required",
            )
        ]

    by_executable: dict[str, list[str]] = {}
    for label, executable in hooks:
        by_executable.setdefault(executable, []).append(label)

    checks: list[Check] = []
    for executable in sorted(by_executable):
        where = ", ".join(sorted(by_executable[executable]))
        missing = capabilities.missing_for_startup_hook(capabilities.provided_by(executable))
        if not missing:
            checks.append(
                _check(
                    "hook capabilities",
                    "ok",
                    f"{executable} satisfies {capabilities.PROFILE} ({where})",
                )
            )
            continue
        checks.append(
            _check(
                "hook capabilities",
                "error",
                f"{executable} is missing {', '.join(missing)} "
                f"(required by {capabilities.PROFILE}; used by {where})",
                "That install predates the automatic-curation safety guards. "
                "Reinstall it (`uv tool install --force .`), then re-run doctor "
                "before enabling startup hooks.",
            )
        )
    return checks


def _refreshed_the_node(entry: dict) -> bool | None:
    """Did this pass leave the status node current? ``None`` ⇒ cannot tell.

    Prefers the journaled ``node_refreshed`` fact over inferring from ``status``,
    because ``status`` is overloaded: ``error`` covers both a plan failure *after*
    synthesis refreshed the node and a failed ``--dry-run`` that never attempted
    it (review round 2, F3). Records predating the field fall back to the status
    sets, and a status this build does not recognize returns ``None`` so the
    caller can say "unknown" rather than guess in either direction.
    """
    status = entry.get("status")
    # Neutral outcomes are decided by status *first*. A `noop` attempts no
    # synthesis, so a journaled `node_refreshed: false` on one would otherwise
    # read as a failed pass — the field answers "did synthesis leave the node
    # current", not "was anything wrong".
    if status in _CURATE_NEUTRAL:
        return None
    # A failed preview changes nothing at all — neither refreshes nor freezes.
    if entry.get("dry_run") is True:
        return None
    refreshed = entry.get("node_refreshed")
    if isinstance(refreshed, bool):
        return refreshed
    if status in _CURATE_SUCCESS:
        return True
    if status in _CURATE_FAILURE:
        return False
    return None


def _classify_curate_history(
    entries: list[dict],
) -> tuple[dict | None, int, dict | None, bool]:
    """``(last refreshing pass, failures since it, last failing entry, saw unknown)``.

    Neutral outcomes are skipped in both directions: they neither prove health
    nor count against it, and an earlier revision counting them as failures
    reported an idle project as broken.

    An **unrecognized** status is different from a neutral one and is reported
    separately. An older doctor genuinely cannot tell whether a newer engine's
    status denotes a synthesis failure, so treating it as healthy would restore
    the "unknown history reads as green" hole; the caller downgrades to a warning
    instead (review round 2, F4).
    """
    last_refresh: dict | None = None
    failures = 0
    last_failure: dict | None = None
    saw_unknown = False

    for entry in reversed(entries):
        verdict = _refreshed_the_node(entry)
        if verdict is True:
            last_refresh = entry
            break
        if verdict is False:
            failures += 1
            if last_failure is None:
                last_failure = entry
        elif entry.get("status") not in _CURATE_NEUTRAL and entry.get("dry_run") is not True:
            saw_unknown = True

    if last_refresh is None:
        last_refresh = next((e for e in reversed(entries) if _refreshed_the_node(e) is True), None)
    return last_refresh, failures, last_failure, saw_unknown


def _curate_health_check(root: Path, cwd: Path) -> Check:
    """Has synthesis actually succeeded lately, or is the node quietly frozen?

    Before this check, a node could be weeks stale while every user-facing
    surface reported healthy: failures land only in ``.curator-log.jsonl``, which
    nothing read. Both stale nodes on the reporting machine went unnoticed for 7
    and 11 days (2026-07-27 review, [C-10]).
    """
    try:
        handle = open_store(root, StoreMode.DOCTOR)
    except store.UnsupportedSchemaError:
        return _check("curate health", "warn", "store is unreadable")
    if handle.schema is None:
        return _check("curate health", "ok", "store is not initialized yet")
    try:
        project = handle.resolve_project(cwd)
    except (OSError, tomllib.TOMLDecodeError):
        return _check("curate health", "warn", "registry is unreadable")
    if project is None:
        return _check("curate health", "ok", "not an enabled project — nothing to curate")

    entries, state = _read_curator_log(handle.memory_dir(project) / store.CURATOR_LOG)
    if state is LogState.UNREADABLE:
        return _check(
            "curate health",
            "warn",
            f"{project!r}: curate history is unreadable — health unknown",
            "Check permissions on the project's .curator-log.jsonl.",
        )
    if state is LogState.UNPARSEABLE:
        return _check(
            "curate health",
            "warn",
            f"{project!r}: curate history holds no readable records — health unknown",
            "The log may be corrupt; `neurobase curate` will append a fresh record.",
        )
    if state is LogState.EMPTY:
        return _check(
            "curate health",
            "warn",
            f"{project!r}: curate history exists but is empty — health unknown",
            "The producer only appends whole records, so an empty log means it "
            "was truncated; `neurobase curate` will append a fresh record.",
        )
    if state is LogState.MISSING or not entries:
        return _check("curate health", "ok", f"no curate passes recorded for {project!r} yet")

    last_refresh, failures, last_failure, saw_unknown = _classify_curate_history(entries)

    if last_refresh is None and failures:
        return _check(
            "curate health",
            "error",
            f"{project!r}: no curate pass has ever refreshed the node ({failures} failed)",
            "Run `neurobase curate` and read the error it reports.",
        )
    if failures and last_refresh is not None:
        entry = last_failure or {}
        reason = str(entry.get("error") or entry.get("status") or "unknown")
        return _check(
            "curate health",
            "error" if failures >= _CURATE_FAILURE_ERROR_THRESHOLD else "warn",
            f"{project!r}: {failures} failed pass(es) since {last_refresh.get('at')}"
            f" — last: {reason[:120]}",
            "The status node stays frozen at that last refresh until this clears; "
            "run `neurobase curate` to see the failure directly.",
        )

    # Damage or an unrecognized status means the history cannot be vouched for.
    # Reported *after* real failures, which are the more specific diagnosis, but
    # never suppressed into a green check (review round 2, F4).
    if state is LogState.DAMAGED:
        return _check(
            "curate health",
            "warn",
            f"{project!r}: curate history has malformed records — health unknown",
            "Only a torn final line is expected; earlier damage means the history "
            "is incomplete. `neurobase curate` will append a fresh record.",
        )
    if saw_unknown:
        return _check(
            "curate health",
            "warn",
            f"{project!r}: curate history has outcomes this build does not recognize "
            "— health unknown",
            "The store was likely written by a newer Neurobase; upgrade this "
            "install so doctor can classify its records.",
        )
    if last_refresh is None:
        return _check("curate health", "ok", f"{project!r}: no pass has attempted synthesis yet")
    return _check("curate health", "ok", f"{project!r}: last refresh {last_refresh.get('at')}")


def _counter(value: object) -> int | None:
    """``value`` as a counter ``BrainUsage.record`` could have produced, else ``None``.

    ``type(value) is int``, **not** ``isinstance``: ``bool`` subclasses ``int``, so
    JSON ``true`` would otherwise be accepted as a count of one and ``false`` as
    zero. That let ``{"calls": 1, "models": {"main": true}}`` read as a healthy
    pass — a corrupt record laundered into an ``ok`` verdict, which is the exact
    outcome the malformed bucket exists to prevent (round 1, P2-CORRECTNESS-004).
    """
    return value if type(value) is int else None


def _model_mixture(usage: object) -> tuple[frozenset[str], list[tuple[str, int]], list[str]]:
    """``(served every call, served only some, counts a call total cannot explain)``.

    **More than one model is NOT the signal, and a check that assumes otherwise
    fires on every healthy pass.** One ``claude -p`` call reports two model ids —
    the main model plus a ``claude-haiku-4-5`` auxiliary — so ``len(models) > 1``
    is the normal case, not a fault. The first version of this rejection logic
    asserted one model per arm and would have cried wolf on all of them
    (``docs/notes/spikes/2026-08-04-stance-test.py``, ``model_verdict``).

    A model with ``0 < count < calls`` served only part of the pass. **On its own
    that does not prove the model set changed** — see ``_partials_are_proven``;
    this function only sorts the counts, and the caller decides what they license.

    A count that counter cannot produce — above the call total, or ``<= 0`` when
    ``record`` only ever increments — is reported separately as a malformed
    record rather than folded into either healthy bucket. **Every model lands in
    exactly one of the three buckets**, deliberately: an earlier shape let a
    zero or negative count match no branch at all, so a model in a corrupt
    record vanished from the report instead of being flagged.
    """
    if not isinstance(usage, dict):
        return frozenset(), [], []
    calls = _counter(usage.get("calls"))
    models = usage.get("models")
    if calls is None or calls <= 0 or not isinstance(models, dict):
        return frozenset(), [], []
    consistent: set[str] = set()
    partial: list[tuple[str, int]] = []
    impossible: list[str] = []
    for model, raw in models.items():
        name = str(model)
        count = _counter(raw)
        if count is None:
            impossible.append(name)
        elif count == calls:
            consistent.add(name)
        elif 0 < count < calls:
            partial.append((name, count))
        else:  # count > calls, or <= 0 — neither is reachable from `record`
            impossible.append(name)
    return frozenset(consistent), sorted(partial), sorted(impossible)


def _partials_are_proven(consistent: frozenset[str]) -> bool:
    """Does a partial count prove the model set *changed*, or only that telemetry
    is incomplete? The journal cannot always distinguish the two.

    ``BrainUsage.record`` increments ``calls`` for every successful envelope but
    increments a model only when that envelope carries a dict ``modelUsage`` —
    missing telemetry is explicitly tolerated there. So two calls from **one**
    model where only one envelope reported ``modelUsage`` also yield
    ``count == 1 < calls == 2``, identical in the journal to a genuine mid-pass
    change (round 1, P2-CORRECTNESS-001).

    What *does* discriminate: ``count(m) <= (envelopes that reported telemetry)
    <= calls`` for every model. So if **some** model reached ``count == calls``,
    every envelope must have reported telemetry — coverage is complete, and any
    remaining partial is then a real change. With no such model, coverage is
    unknown and the two shapes are indistinguishable, so the check must say so
    rather than assert a change (let alone a cause) the record cannot support.
    """
    return bool(consistent)


def _newest_substantive_pass(entries: list[dict]) -> dict | None:
    """The newest pass that meant to do work, whether or not it left telemetry.

    **Not "the newest pass carrying usage".** That was the round-1 bug
    (P2-CORRECTNESS-002): absent ``brain_usage`` does not mean "spent no calls".
    The codex and API brains expose no ``BrainUsage`` at all, and the ``resynth``
    branch journals no usage even though ``_synthesize`` spends a real budgeted
    call (``curator/engine.py:388``). Scanning past those for an older
    instrumented record made doctor present a pass from days ago as the current
    one — precisely wrong after a backend switch, when the stale warning is about
    a brain no longer in use.

    Neutral outcomes are skipped, and only those: ``noop`` and ``skipped-locked``
    attempt nothing, and ``dry-run`` changes nothing on disk. Everything else —
    including ``resynth``, ``error`` and ``partial`` — is a pass whose model
    evidence (or lack of it) supersedes anything older.
    """
    for entry in reversed(entries):
        if entry.get("status") in _CURATE_NEUTRAL:
            continue
        return entry
    return None


def _model_mixture_check(root: Path, cwd: Path) -> list[Check]:
    """Was the most recent substantive pass served by one consistent set of models?

    PR #17 journals ``brain_usage.models`` — per-model call counts — but nothing
    ever read them, so a pass whose model mixture changed halfway through looked
    identical to a clean one. Doctor already reads this journal for curate
    health, so this is the read side of a fact the engine was already writing:
    no new contract, no new file, nothing written.

    Emits **no check at all** when the history cannot be vouched for or there is
    nothing to vouch for: an unusable log (unreadable, unparseable, empty, or
    **damaged**), a history carrying outcomes this build cannot classify, no
    store, no project, or no substantive pass yet. All of those are states
    ``curate health`` already reports on, and a second, more confident-sounding
    line beside its "health unknown" is worse than silence (round 1,
    P2-OBSERVABILITY-003).
    """
    try:
        handle = open_store(root, StoreMode.DOCTOR)
    except store.UnsupportedSchemaError:
        return []
    if handle.schema is None:
        return []
    try:
        project = handle.resolve_project(cwd)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if project is None:
        return []

    entries, state = _read_curator_log(handle.memory_dir(project) / store.CURATOR_LOG)
    if state is not LogState.OK or not entries:
        return []
    # `saw_unknown` is curate health's own verdict on whether this history can be
    # classified at all; reusing it keeps the two lines from disagreeing about
    # the same records.
    if _classify_curate_history(entries)[3]:
        return []

    entry = _newest_substantive_pass(entries)
    if entry is None:
        return []

    at = entry.get("at")
    usage = entry.get("brain_usage")
    consistent, partial, impossible = _model_mixture(usage)

    if not isinstance(usage, dict) or not usage:
        return [
            _check(
                "model mixture",
                "ok",
                f"{project!r}: the pass at {at} recorded no model telemetry "
                "(the codex and API brains report none, and `--resynth` never does)",
            )
        ]

    calls = _counter(usage.get("calls"))
    if calls is None or calls <= 0:
        return [
            _check(
                "model mixture",
                "warn",
                f"{project!r}: the pass at {at} journaled an unusable call total "
                f"({usage.get('calls')!r}) — model mixture unknown",
                "Counts come from a counter that only ever increments, so this "
                "record's usage block cannot be trusted. The log is append-only, "
                "so the record stays; a later instrumented pass supersedes what "
                "this line reports.",
            )
        ]

    if impossible:
        return [
            _check(
                "model mixture",
                "warn",
                f"{project!r}: malformed model counts on the pass at {at} "
                f"({', '.join(impossible)} vs {calls} call(s)) — mixture unknown",
                "A model cannot serve more calls than the pass made, or fewer than "
                "one. Treat that record's usage block as unreliable. The log is "
                "append-only, so the record stays; a later instrumented pass "
                "supersedes what this line reports.",
            )
        ]

    if partial and _partials_are_proven(consistent):
        served = ", ".join(f"{model} on {count}/{calls}" for model, count in partial)
        return [
            _check(
                "model mixture",
                "warn",
                f"{project!r}: the model set changed during the pass at {at} — "
                f"{served} call(s), while {', '.join(sorted(consistent))} served all "
                f"{calls}",
                "Nothing is broken and no action is required. The pass's digests "
                "were not all produced by the same model set, so they are not "
                "comparable to each other when judging output quality — do not read "
                "a wording change across them as a change in the source material. "
                "The journal records which models served how many calls, not why "
                "the set changed.",
            )
        ]
    if partial:
        served = ", ".join(f"{model} on {count}/{calls}" for model, count in partial)
        return [
            _check(
                "model mixture",
                "warn",
                f"{project!r}: model telemetry does not cover the pass at {at} — "
                f"{served} call(s), and no model served all {calls}",
                "This is NOT proof the model changed: a successful call may report "
                "no model at all, which counts toward the call total but toward no "
                "model. With no model covering every call, an incomplete report and "
                "a genuine mid-pass change look identical in the journal, so treat "
                "that pass's digests as unattributed rather than as evidence of "
                "either.",
            )
        ]
    if not consistent:
        return [
            _check(
                "model mixture",
                "ok",
                f"{project!r}: backend reported no model ids for the pass at {at}",
            )
        ]
    return [
        _check(
            "model mixture",
            "ok",
            f"{project!r}: most recent substantive pass ({at}) served throughout by "
            f"{', '.join(sorted(consistent))}",
        )
    ]


def _denylist_scope_check(config: Config) -> Check:
    """Report `[enable].denylist` entries that do not gate the repo containing them.

    The matching rule (ADR-0026), stated exactly:
    **an entry gates a repo iff that repo's root is at or beneath the entry.**
    `projects.is_denylisted` collapses the cwd to its git root and tests *that*
    against each entry, so everything follows from the rule with no special cases.

    The consequence worth warning about: an entry *inside* repo `C` never gates
    `C`, because `C`'s root sits **above** the entry — so a user who denylists a
    sensitive subdirectory has not stopped capture of the repo it lives in
    (review I7), and a hook cannot tell them, since its stdout is protocol output
    (`hookSpecificOutput`) rather than a user channel.

    It does **not** follow that such an entry does nothing. Review I8: with repo
    `/work/mono`, entry `/work/mono/packages`, and a nested repo
    `/work/mono/packages/plugin`, the nested repo's root *is* beneath the entry, so
    `is_denylisted` returns True and it is gated. An earlier version of this check
    claimed such entries "gate NOTHING" and was simply wrong there. The wording
    below is therefore scoped to the claim that is true in every case — the
    containing repo is not gated — and says what the entry does still cover.

    Read-only. Uses `projects._resolved_config_dirs` rather than re-implementing
    the expansion: this check's only job is to predict what the matcher will do, so
    a private-but-shared helper beats a copy that can drift out of agreement with
    it.
    """
    entries = config.enable.denylist
    if not entries:
        return _check("denylist scope", "ok", "no [enable].denylist entries configured")

    inner: list[tuple[Path, Path]] = []
    for path in projects._resolved_config_dirs(entries):
        if not path.exists():
            continue  # a non-existent entry matches nothing; not this check's concern
        repo_root = projects.git_common_root(path)
        if repo_root is not None and repo_root.resolve() != path:
            inner.append((path, repo_root.resolve()))

    plural = lambda n: "y" if n == 1 else "ies"  # noqa: E731
    if not inner:
        return _check(
            "denylist scope",
            "ok",
            f"all {len(entries)} [enable].denylist entr{plural(len(entries))} "
            "name a repo root or an ancestor",
        )

    shown = "; ".join(f"{path} (inside repo {repo})" for path, repo in inner[:3])
    more = f" (+{len(inner) - 3} more)" if len(inner) > 3 else ""
    return _check(
        "denylist scope",
        "warn",
        f"{len(inner)} [enable].denylist entr{plural(len(inner))} sit inside a git repo and "
        f"do NOT gate that repo — automatic capture and injection continue in it. Only repos "
        f"whose root is at or beneath the entry are gated: {shown}{more}",
        remedy=(
            "An entry gates a repo iff that repo's root is at or beneath it (ADR-0026). "
            f"To gate the containing repo, name its root — e.g. `{inner[0][1]}` — or an "
            "ancestor. Keep the entry as-is only if you meant to gate repos nested "
            "beneath it."
        ),
    )


def collect_checks(config: Config, root: Path, cwd: Path) -> list[Check]:
    which = shutil.which
    shim = claude_install.shim_path()
    return [
        _shim_check(which),
        *_store_checks(root, cwd),
        _curate_health_check(root, cwd),
        *_model_mixture_check(root, cwd),
        _brain_check(config),
        _agent_check("claude", which),
        _agent_check("codex", which),
        _claude_hook_check(cwd, shim),
        *_codex_hook_checks(cwd, shim),
        *_capability_checks(cwd),
        _denylist_scope_check(config),
        _brain_isolation_check(config),
        _claude_mcp_check(shim),
        _codex_mcp_check(shim),
    ]


def has_errors(checks: list[Check]) -> bool:
    return any(check.status == "error" for check in checks)
