"""Phase 0 smoke tests — the CLI installs, runs, and exposes its surface honestly."""

from __future__ import annotations

from typer.testing import CliRunner

from neurobase import __version__
from neurobase.cli import app

runner = CliRunner()


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # The planned command surface is visible in help.
    assert "version" in result.output
    assert "curate" in result.output


def test_version_prints_metadata_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help prints usage; Click exits 2 ("missing command") in that path.
    assert result.exit_code == 2
    assert "Usage" in result.output


def test_stub_command_exits_nonzero() -> None:
    result = runner.invoke(app, ["recall"])
    assert result.exit_code == 1


def test_version_is_not_placeholder() -> None:
    # When installed (editable via `uv sync`), metadata resolves to the real version.
    assert __version__ != "0+unknown"
