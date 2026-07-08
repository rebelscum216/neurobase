"""Neurobase command-line interface (Typer app — decision D12).

Phase 0: ``--help`` and ``version`` are live. The rest of the planned command
surface is declared as honest stubs so ``neurobase --help`` shows where the tool is
going; each stub exits non-zero and names the phase that will implement it. As
phases land, replace a stub with its real command.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import typer

from neurobase import __version__
from neurobase.adapters.claude import recall, scribe
from neurobase.brain import resolve_brain
from neurobase.core import projects, store
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
def doctor() -> None:
    """Diagnose the install: which brain backend resolves, and why (Phase 2).

    (Shim + agent + store-health checks land with their phases; Phase 2 covers
    the brain section — build-plan Phase 2 demo.)
    """
    config = load_config()
    brain, resolution = resolve_brain(config)
    label = resolution.backend
    if resolution.version:
        label += f" ({resolution.version})"
    if brain is not None:
        typer.secho(f"brain: {label} — {resolution.reason}", fg=typer.colors.GREEN)
    else:
        typer.secho(
            f"brain: none — {resolution.reason} (configured backend: {config.brain.backend})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


# --- Planned command surface (stubs until each command's phase lands) ---------

_PLANNED: list[tuple[str, int, str]] = [
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


_HOOK_FLAGS = ("--transcript", "--cwd", "--root", "--reason")


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
        # codex (Phase 5) and any unknown agent/event: no-op.
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
