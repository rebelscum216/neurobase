"""Neurobase command-line interface (Typer app — decision D12).

Phase 0: ``--help`` and ``version`` are live. The rest of the planned command
surface is declared as honest stubs so ``neurobase --help`` shows where the tool is
going; each stub exits non-zero and names the phase that will implement it. As
phases land, replace a stub with its real command.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from neurobase import __version__
from neurobase.core import projects, store

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


# --- Planned command surface (stubs until each command's phase lands) ---------

_PLANNED: list[tuple[str, int, str]] = [
    ("doctor", 2, "Diagnose the install: shim, agents, brain backend, store health."),
    ("curate", 3, "Fold unconsumed raw captures into the curated fact set."),
    ("recall", 4, "Print the memory that would be injected for a project."),
    ("init", 6, "Interactive setup: detect agents, choose store root, install hooks."),
    ("uninstall", 6, "Remove Neurobase-owned hooks; leave the store intact."),
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


@app.command(
    name="hook",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def hook_stub(ctx: typer.Context) -> None:
    """Capture/inject entry point invoked by agent hooks. [not implemented — Phase 4]"""
    typer.secho(
        "`neurobase hook` is not implemented yet (planned for Phase 4).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    # Spec §4/§5: hook entry points MUST always exit 0 — never wedge an
    # agent's session start/teardown, even before real logic lands. Any
    # <agent> <event> args (e.g. "claude session-start") are accepted and
    # ignored via allow_extra_args, matching the eventual Phase-4 signature.
