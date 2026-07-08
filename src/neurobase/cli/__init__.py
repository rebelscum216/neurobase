"""Neurobase command-line interface (Typer app — decision D12).

Phase 0: ``--help`` and ``version`` are live. The rest of the planned command
surface is declared as honest stubs so ``neurobase --help`` shows where the tool is
going; each stub exits non-zero and names the phase that will implement it. As
phases land, replace a stub with its real command.
"""

from __future__ import annotations

import difflib
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import typer

from neurobase import __version__
from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.claude import recall, scribe
from neurobase.adapters.codex import install as codex_install
from neurobase.adapters.codex import recall as codex_recall
from neurobase.adapters.codex import scribe as codex_scribe
from neurobase.brain import resolve_brain
from neurobase.cli import diagnostics
from neurobase.core import backups, projects, store
from neurobase.core.config import load_config
from neurobase.curator import curate as run_curate
from neurobase.curator import is_stale, read_fact_count_trend

app = typer.Typer(
    name="neurobase",
    help="Local-first, cross-agent memory layer for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed Neurobase version."""
    typer.echo(__version__)


def _check_store_schema(root: Path) -> None:
    """Refuse to operate on a store whose schema is newer than this binary
    supports (spec §10/D11) — called before any registry/memory read or
    write so a newer-schema store is never partially mutated."""
    try:
        store.ensure_store_metadata(root)
    except store.UnsupportedSchemaError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def enable(
    root: str | None = typer.Option(
        None, "--root", help="Override the store root (default: config/env/~/neurobase)."
    ),
    slug: str | None = typer.Option(
        None, "--slug", help="Explicit project slug (skips the collision error)."
    ),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Register the current repo as a project and create its memory tree."""
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    _check_store_schema(resolved_root)  # before registry.toml is touched
    try:
        project_slug = projects.register_project(resolved_root, resolved_cwd, slug=slug)
    except (projects.ProjectSlugCollisionError, store.InvalidSlugError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    mem = store.ensure_tree(project_slug, resolved_root)
    typer.echo(f"Enabled project '{project_slug}' at {mem}")


@app.command()
def status(
    root: str | None = typer.Option(None, "--root", help="Override the store root."),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Show projects, raw/curated counts, nodes, and fact-count trend."""
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    project_slug = projects.resolve_project(resolved_root, resolved_cwd)
    if project_slug is None:
        typer.echo("Not an enabled project (no registered root matches this directory).")
        raise typer.Exit(code=1)
    _check_store_schema(resolved_root)  # before any memory read

    all_raw = store.list_raw(resolved_root, project_slug, unconsumed_only=False)
    unconsumed_count = sum(1 for d in all_raw if not d.get("consumed"))
    consumed_count = sum(1 for d in all_raw if d.get("consumed"))

    mem = store.memory_dir(project_slug, resolved_root)
    active_facts = 0
    curated_dir = mem / "curated"
    if curated_dir.exists():
        for path in curated_dir.glob("*.md"):
            try:
                doc = store.read_doc(path)
            except ValueError:
                continue
            if doc.get("status") == "active":
                active_facts += 1
    nodes_dir = mem / "nodes"
    node_count = len(list(nodes_dir.glob("*.md"))) if nodes_dir.exists() else 0

    typer.echo(f"Project: {project_slug}")
    typer.echo(f"Raw captures: {unconsumed_count} unconsumed, {consumed_count} consumed")
    typer.echo(f"Active curated facts: {active_facts}")
    typer.echo(f"Nodes: {node_count}")

    trend = read_fact_count_trend(resolved_root, project_slug)
    if trend:
        typer.echo(f"Fact-count trend (last {len(trend)} passes): {' → '.join(map(str, trend))}")


@app.command()
def curate(
    root: str | None = typer.Option(None, "--root", help="Override the store root."),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
    if_stale: bool = typer.Option(
        False, "--if-stale", help="Only run if unconsumed raw is older than the staleness window."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan the curator would apply; change nothing."
    ),
    resynth: bool = typer.Option(
        False, "--resynth", help="Regenerate the node + index from current facts; no new raw."
    ),
) -> None:
    """Fold unconsumed raw captures into the curated fact set (spec §2)."""
    config = load_config()
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    project_slug = projects.resolve_project(resolved_root, resolved_cwd)
    if project_slug is None:
        typer.echo("Not an enabled project (no registered root matches this directory).")
        raise typer.Exit(code=1)
    _check_store_schema(resolved_root)

    checking_staleness = if_stale and not resynth
    if checking_staleness and not is_stale(resolved_root, project_slug, config.curate.stale_hours):
        typer.echo("Not stale — nothing to curate.")
        return

    brain, resolution = resolve_brain(config)
    if brain is None:
        typer.secho(
            f"No brain backend available ({resolution.reason}); run `neurobase doctor`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    summary = run_curate(
        resolved_root,
        project_slug,
        brain,
        dry_run=dry_run,
        resynth=resynth,
        tombstone_grace_days=config.curate.tombstone_grace_days,
    )

    if dry_run:
        typer.echo(json.dumps(summary.get("plan", {}), indent=2, ensure_ascii=False))
        return
    typer.echo(json.dumps({k: v for k, v in summary.items() if k != "plan"}, ensure_ascii=False))
    if summary.get("status") == "error":
        raise typer.Exit(code=1)


@app.command()
def doctor(
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Diagnose the install: shim, store, brain, agents, hooks, and trust."""
    config = load_config()
    resolved_root = store.resolve_root(None)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    checks = diagnostics.collect_checks(config, resolved_root, resolved_cwd)
    for check in checks:
        symbol = {"ok": "✓", "warn": "!", "error": "✗"}[check.status]
        color = {
            "ok": typer.colors.GREEN,
            "warn": typer.colors.YELLOW,
            "error": typer.colors.RED,
        }[check.status]
        typer.secho(f"{symbol} {check.name}: {check.detail}", fg=color)
        if check.remedy:
            typer.echo(f"  remedy: {check.remedy}")
    if diagnostics.has_errors(checks):
        raise typer.Exit(code=1)


@app.command()
def init(
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Which agent to install hooks for (claude | codex). Omit for guided setup.",
    ),
    user: bool = typer.Option(
        False, "--user", help="Install into the agent's user config (default: project-local)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Install Neurobase's hooks into an agent's config (consent-first, spec §7).

    Shows the exact config diff, asks for consent, backs up the original(s), and
    writes idempotently — only hook entries Neurobase created are ever touched.
    Supports ``--agent claude`` and ``--agent codex``.
    """
    resolved_root = store.resolve_root(None)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    if agent is None:
        _init_guided(resolved_root, resolved_cwd, user=user, yes=yes)
    elif agent == "claude":
        _init_claude(resolved_root, resolved_cwd, user=user, yes=yes)
    elif agent == "codex":
        _init_codex(resolved_root, resolved_cwd, user=user, yes=yes)
    else:
        typer.secho(
            f"unsupported agent {agent!r} — choose 'claude' or 'codex'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


def _init_guided(resolved_root: Path, resolved_cwd: Path, *, user: bool, yes: bool) -> None:
    """Unified Phase-6 setup flow: choose root, enable repo, install detected
    agents through the existing per-agent consent paths."""
    if not yes:
        chosen_root = typer.prompt("Store root", default=str(resolved_root))
        resolved_root = store.resolve_root(chosen_root)

    enable_repo = yes or typer.confirm(f"Enable this repo in Neurobase ({resolved_cwd})?")
    if enable_repo:
        try:
            project_slug = projects.register_project(resolved_root, resolved_cwd)
            mem = store.ensure_tree(project_slug, resolved_root)
        except (projects.ProjectSlugCollisionError, store.InvalidSlugError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Enabled project '{project_slug}' at {mem}")

    detected = [name for name in ("claude", "codex") if shutil.which(name) is not None]
    if not detected:
        typer.secho(
            "No supported agents found on PATH; install Claude/Codex or run "
            "`neurobase init --agent <agent>` explicitly.",
            fg=typer.colors.YELLOW,
        )
        return

    selected: list[str] = []
    for name in detected:
        if yes or typer.confirm(f"Install Neurobase hooks for {name}?"):
            selected.append(name)
    if not selected:
        typer.echo("No agent hooks selected.")
        return

    for name in selected:
        if name == "claude":
            _init_claude(resolved_root, resolved_cwd, user=user, yes=yes)
        elif name == "codex":
            _init_codex(resolved_root, resolved_cwd, user=user, yes=yes)


def _init_claude(resolved_root: Path, resolved_cwd: Path, *, user: bool, yes: bool) -> None:
    config = load_config()
    path = claude_install.settings_path(user=user, cwd=resolved_cwd)

    try:
        existing = claude_install.load_settings(path)
    except claude_install.SettingsParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    shim = claude_install.shim_path()
    new_settings = claude_install.build_settings(existing, shim, config.inject.sources)

    before = claude_install.render(existing) if path.exists() else ""
    after = claude_install.render(new_settings)
    if before == after:
        typer.echo(f"Claude hooks already up to date in {path}.")
        return

    typer.echo(_unified_diff(before, after, path))

    if not yes and not typer.confirm(f"Apply these changes to {path}?"):
        typer.echo("Aborted — no changes made.")
        return

    backup_dir = backups.backup_files(resolved_root, [path])
    if backup_dir is not None:
        typer.echo(f"Backed up {path} to {backup_dir}")
    claude_install.write_settings(path, new_settings)
    typer.secho(
        f"Installed Claude hooks in {path}. Takes effect next session.",
        fg=typer.colors.GREEN,
    )
    typer.echo("Run `neurobase enable` in each repo you want captured (opt-in).")


def _init_codex(resolved_root: Path, resolved_cwd: Path, *, user: bool, yes: bool) -> None:
    """Install Codex hooks (spec §7). Writes a ``hooks.json`` and, for project
    scope, surgically registers the project in ``~/.codex/config.toml`` so the
    hook is discovered and trusted."""
    project_root = resolved_cwd if user else projects.git_common_root(resolved_cwd) or resolved_cwd
    hooks_path = codex_install.hooks_json_path(user=user, cwd=project_root)
    try:
        existing_hooks = codex_install.load_hooks(hooks_path)
    except codex_install.HooksParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    shim = codex_install.shim_path()
    new_hooks = codex_install.build_hooks(existing_hooks, shim)
    hooks_before = codex_install.render_hooks(existing_hooks) if hooks_path.exists() else ""
    hooks_after = codex_install.render_hooks(new_hooks)
    hooks_changed = hooks_before != hooks_after

    # config.toml is edited only for project scope (user hooks.json is
    # auto-discovered and needs no [projects.*] table).
    cfg_path = codex_install.config_path()
    project_key = str(project_root)
    cfg_before = ""
    cfg_after = ""
    cfg_changed = False
    if not user:
        try:
            cfg_before = codex_install.load_config_text(cfg_path)
            cfg_after = codex_install.merge_config(cfg_before, project_key)
        except codex_install.ConfigParseError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        cfg_changed = cfg_before != cfg_after

    if not hooks_changed and not cfg_changed:
        typer.echo("Codex hooks already up to date.")
        return

    if hooks_changed:
        typer.echo(_unified_diff(hooks_before, hooks_after, hooks_path))
    if cfg_changed:
        typer.echo(_unified_diff(cfg_before, cfg_after, cfg_path))

    targets = [hooks_path] + ([cfg_path] if not user else [])
    target_desc = ", ".join(str(t) for t in targets)
    if not yes and not typer.confirm(f"Apply these changes to {target_desc}?"):
        typer.echo("Aborted — no changes made.")
        return

    backup_dir = backups.backup_files(resolved_root, targets)
    if backup_dir is not None:
        typer.echo(f"Backed up existing config to {backup_dir}")
    if hooks_changed:
        codex_install.write_hooks(hooks_path, new_hooks)
    if cfg_changed:
        codex_install.write_config(cfg_path, cfg_after)

    typer.secho(
        f"Installed Codex hooks in {hooks_path}. Takes effect next session.",
        fg=typer.colors.GREEN,
    )
    typer.secho(
        "IMPORTANT — approve the hook in Codex before it takes effect: editing "
        "hooks.json invalidates its trust hash, so Codex re-prompts to approve "
        "the hook on next launch. It will not fire until you approve it there.",
        fg=typer.colors.YELLOW,
    )
    typer.echo("Run `neurobase enable` in each repo you want captured (opt-in).")


def _unified_diff(before: str, after: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
        )
    )


_PendingWrite = tuple[Path, str, str, Callable[[], None]]


@app.command()
def uninstall(
    agent: str = typer.Option(
        "all", "--agent", help="Which agent to uninstall hooks for (claude | codex | all)."
    ),
    user: bool = typer.Option(
        False, "--user", help="Uninstall from the agent's user config (default: project-local)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    purge_store: bool = typer.Option(
        False, "--purge-store", help="Also delete the Neurobase store root."
    ),
    restore_backup: str | None = typer.Option(
        None,
        "--restore-backup",
        help="Restore a specific backup timestamp wholesale instead of surgical uninstall.",
    ),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Remove Neurobase-owned hooks. The store is left intact unless
    ``--purge-store`` is passed explicitly."""
    resolved_root = store.resolve_root(None)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()

    if restore_backup is not None:
        if purge_store:
            typer.secho(
                "--restore-backup cannot be combined with --purge-store.", fg=typer.colors.RED
            )
            raise typer.Exit(code=1)
        if not yes and not typer.confirm(
            f"Restore backup {restore_backup!r} from {resolved_root}/backups?"
        ):
            typer.echo("Aborted — no changes made.")
            return
        try:
            restored = backups.restore_backup(resolved_root, restore_backup)
        except backups.BackupRestoreError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        for path in restored:
            typer.echo(f"Restored {path}")
        return

    if agent not in {"claude", "codex", "all"}:
        typer.secho(
            f"unsupported agent {agent!r} — choose 'claude', 'codex', or 'all'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    writes: list[_PendingWrite] = []
    try:
        if agent in {"claude", "all"}:
            writes.extend(_uninstall_claude(resolved_cwd, user=user))
        if agent in {"codex", "all"}:
            writes.extend(_uninstall_codex(resolved_cwd, user=user))
    except (
        claude_install.SettingsParseError,
        codex_install.HooksParseError,
        codex_install.ConfigParseError,
    ) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if not writes and not purge_store:
        typer.echo("No Neurobase hooks found.")
        return

    for path, before, after, _writer in writes:
        typer.echo(_unified_diff(before, after, path))

    actions = [str(path) for path, *_rest in writes]
    if purge_store:
        actions.append(f"DELETE store {resolved_root}")
    target_desc = ", ".join(actions)
    if not yes and not typer.confirm(f"Apply these uninstall changes to {target_desc}?"):
        typer.echo("Aborted — no changes made.")
        return

    backup_dir = backups.backup_files(resolved_root, [path for path, *_rest in writes])
    if backup_dir is not None:
        typer.echo(f"Backed up existing config to {backup_dir}")
    for _path, _before, _after, writer in writes:
        writer()
    if purge_store and resolved_root.exists():
        shutil.rmtree(resolved_root)
        typer.echo(f"Deleted store {resolved_root}")
    typer.secho("Uninstalled Neurobase-owned hooks.", fg=typer.colors.GREEN)


def _uninstall_claude(resolved_cwd: Path, *, user: bool) -> list[_PendingWrite]:
    path = claude_install.settings_path(user=user, cwd=resolved_cwd)
    if not path.exists():
        return []
    existing = claude_install.load_settings(path)
    new_settings = claude_install.remove_owned_settings(existing)
    before = claude_install.render(existing)
    after = claude_install.render(new_settings)
    if before == after:
        return []
    return [(path, before, after, lambda: claude_install.write_settings(path, new_settings))]


def _uninstall_codex(resolved_cwd: Path, *, user: bool) -> list[_PendingWrite]:
    project_root = resolved_cwd if user else projects.git_common_root(resolved_cwd) or resolved_cwd
    writes: list[_PendingWrite] = []

    hooks_path = codex_install.hooks_json_path(user=user, cwd=project_root)
    if hooks_path.exists():
        existing_hooks = codex_install.load_hooks(hooks_path)
        new_hooks = codex_install.remove_owned_hooks(existing_hooks)
        hooks_before = codex_install.render_hooks(existing_hooks)
        hooks_after = codex_install.render_hooks(new_hooks)
        if hooks_before != hooks_after:
            writes.append(
                (
                    hooks_path,
                    hooks_before,
                    hooks_after,
                    lambda: codex_install.write_hooks(hooks_path, new_hooks),
                )
            )

    if not user:
        cfg_path = codex_install.config_path()
        if cfg_path.exists():
            cfg_before = codex_install.load_config_text(cfg_path)
            cfg_after = codex_install.remove_project_hooks_config(cfg_before, str(project_root))
            if cfg_before != cfg_after:
                writes.append(
                    (
                        cfg_path,
                        cfg_before,
                        cfg_after,
                        lambda: codex_install.write_config(cfg_path, cfg_after),
                    )
                )
    return writes


# --- Planned command surface (stubs until each command's phase lands) ---------

_PLANNED: list[tuple[str, int, str]] = [
    ("recall", 4, "Print the memory that would be injected for a project."),
    ("mcp", 7, "Run the MCP server exposing memory tools to any client."),
    ("recommend", 8, "Review skill/rule proposals mined from your history."),
    ("seed", 8, "Import existing notes / Claude auto-memory as curated facts."),
]


def _make_stub(name: str, phase: int, summary: str) -> Callable[[], None]:
    def _cmd() -> None:
        typer.secho(
            f"`neurobase {name}` is not implemented yet (planned for Phase {phase}).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    _cmd.__doc__ = f"{summary}  [not implemented — Phase {phase}]"
    return _cmd


for _name, _phase, _summary in _PLANNED:
    app.command(name=_name)(_make_stub(_name, _phase, _summary))


def _read_stdin_json() -> dict[str, object]:
    """Read the hook's stdin JSON payload. Any problem ⇒ empty dict (fail-safe;
    never blocks on an interactive terminal)."""
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _hook_claude_session_end(
    payload: dict[str, object],
    transcript: str | None,
    cwd: str | None,
    root: str | None,
    reason: str | None,
) -> None:
    transcript_path = transcript or payload.get("transcript_path")
    if not transcript_path:
        return
    resolved_root = store.resolve_root(root)
    scribe.scribe(
        resolved_root,
        transcript_path=Path(str(transcript_path)),
        cwd=cwd or str(payload.get("cwd") or ""),
        reason=reason or str(payload.get("reason") or "other"),
        session_id=str(payload.get("session_id") or ""),
    )


def _hook_claude_session_start(
    payload: dict[str, object], cwd: str | None, root: str | None
) -> None:
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd or str(payload.get("cwd") or ".")).expanduser()
    output = recall.emit(resolved_root, resolved_cwd)
    if output:
        typer.echo(output)
    recall.spawn_curate_if_stale(resolved_root, resolved_cwd)


def _hook_codex_session_start(
    payload: dict[str, object], cwd: str | None, root: str | None
) -> None:
    """Codex SessionStart inject — identical to Claude's (ADR-0005)."""
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd or str(payload.get("cwd") or ".")).expanduser()
    output = codex_recall.emit(resolved_root, resolved_cwd)
    if output:
        typer.echo(output)
    codex_recall.spawn_curate_if_stale(resolved_root, resolved_cwd)


def _hook_codex_stop(
    payload: dict[str, object], rollout: str | None, cwd: str | None, root: str | None
) -> None:
    """Codex per-turn capture (spec §5). The rollout path arrives as the hook
    payload's ``transcript_path`` (or ``--rollout`` for testing); if absent, it
    is discovered by mtime + session-id cross-check."""
    resolved_root = store.resolve_root(root)
    resolved_cwd = cwd or str(payload.get("cwd") or "")
    session_id = str(payload.get("session_id") or "")
    rollout_path = rollout or payload.get("transcript_path")
    resolved_rollout = (
        Path(str(rollout_path))
        if rollout_path
        else codex_scribe.discover_rollout(session_id=session_id or None)
    )
    if resolved_rollout is None:
        return
    codex_scribe.scribe(
        resolved_root,
        rollout_path=resolved_rollout,
        cwd=resolved_cwd,
        session_id=session_id,
    )


def _hook_codex_notify(argv_payload: dict[str, object], root: str | None) -> None:
    """Codex ``notify`` fallback (spec §5/§11.4): payload is argv JSON with no
    rollout path, so the rollout is always discovered (session id = thread id)."""
    resolved_root = store.resolve_root(root)
    session_id = str(argv_payload.get("thread-id") or "")
    resolved_rollout = codex_scribe.discover_rollout(session_id=session_id or None)
    if resolved_rollout is None:
        return
    codex_scribe.scribe(
        resolved_root,
        rollout_path=resolved_rollout,
        cwd=str(argv_payload.get("cwd") or ""),
        session_id=session_id,
    )


_HOOK_FLAGS = ("--transcript", "--rollout", "--cwd", "--root", "--reason")


def _parse_hook_args(args: list[str]) -> tuple[str | None, str | None, dict[str, str]]:
    """Manual, never-failing parse of ``hook`` args. Positionals → agent/event;
    ``--flag value`` / ``--flag=value`` (known flags only) → opts; anything else
    (extra positionals, unknown or value-less flags) is ignored. This is the
    fast path (D12): hook safety must not depend on Typer/Click parsing, which
    can exit 2 on a malformed argv *before* the body runs."""
    positionals: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--"):
            if "=" in tok:
                key, _, val = tok.partition("=")
                if key in _HOOK_FLAGS:
                    opts[key[2:]] = val
            elif tok in _HOOK_FLAGS and i + 1 < len(args) and not args[i + 1].startswith("--"):
                opts[tok[2:]] = args[i + 1]
                i += 1
            # unknown flag, or known flag with no value: ignore (never crash)
        else:
            positionals.append(tok)
        i += 1
    agent = positionals[0] if positionals else None
    event = positionals[1] if len(positionals) > 1 else None
    return agent, event, opts


def _argv_json_payload(args: list[str]) -> dict[str, object]:
    """Codex ``notify`` delivers its JSON as argv (§11.4), not stdin. Return the
    first ``{``-prefixed arg that parses as a JSON object, else ``{}``."""
    for tok in args:
        if tok.startswith("{"):
            try:
                data = json.loads(tok)
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def run_hook(args: list[str]) -> None:
    """Dispatch a hook invocation. Spec §4/§5: **always returns cleanly** —
    never raises, never exits non-zero, never wedges an agent's session start
    or teardown. On any error it captures nothing / injects nothing."""
    try:
        agent, event, opts = _parse_hook_args(args)
        payload = _read_stdin_json()
        if agent == "claude" and event == "session-end":
            _hook_claude_session_end(
                payload,
                opts.get("transcript"),
                opts.get("cwd"),
                opts.get("root"),
                opts.get("reason"),
            )
        elif agent == "claude" and event == "session-start":
            _hook_claude_session_start(payload, opts.get("cwd"), opts.get("root"))
        elif agent == "codex" and event == "session-start":
            _hook_codex_session_start(payload, opts.get("cwd"), opts.get("root"))
        elif agent == "codex" and event == "stop":
            _hook_codex_stop(payload, opts.get("rollout"), opts.get("cwd"), opts.get("root"))
        elif agent == "codex" and event == "notify":
            _hook_codex_notify(_argv_json_payload(args), opts.get("root"))
        # any unknown agent/event: no-op.
    except Exception:  # noqa: BLE001 - fail-safe: never wedge teardown
        pass


@app.command(
    name="hook",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def hook(ctx: typer.Context) -> None:
    """Deterministic capture/inject entry point invoked by agent hooks.

    Spec §4/§5: **always exits 0** — never wedge an agent's session start or
    teardown. Reads the hook payload as JSON on stdin; ``--transcript`` /
    ``--cwd`` / ``--root`` / ``--reason`` override for testing. All args are
    parsed manually (``run_hook``) so a malformed argv can't trip a Typer
    parse-error exit before dispatch; ``main()`` routes real ``neurobase hook``
    invocations here without paying Typer's startup at all (D12 fast path).
    """
    run_hook(ctx.args)


def main() -> None:
    """Console-script entry point. ``neurobase hook …`` takes a Typer-light
    fast path that **cannot exit non-zero** (spec §4/§5); everything else goes
    through the normal Typer app."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        run_hook(sys.argv[2:])
        return
    app()
