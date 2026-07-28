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

#: A pass that meant to produce a node and did not. `partial` belongs here: raws
#: are consumed and facts upserted, then synthesis dies — which is precisely the
#: shape that leaves a node frozen while curated facts move on.
_CURATE_FAILURE = frozenset({"error", "partial"})


class LogState(Enum):
    """Why a curator-log read produced the entries it did.

    Kept separate from the entries themselves because *no history* and
    *unreadable history* are different diagnoses that an earlier revision
    collapsed into one cheerful "no curate passes recorded". A permissions
    failure could therefore restore the all-green blindness this check exists to
    prevent.
    """

    MISSING = "missing"
    UNREADABLE = "unreadable"
    UNPARSEABLE = "unparseable"
    OK = "ok"


def _read_curator_log(path: Path) -> tuple[list[dict], LogState]:
    """Curator-log entries plus the state of the read.

    A torn trailing line stays fail-soft — the writer appends, so the last line
    can be mid-write — but a log that exists, holds bytes, and yields no records
    at all is reported as unparseable rather than as an empty history.
    """
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8") as fh:
            lines = [line.strip() for line in fh]
    except FileNotFoundError:
        return [], LogState.MISSING
    except OSError:
        return [], LogState.UNREADABLE

    populated = [line for line in lines if line]
    entries: list[dict] = []
    for line in populated:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    if populated and not entries:
        return [], LogState.UNPARSEABLE
    if not populated:
        return [], LogState.MISSING
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
    # Sanctioned raw-root call #1 of doctor's two (the other is resolve_project in
    # _project_check): the store.toml path is the report *label*, needed even when the
    # store is corrupt and no handle can open. Allow-listed by (file, name) in the
    # step-5 chokepoint guard and named in spec §10; see _project_check's docstring.
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

    The no-handle branch is one of doctor's **two** sanctioned raw-root reads (the
    other is ``store.store_toml_path`` in ``_store_checks``), allow-listed in the
    step-5 chokepoint guard (``scripts/check_store_chokepoint.py``) by (file, name).
    It is legitimate here because ``registry.toml`` is a separate concern from the
    store-schema chokepoint (ADR-0015 registry carve-out, F1), so resolving a project
    without a validated handle must still work when ``store.toml`` is corrupt. The
    guard enforces the store/registry **accessor + metadata-literal** invariant in
    ``src/`` (spec §10); it is not an exhaustive raw-root detector."""
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

    for user_scope in (False, True):
        scope = "user" if user_scope else "project"
        doc, error = _read_json(claude_install.settings_path(user=user_scope, cwd=cwd))
        if error is None:
            _collect(f"claude SessionStart ({scope})", doc, claude_install.is_owned_command)

    project_root = projects.git_common_root(cwd) or cwd
    doc, error = _read_json(codex_install.hooks_json_path(user=True, cwd=project_root))
    if error is None:
        _collect("codex SessionStart (user)", doc, codex_install.is_owned_command)
    if _codex_project_hooks_are_wired(project_root):
        doc, error = _read_json(codex_install.hooks_json_path(user=False, cwd=project_root))
        if error is None:
            _collect("codex SessionStart (project)", doc, codex_install.is_owned_command)

    return found


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


def _classify_curate_history(entries: list[dict]) -> tuple[dict | None, int, dict | None]:
    """``(last success, failures since it, last failing entry)``.

    Neutral outcomes (``noop``, ``skipped-locked``, ``dry-run``) are skipped in
    both directions: they neither prove health nor count against it, and an
    earlier revision counting them as failures reported an idle project as
    broken. Unknown statuses from a future engine are treated as neutral rather
    than as failures — a newer producer must not make an older doctor cry wolf.
    """
    last_success = next((e for e in reversed(entries) if e.get("status") in _CURATE_SUCCESS), None)
    failures = 0
    last_failure: dict | None = None
    for entry in reversed(entries):
        status = entry.get("status")
        if status in _CURATE_SUCCESS:
            break
        if status in _CURATE_FAILURE:
            failures += 1
            if last_failure is None:
                last_failure = entry
    return last_success, failures, last_failure


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
    if state is LogState.MISSING or not entries:
        return _check("curate health", "ok", f"no curate passes recorded for {project!r} yet")

    last_success, failures, last_failure = _classify_curate_history(entries)

    if last_success is None and failures:
        return _check(
            "curate health",
            "error",
            f"{project!r}: no curate pass has ever succeeded ({failures} failed)",
            "Run `neurobase curate` and read the error it reports.",
        )
    if last_success is None:
        return _check(
            "curate health",
            "ok",
            f"{project!r}: no pass has attempted synthesis yet",
        )
    if failures:
        entry = last_failure or {}
        reason = str(entry.get("error") or entry.get("status") or "unknown")
        return _check(
            "curate health",
            "error" if failures >= _CURATE_FAILURE_ERROR_THRESHOLD else "warn",
            f"{project!r}: {failures} failed pass(es) since {last_success.get('at')}"
            f" — last: {reason[:120]}",
            "The status node stays frozen at that last success until this clears; "
            "run `neurobase curate` to see the failure directly.",
        )
    return _check("curate health", "ok", f"{project!r}: last success {last_success.get('at')}")


def collect_checks(config: Config, root: Path, cwd: Path) -> list[Check]:
    which = shutil.which
    shim = claude_install.shim_path()
    return [
        _shim_check(which),
        *_store_checks(root, cwd),
        _curate_health_check(root, cwd),
        _brain_check(config),
        _agent_check("claude", which),
        _agent_check("codex", which),
        _claude_hook_check(cwd, shim),
        *_codex_hook_checks(cwd, shim),
        *_capability_checks(cwd),
        _claude_mcp_check(shim),
        _codex_mcp_check(shim),
    ]


def has_errors(checks: list[Check]) -> bool:
    return any(check.status == "error" for check in checks)
