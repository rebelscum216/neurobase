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

import click
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
from neurobase.recommender import corpus as recommend_corpus
from neurobase.recommender import miner, proposals, ranker
from neurobase.recommender import seed as seed_import

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
def seed(
    from_dir: str | None = typer.Option(
        None,
        "--from-dir",
        help="Recursively import markdown files from this directory as curated facts.",
    ),
    from_claude_memory: bool = typer.Option(
        False,
        "--from-claude-memory",
        help="Import Claude Code's per-project auto-memory directory as curated facts.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Target this registered project slug instead of the one resolved from cwd "
        "(also widens --from-claude-memory's scope to that project).",
    ),
    all_projects: bool = typer.Option(
        False,
        "--all-projects",
        help="With --from-claude-memory, import every registered project's auto-memory "
        "directory (a project with none is silently skipped).",
    ),
    root: str | None = typer.Option(None, "--root", help="Override the store root."),
    cwd: str | None = typer.Option(None, "--cwd", hidden=True, help="Override cwd (testing)."),
) -> None:
    """Import existing notes / Claude auto-memory as curated facts (spec §12.3).

    Requires an explicit ``--from-dir <path>`` and/or ``--from-claude-memory``
    — never crawls a directory the user did not name. Redacts every imported
    body before it touches ``curated/`` and is idempotent on rerun (dedupe by
    slug + source digest).
    """
    if from_dir is None and not from_claude_memory:
        typer.secho(
            "`neurobase seed` requires --from-dir <path> and/or --from-claude-memory.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if project is not None and all_projects:
        typer.secho(
            "--project and --all-projects cannot be combined.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1)
    if all_projects and not from_claude_memory:
        typer.secho(
            "--all-projects only applies to --from-claude-memory.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if from_dir is not None:
        # Validate the --from-dir target up front, before project-scope
        # resolution below — otherwise an unresolvable cwd masks a bad
        # --from-dir target with a less relevant error message when both are
        # wrong at once.
        resolved_from_dir_check = Path(from_dir).expanduser().resolve()
        if not resolved_from_dir_check.is_dir():
            typer.secho(
                f"{resolved_from_dir_check} is not a directory", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=1)

    config = load_config()
    resolved_root = store.resolve_root(root)
    resolved_cwd = Path(cwd).resolve() if cwd else Path.cwd()
    _check_store_schema(resolved_root)
    registry = projects.load_registry(resolved_root)

    total = seed_import.SeedResult()

    # Both --from-dir and a non---all-projects --from-claude-memory act on
    # exactly one project: --project names it explicitly, else it's resolved
    # from launch cwd (a hard CLI error if that cwd is unresolvable — spec
    # §12.3's Invariants section).
    single_slug: str | None = None
    single_project_root: Path | None = None
    if from_dir is not None or (from_claude_memory and not all_projects):
        if project is not None:
            roots = registry.get(project)
            if not roots:
                typer.secho(
                    f"unknown project {project!r} (not in the registry).",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            single_slug = project
            single_project_root = Path(roots[0])
        else:
            single_slug = projects.resolve_project(resolved_root, resolved_cwd)
            if single_slug is None:
                typer.secho(
                    "Cannot resolve a project from this cwd — run `neurobase seed` from "
                    "inside a registered project, or pass --project <slug>.",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            roots = registry.get(single_slug) or []
            single_project_root = Path(roots[0]) if roots else None

    if from_dir is not None:
        assert single_slug is not None  # guaranteed by the block above
        try:
            result = seed_import.import_from_dir(
                resolved_root,
                single_slug,
                Path(from_dir),
                extra_patterns=config.redact.extra_patterns,
            )
        except seed_import.BadSeedSourceError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        total = total.merge(result)

    if from_claude_memory:
        if all_projects:
            for slug, roots in registry.items():
                if not roots:
                    continue
                result = seed_import.import_from_claude_memory(
                    resolved_root,
                    slug,
                    Path(roots[0]),
                    extra_patterns=config.redact.extra_patterns,
                )
                total = total.merge(result)
        else:
            assert single_slug is not None  # guaranteed by the block above
            if single_project_root is not None:
                result = seed_import.import_from_claude_memory(
                    resolved_root,
                    single_slug,
                    single_project_root,
                    extra_patterns=config.redact.extra_patterns,
                )
                total = total.merge(result)

    typer.echo(
        json.dumps(
            {
                "imported": total.imported,
                "unchanged": total.unchanged,
                "skipped": [{"path": p, "reason": r} for p, r in total.skipped],
            },
            ensure_ascii=False,
        )
    )


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
    shim = claude_install.shim_path()
    writes: list[_PendingWrite] = []

    # Hooks — user or project scope per --user.
    hooks_path = claude_install.settings_path(user=user, cwd=resolved_cwd)
    try:
        existing = claude_install.load_settings(hooks_path)
    except claude_install.SettingsParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    new_settings = claude_install.build_settings(existing, shim, config.inject.sources)
    h_before = claude_install.render(existing) if hooks_path.exists() else ""
    h_after = claude_install.render(new_settings)
    if h_before != h_after:
        writes.append(
            (
                hooks_path,
                h_before,
                h_after,
                lambda: claude_install.write_settings(hooks_path, new_settings),
            )
        )

    # MCP server — always user scope (~/.claude.json), spec §13 / decision D-d.
    mcp_path = claude_install.mcp_config_path()
    try:
        mcp_existing = claude_install.load_mcp_config(mcp_path)
    except claude_install.SettingsParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    mcp_new = claude_install.build_mcp_config(mcp_existing, shim)
    m_before = claude_install.render(mcp_existing) if mcp_path.exists() else ""
    m_after = claude_install.render(mcp_new)
    if m_before != m_after:
        writes.append(
            (mcp_path, m_before, m_after, lambda: claude_install.write_settings(mcp_path, mcp_new))
        )

    if not writes:
        typer.echo("Claude hooks and MCP server already up to date.")
        return

    for path, before, after, _writer in writes:
        typer.echo(_unified_diff(before, after, path))

    target_desc = ", ".join(str(p) for p, *_rest in writes)
    if not yes and not typer.confirm(f"Apply these changes to {target_desc}?"):
        typer.echo("Aborted — no changes made.")
        return

    backup_dir = backups.backup_files(resolved_root, [p for p, *_rest in writes])
    if backup_dir is not None:
        typer.echo(f"Backed up existing config to {backup_dir}")
    for _path, _before, _after, writer in writes:
        writer()
    typer.secho(
        "Installed Claude hooks + MCP server. Takes effect next session.",
        fg=typer.colors.GREEN,
    )
    typer.echo("Run `neurobase enable` in each repo you want captured (opt-in).")


def _init_codex(resolved_root: Path, resolved_cwd: Path, *, user: bool, yes: bool) -> None:
    """Install Codex hooks (spec §7) + register the MCP server (spec §13).

    Writes a ``hooks.json`` and edits ``~/.codex/config.toml``: for project
    scope, the ``[projects.*]`` trust/discovery table; and — always, user-scope —
    the ``[mcp_servers.neurobase]`` table so ``neurobase mcp serve`` is available.
    """
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

    # config.toml carries the project trust/discovery table (project scope only)
    # and the MCP server table (always — user-scope registration, spec §13).
    cfg_path = codex_install.config_path()
    project_key = str(project_root)
    try:
        cfg_before = codex_install.load_config_text(cfg_path)
        cfg_after = cfg_before
        if not user:
            cfg_after = codex_install.merge_config(cfg_after, project_key)
        cfg_after = codex_install.merge_mcp_config(cfg_after, shim)
    except codex_install.ConfigParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    cfg_changed = cfg_before != cfg_after

    if not hooks_changed and not cfg_changed:
        typer.echo("Codex hooks and MCP server already up to date.")
        return

    if hooks_changed:
        typer.echo(_unified_diff(hooks_before, hooks_after, hooks_path))
    if cfg_changed:
        typer.echo(_unified_diff(cfg_before, cfg_after, cfg_path))

    targets = ([hooks_path] if hooks_changed else []) + ([cfg_path] if cfg_changed else [])
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
        "Installed Codex hooks + MCP server. Takes effect next session.",
        fg=typer.colors.GREEN,
    )
    if hooks_changed:
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
    writes: list[_PendingWrite] = []

    path = claude_install.settings_path(user=user, cwd=resolved_cwd)
    if path.exists():
        existing = claude_install.load_settings(path)
        new_settings = claude_install.remove_owned_settings(existing)
        before = claude_install.render(existing)
        after = claude_install.render(new_settings)
        if before != after:
            writes.append(
                (path, before, after, lambda: claude_install.write_settings(path, new_settings))
            )

    # MCP registration is user-scope (~/.claude.json), removed regardless of the
    # hook scope being uninstalled.
    mcp_path = claude_install.mcp_config_path()
    if mcp_path.exists():
        mcp_existing = claude_install.load_mcp_config(mcp_path)
        mcp_new = claude_install.remove_mcp_config(mcp_existing)
        mcp_before = claude_install.render(mcp_existing)
        mcp_after = claude_install.render(mcp_new)
        if mcp_before != mcp_after:
            writes.append(
                (
                    mcp_path,
                    mcp_before,
                    mcp_after,
                    lambda: claude_install.write_settings(mcp_path, mcp_new),
                )
            )
    return writes


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

    # config.toml: drop the project hooks table (project scope) and the MCP
    # server table (always — it's user-scope).
    cfg_path = codex_install.config_path()
    if cfg_path.exists():
        cfg_before = codex_install.load_config_text(cfg_path)
        cfg_after = cfg_before
        if not user:
            cfg_after = codex_install.remove_project_hooks_config(cfg_after, str(project_root))
        cfg_after = codex_install.remove_mcp_config(cfg_after)
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


# --- recommend: Phase 8 proposal review -------------------------------------

recommend_app = typer.Typer(
    name="recommend",
    help="Mine and review skill/rule proposals from your history.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(recommend_app, name="recommend")


@recommend_app.command("list")
def recommend_list(
    project: str | None = typer.Option(None, "--project"),
    status_filter: str | None = typer.Option(None, "--status"),
    root: str | None = typer.Option(None, "--root"),
) -> None:
    """List proposals in deterministic review order."""
    resolved_root = store.resolve_root(root)
    _check_store_schema(resolved_root)
    for doc in proposals.load_all_proposals(resolved_root):
        if project is not None and doc.get("project") != project:
            continue
        if status_filter is not None and doc.get("status") != status_filter:
            continue
        scores = doc.get("scores") if isinstance(doc.get("scores"), dict) else {}
        typer.echo(
            f"{doc.get('name')}\t{doc.get('status')}\t{doc.get('type')}\t"
            f"{doc.get('target')}\t{scores.get('total', 0)}"
        )


@recommend_app.command("show")
def recommend_show(slug: str, root: str | None = typer.Option(None, "--root")) -> None:
    """Show a proposal, evidence resolution, and ledger history."""
    resolved_root = store.resolve_root(root)
    _check_store_schema(resolved_root)
    doc = proposals.load_proposal(resolved_root, slug)
    if doc is None:
        typer.secho(f"proposal {slug!r} not found or malformed", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(doc.body.rstrip())
    typer.echo("\nEvidence:")
    for item in doc.get("evidence") or []:
        try:
            ref = recommend_corpus.EvidenceRef.from_frontmatter(item)
            resolved = recommend_corpus.resolve_evidence(resolved_root, ref)
            typer.echo(f"- {ref.to_frontmatter()} [{resolved.status}]")
        except (KeyError, ValueError):
            typer.echo(f"- {item!r} [unresolved]")
    typer.echo("\nHistory:")
    for event in proposals.ledger_history(resolved_root, slug):
        typer.echo(json.dumps(event, ensure_ascii=False))


@recommend_app.command("run")
def recommend_run(
    dry_run: bool = typer.Option(False, "--dry-run"),
    root: str | None = typer.Option(None, "--root"),
) -> None:
    """Mine, rank, and optionally persist proposals."""
    config = load_config()
    resolved_root = store.resolve_root(root)
    _check_store_schema(resolved_root)
    brain, resolution = resolve_brain(config)
    if brain is None:
        typer.secho(
            f"No brain backend available ({resolution.reason}); run `neurobase doctor`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    candidates = miner.mine(resolved_root, brain, config=config.recommend)
    loaded = recommend_corpus.load_corpus(resolved_root, config=config.recommend)
    ranked = ranker.rank(resolved_root, candidates, loaded, config=config.recommend)
    if dry_run:
        for candidate in ranked:
            typer.echo(f"{candidate.slug}\t{candidate.type}\t{candidate.scores.total}")
        return
    outcome = proposals.write_ranked(resolved_root, ranked, config=config.recommend)
    typer.echo(json.dumps(outcome.__dict__, ensure_ascii=False))


@recommend_app.command("edit")
def recommend_edit(slug: str, root: str | None = typer.Option(None, "--root")) -> None:
    """Edit only a proposal's managed artifact draft."""
    resolved_root = store.resolve_root(root)
    _check_store_schema(resolved_root)
    doc = proposals.load_proposal(resolved_root, slug)
    if doc is None:
        typer.secho(f"proposal {slug!r} not found or malformed", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    status = str(doc.get("status") or "proposed")
    if status in {"rejected", "superseded"}:
        typer.secho(f"cannot edit proposal {slug!r}: status is {status}", err=True)
        raise typer.Exit(code=1)
    draft = proposals.extract_draft(doc.body)
    if draft is None:
        typer.secho(f"proposal {slug!r} has no managed draft region", err=True)
        raise typer.Exit(code=1)
    edited = click.edit(draft, extension=".md")
    if edited is None:
        typer.echo(draft)
        return
    if not proposals.save_edited_draft(resolved_root, slug, edited):
        typer.secho("could not save edited draft", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Edited proposal {slug}.")


@recommend_app.command("reject")
def recommend_reject(
    slug: str,
    reason: str | None = typer.Option(None, "--reason"),
    root: str | None = typer.Option(None, "--root"),
) -> None:
    """Reject a proposed candidate without touching agent configuration."""
    resolved_root = store.resolve_root(root)
    _check_store_schema(resolved_root)
    try:
        proposals.reject_proposal(resolved_root, slug, reason=reason)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rejected proposal {slug}.")


# --- mcp: the MCP server (Phase 7) --------------------------------------------

mcp_app = typer.Typer(
    name="mcp",
    help="Run the MCP server exposing memory tools to any client.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    root: str | None = typer.Option(None, "--root", help="Override the store root."),
) -> None:
    """Serve memory tools over stdio to any MCP client (Claude, Codex, …)."""
    # Lazy import: the mcp SDK pulls in starlette/uvicorn/pydantic — keep it off
    # the hot path for every other command (and the hook fast-path).
    from neurobase.mcp import serve as _serve

    _serve(store.resolve_root(root))


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
