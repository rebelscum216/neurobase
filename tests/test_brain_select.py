"""Tests for backend selection + detection (select.py, decision D9)."""

from __future__ import annotations

import pytest

from neurobase.brain import anthropic_api, select
from neurobase.brain.anthropic_api import AnthropicAPIBrain
from neurobase.brain.claude_cli import ClaudeCLIBrain
from neurobase.brain.codex_cli import CodexCLIBrain
from neurobase.core.config import Config
from neurobase.core.process_guard import INTERNAL_CALL_ENV


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Neutralize the OS keychain so a real dev-machine entry can't leak in.
    monkeypatch.setattr(anthropic_api, "_keychain_api_key", lambda: None)


def _no_clis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(select.shutil, "which", lambda _: None)


def _clis_present(monkeypatch: pytest.MonkeyPatch, *present: str) -> None:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(select.shutil, "which", which)
    monkeypatch.setattr(select, "_cli_version", lambda binary: f"{binary} 1.0")


def test_auto_prefers_claude_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _clis_present(monkeypatch, "claude", "codex")
    brain, resolution = select.resolve_brain(Config())
    assert isinstance(brain, ClaudeCLIBrain)
    assert resolution.backend == "claude-cli"
    assert resolution.available


def test_cli_version_marks_agent_process_as_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return select.subprocess.CompletedProcess(cmd, 0, stdout="claude 1.0\n", stderr="")

    monkeypatch.setattr(select.subprocess, "run", fake_run)
    assert select._cli_version("claude") == "claude 1.0"
    assert seen["env"][INTERNAL_CALL_ENV] == "1"


def test_auto_falls_through_to_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    _clis_present(monkeypatch, "codex")
    brain, resolution = select.resolve_brain(Config())
    assert isinstance(brain, CodexCLIBrain)
    assert resolution.backend == "codex-cli"


def test_auto_falls_through_to_anthropic_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_clis(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    brain, resolution = select.resolve_brain(Config())
    assert isinstance(brain, AnthropicAPIBrain)
    assert resolution.backend == "anthropic-api"


def test_auto_nothing_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_clis(monkeypatch)
    brain, resolution = select.resolve_brain(Config())
    assert brain is None
    assert not resolution.available
    assert resolution.backend == "auto"


def test_explicit_backend_pins_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _clis_present(monkeypatch, "claude", "codex")
    config = Config()
    config.brain.backend = "codex-cli"
    brain, resolution = select.resolve_brain(config)
    assert isinstance(brain, CodexCLIBrain)
    assert resolution.backend == "codex-cli"


def test_explicit_backend_unavailable_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_clis(monkeypatch)
    config = Config()
    config.brain.backend = "claude-cli"
    brain, resolution = select.resolve_brain(config)
    assert brain is None
    assert resolution.backend == "claude-cli"
    assert not resolution.available


def test_openai_api_is_honestly_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    resolution = select.detect("openai-api", Config())
    assert not resolution.available
    assert "not implemented" in resolution.reason


def test_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    resolution = select.detect("gpt-9000", Config())
    assert not resolution.available
    assert "unknown backend" in resolution.reason


def test_anthropic_api_reports_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEUROBASE_API_KEY", "sk-x")
    config = Config()
    config.brain.model = "claude-opus-4-8"
    resolution = select.detect("anthropic-api", config)
    assert resolution.available
    assert "claude-opus-4-8" in resolution.reason
