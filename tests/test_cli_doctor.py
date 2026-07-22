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
    # Windows Path.home() reads USERPROFILE, not HOME — set both to isolate.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
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


# --- store health via the DOCTOR handle (ADR-0015 D26) -------------------


def test_doctor_reports_unsupported_store_schema_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A store.toml a version too new is *reported*, not refused: a DOCTOR handle
    # carries the newer schema instead of raising, so doctor exits with an error
    # rather than crashing.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text(
        f"schema = {store.STORE_SCHEMA_VERSION + 1}\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["doctor"])

    # A clean CLI error exits via typer.Exit (SystemExit, not an Exception); a
    # genuine crash would surface an Exception subclass here instead.
    assert not isinstance(result.exception, Exception)
    assert result.exit_code == 1
    assert f"unsupported schema {store.STORE_SCHEMA_VERSION + 1}" in result.output


def test_doctor_reports_corrupt_store_toml_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D26 carry-in: a genuinely-corrupt store.toml makes open_store(DOCTOR) raise
    # UnsupportedSchemaError; doctor must catch and report it, never crash — it is
    # a read-only reporting surface.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text("this is [not valid toml", encoding="utf-8")

    result = runner.invoke(app, ["doctor"])

    assert not isinstance(result.exception, Exception)  # caught + reported, not crashed
    assert result.exit_code == 1
    assert "unreadable" in result.output


def test_doctor_is_read_only_and_does_not_create_store_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Doctor inspects via a DOCTOR handle, which never writes: reporting on an
    # uninitialized store must not materialize store.toml as a side effect.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "not initialized yet" in result.output
    assert not (root / "store.toml").exists()


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


def test_doctor_warns_on_stale_mcp_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    # Right command, wrong args ⇒ not startable ⇒ doctor must warn, not OK.
    claude_install.write_settings(
        claude_install.mcp_config_path(),
        {"mcpServers": {"neurobase": {"type": "stdio", "command": SHIM, "args": ["bad"]}}},
    )
    result = runner.invoke(app, ["doctor"])
    assert "unexpected command or args" in result.output
    assert "registers neurobase →" not in result.output  # not reported OK


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


def test_doctor_recognizes_absolute_project_codex_trust_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_path = repo / ".codex" / "hooks.json"
    codex_install.write_hooks(hooks_path, codex_install.build_hooks({}, SHIM))
    key = str(hooks_path).replace("\\", "\\\\")
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += (
        f'\n[hooks.state]\n"{key}:session_start:0:0" = {{ trusted_hash = "abc" }}\n'
        f'"{key}:stop:0:0" = {{ trusted_hash = "def" }}\n'
    )
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex trust:" in result.output
    assert "! codex trust:" not in result.output


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
    # Escape backslashes so a Windows path is a valid TOML basic-string key.
    key = str(user_hooks).replace("\\", "\\\\")
    codex_install.write_config(
        tmp_path / "home" / ".codex" / "config.toml",
        (
            "[hooks.state]\n"
            f'"{key}:session_start:0:0" = {{ trusted_hash = "abc" }}\n'
            f'"{key}:stop:0:0" = {{ trusted_hash = "def" }}\n'
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
