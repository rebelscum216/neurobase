"""Neurobase command-line interface (Typer app — decision D12).

Phase 0: ``--help`` and ``version`` are live. The rest of the planned command
surface is declared as honest stubs so ``neurobase --help`` shows where the tool is
going; each stub exits non-zero and names the phase that will implement it. As
phases land, replace a stub with its real command.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from neurobase import __version__

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


# --- Planned command surface (stubs until each command's phase lands) ---------

_PLANNED: list[tuple[str, int, str]] = [
    ("enable", 1, "Register the current repo as a project and create its memory tree."),
    ("status", 1, "Show projects, raw/curated counts, nodes, and fact-count trend."),
    ("doctor", 2, "Diagnose the install: shim, agents, brain backend, store health."),
    ("curate", 3, "Fold unconsumed raw captures into the curated fact set."),
    ("recall", 4, "Print the memory that would be injected for a project."),
    ("init", 6, "Interactive setup: detect agents, choose store root, install hooks."),
    ("uninstall", 6, "Remove Neurobase-owned hooks; leave the store intact."),
    ("mcp", 7, "Run the MCP server exposing memory tools to any client."),
    ("recommend", 8, "Review skill/rule proposals mined from your history."),
    ("seed", 8, "Import existing notes / Claude auto-memory as curated facts."),
    ("hook", 4, "Deterministic capture/inject entry point invoked by agent hooks."),
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
