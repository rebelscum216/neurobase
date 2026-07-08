"""Integration tests for `neurobase doctor` (Phase 6 lifecycle matrix)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.brain import anthropic_api, select
from neurobase.cli import app, diagnostics
from neurobase.core import projects, store
from neurobase.core.config import Config

runner = CliRunner()
SHIM = "/usr/local/bin/neurobase"


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No API keys, no keychain leak, and a default config regardless of the
    # dev machine's own.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEUROBASE_ROOT", str(tmp_path / "store"))
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(anthropic_api, "_keychain_api_key", lambda: None)
    monkeypatch.setattr(cli, "load_config", Config)
    monkeypatch.setattr(diagnostics.claude_install, "shim_path", lambda: SHIM)


def _which(name: str) -> str | None:
    paths = {
        "neurobase": SHIM,
        "claude": "/usr/bin/claude",
        "codex": "/usr/bin/codex",
    }
    return paths.get(name)


def _patch_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics.shutil, "which", _which)
    monkeypatch.setattr(select.shutil, "which", _which)
    monkeypatch.setattr(select, "_cli_version", lambda binary: f"{binary} 2.1.x")


def test_doctor_reports_resolved_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "brain: claude-cli" in result.output
    assert "2.1.x" in result.output


def test_doctor_exits_nonzero_when_nothing_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(select.shutil, "which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "brain: none" in result.output


def test_doctor_reports_mcp_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    # The mcp checks always run (as warnings before registration).
    before = runner.invoke(app, ["doctor"])
    assert "claude mcp" in before.output
    assert "codex mcp" in before.output
    # Register the Claude MCP server (user scope) → doctor reports it ok.
    claude_install.write_settings(
        claude_install.mcp_config_path(), claude_install.build_mcp_config({}, SHIM)
    )
    after = runner.invoke(app, ["doctor"])
    assert "registers neurobase" in after.output


def test_doctor_reports_installed_hooks_and_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = projects.register_project(root, repo)
    store.ensure_tree(slug, root)

    claude_install.write_settings(
        repo / ".claude" / "settings.json",
        claude_install.build_settings({}, SHIM, ["startup", "clear"]),
    )
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += (
        '\n[hooks.state]\n".codex/hooks.json:session_start:0:0" = { trusted_hash = "abc" }\n'
        '".codex/hooks.json:stop:0:0" = { trusted_hash = "def" }\n'
    )
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ store:" in result.output
    assert "✓ project:" in result.output
    assert "✓ claude hooks:" in result.output
    assert "✓ codex hooks:" in result.output
    assert "✓ codex config:" in result.output
    assert "✓ codex trust:" in result.output


def test_doctor_warns_for_untrusted_codex_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    codex_install.write_config(
        tmp_path / "home" / ".codex" / "config.toml",
        codex_install.merge_config("", str(repo.resolve())),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "! codex trust:" in result.output
    assert "approve the hook prompt" in result.output


def test_doctor_does_not_accept_unrelated_codex_trusted_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += '\n[hooks.state]\n"other/hooks.json:session_start:0:0" = { trusted_hash = "abc" }\n'
    cfg += '".codex/hooks.json:stop:0:0" = { trusted_hash = "def" }\n'
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "! codex trust:" in result.output
    assert "✓ codex trust:" not in result.output


def test_doctor_recognizes_user_scoped_claude_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    claude_install.write_settings(
        tmp_path / "home" / ".claude" / "settings.json",
        claude_install.build_settings({}, SHIM, ["startup", "clear"]),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ claude hooks:" in result.output
    assert "user " in result.output


def test_doctor_recognizes_user_scoped_codex_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    user_hooks = tmp_path / "home" / ".codex" / "hooks.json"
    codex_install.write_hooks(user_hooks, codex_install.build_hooks({}, SHIM))
    codex_install.write_config(
        tmp_path / "home" / ".codex" / "config.toml",
        (
            "[hooks.state]\n"
            f'"{user_hooks}:session_start:0:0" = {{ trusted_hash = "abc" }}\n'
            f'"{user_hooks}:stop:0:0" = {{ trusted_hash = "def" }}\n'
        ),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex hooks:" in result.output
    assert "user " in result.output
    assert "✓ codex config: user hooks are auto-discovered" in result.output
    assert "✓ codex trust:" in result.output


def test_doctor_warns_when_user_scoped_codex_hook_has_no_trust_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        tmp_path / "home" / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex hooks:" in result.output
    assert "✓ codex config: user hooks are auto-discovered" in result.output
    assert "! codex trust:" in result.output
    assert "approve the hook prompt" in result.output
