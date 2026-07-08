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
from pathlib import Path

from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.brain import resolve_brain
from neurobase.brain import select as brain_select
from neurobase.core import projects, store
from neurobase.core.config import Config

Status = str


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
    meta = store.store_toml_path(root)
    if not meta.exists():
        checks.append(
            _check(
                "store",
                "warn",
                f"{root} is not initialized yet",
                "Run `neurobase enable` or interactive `neurobase init`.",
            )
        )
    else:
        try:
            data = tomllib.loads(meta.read_text(encoding="utf-8"))
            schema = data.get("schema")
            if not isinstance(schema, int) or schema > store.STORE_SCHEMA_VERSION:
                checks.append(
                    _check(
                        "store",
                        "error",
                        f"{meta}: unsupported schema {schema!r}",
                        "Upgrade neurobase-cli before operating on this store.",
                    )
                )
            else:
                checks.append(_check("store", "ok", f"{root} (schema {schema})"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            checks.append(_check("store", "error", f"{meta} is unreadable or invalid: {exc}"))

    try:
        slug = projects.resolve_project(root, cwd)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        checks.append(_check("project", "error", f"registry is unreadable or invalid: {exc}"))
    else:
        if slug is None:
            checks.append(
                _check(
                    "project",
                    "warn",
                    f"{cwd} is not enabled",
                    "Run `neurobase enable` in this repo.",
                )
            )
        else:
            checks.append(_check("project", "ok", f"{cwd} resolves to {slug!r}"))
    return checks


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


def _codex_trust_check(parsed: dict | None, hooks_rel: str) -> Check:
    state = _codex_hooks_state(parsed) if parsed is not None else {}
    if _has_trusted_hash_for(state, hooks_rel, {"session_start", "stop"}):
        return _check("codex trust", "ok", f"trusted_hash present for {hooks_rel}")
    return _check(
        "codex trust",
        "warn",
        f"no trusted_hash recorded for {hooks_rel}",
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
            checks.append(_codex_trust_check(parsed, codex_install.PROJECT_HOOKS_REL))
        return checks

    hook_check, hooks_ok = _codex_hooks_file_check(user_hooks_path, shim, "user")
    if hooks_ok or hook_check.status == "error":
        checks.insert(0, hook_check)
        if hooks_ok:
            checks = [check for check in checks if check.name != "codex config"]
            checks.append(_check("codex config", "ok", "user hooks are auto-discovered"))
        if hooks_ok:
            checks.append(_codex_trust_check(parsed, str(user_hooks_path)))
        return checks

    project_hook_check, project_hooks_ok = _codex_hooks_file_check(
        project_hooks_path, shim, "project"
    )
    checks.insert(0, project_hook_check)
    if project_hooks_ok:
        checks.append(_codex_trust_check(parsed, codex_install.PROJECT_HOOKS_REL))
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
            f"{path} registers neurobase at a different command",
            "Run `neurobase init --agent claude` to update.",
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
            f"{path} registers neurobase at a different command",
            "Run `neurobase init --agent codex` to update.",
        )
    return _check(
        "codex mcp",
        "warn",
        f"{path} has no neurobase MCP server",
        "Run `neurobase init --agent codex`.",
    )


def collect_checks(config: Config, root: Path, cwd: Path) -> list[Check]:
    which = shutil.which
    shim = claude_install.shim_path()
    return [
        _shim_check(which),
        *_store_checks(root, cwd),
        _brain_check(config),
        _agent_check("claude", which),
        _agent_check("codex", which),
        _claude_hook_check(cwd, shim),
        *_codex_hook_checks(cwd, shim),
        _claude_mcp_check(shim),
        _codex_mcp_check(shim),
    ]


def has_errors(checks: list[Check]) -> bool:
    return any(check.status == "error" for check in checks)
