"""Integration test for `neurobase doctor` (Phase 2 brain section)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.brain import select
from neurobase.cli import app
from neurobase.core.config import Config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    # No API keys, and a default config regardless of the dev machine's own.
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_config", Config)


def test_doctor_reports_resolved_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str | None:
        return "/usr/bin/claude" if name == "claude" else None

    monkeypatch.setattr(select.shutil, "which", which)
    monkeypatch.setattr(select, "_cli_version", lambda binary: "claude 2.1.x")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "brain: claude-cli" in result.output
    assert "2.1.x" in result.output


def test_doctor_exits_nonzero_when_nothing_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(select.shutil, "which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "brain: none" in result.output
