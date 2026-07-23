"""Integration tests for `neurobase uninstall` (Phase 6 lifecycle)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.cli import app
from neurobase.core import backups

runner = CliRunner()
SHIM = "/abs/shim/neurobase"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    # Windows Path.home() reads USERPROFILE, not HOME — set both to isolate.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("NEUROBASE_ROOT", str(tmp_path / "store"))
    monkeypatch.setattr(claude_install, "shim_path", lambda: SHIM)
    monkeypatch.setattr(codex_install, "shim_path", lambda: SHIM)
    return home


def test_uninstall_removes_mcp_registration(env: Path, tmp_path: Path) -> None:
    # Register the MCP server in both agents' user configs, then uninstall.
    claude_mcp = env / ".claude.json"
    claude_install.write_settings(
        claude_mcp, claude_install.build_mcp_config({"userID": "x"}, SHIM)
    )
    codex_cfg = env / ".codex" / "config.toml"
    codex_cfg.parent.mkdir(parents=True)
    codex_install.write_config(codex_cfg, codex_install.merge_mcp_config("", SHIM))

    result = runner.invoke(app, ["uninstall", "--agent", "all", "--yes"])
    assert result.exit_code == 0

    claude_after = json.loads(claude_mcp.read_text(encoding="utf-8"))
    assert "mcpServers" not in claude_after
    assert claude_after["userID"] == "x"  # unrelated keys preserved
    config_after = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert "neurobase" not in config_after.get("mcp_servers", {})


def test_uninstall_claude_removes_owned_hooks_and_preserves_foreign(
    env: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    foreign = {"hooks": [{"type": "command", "command": "/usr/bin/audit"}]}
    doc = claude_install.build_settings({"hooks": {"PreToolUse": [foreign]}}, SHIM, ["startup"])
    claude_install.write_settings(settings, doc)

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 0
    assert "Uninstalled" in result.output
    assert json.loads(settings.read_text(encoding="utf-8")) == {"hooks": {"PreToolUse": [foreign]}}
    manifests = list((tmp_path / "store" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1


def test_uninstall_codex_project_removes_hooks_and_config_hooks_key(
    env: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_path = repo / ".codex" / "hooks.json"
    codex_install.write_hooks(hooks_path, codex_install.build_hooks({}, SHIM))
    cfg_path = env / ".codex" / "config.toml"
    cfg_text = codex_install.merge_config("", str(repo.resolve()))
    codex_install.write_config(cfg_path, cfg_text)

    result = runner.invoke(app, ["uninstall", "--agent", "codex", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 0
    assert json.loads(hooks_path.read_text(encoding="utf-8")) == {}
    project = tomllib.loads(cfg_path.read_text(encoding="utf-8"))["projects"][str(repo.resolve())]
    assert project == {"trust_level": "trusted"}


def test_uninstall_restore_backup_is_explicit(env: Path, tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("before", encoding="utf-8")
    backup_dir = backups.backup_files(tmp_path / "store", [target])
    assert backup_dir is not None
    target.write_text("after", encoding="utf-8")

    result = runner.invoke(app, ["uninstall", "--restore-backup", backup_dir.name, "--yes"])

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "before"
    assert "Restored" in result.output


def test_uninstall_purge_store_requires_explicit_flag(env: Path, tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    store_root.mkdir()

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--purge-store", "--yes"])

    assert result.exit_code == 0
    assert not store_root.exists()
    assert "Deleted store" in result.output


def test_uninstall_purge_deletes_newer_schema_store_without_pre_delete_backup(
    env: Path, tmp_path: Path
) -> None:
    """D25 (ADR-0015 step 4d): `--purge-store` must delete even a store whose schema is
    newer than we support (PURGE opens anything, never refuses), and must NOT write a
    config backup *into* that store before deleting it — deletion is the one sanctioned
    mutation of an unsupported store. Pre-4d the purge path backed up first, into the
    doomed (and unsupported) store."""
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    claude_install.write_settings(settings, claude_install.build_settings({}, SHIM, ["startup"]))
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(
        app, ["uninstall", "--agent", "claude", "--cwd", str(repo), "--purge-store", "--yes"]
    )

    assert result.exit_code == 0
    assert "Deleted store" in result.output
    assert "Backed up" not in result.output  # the pre-delete backup was skipped
    assert not store_root.exists()  # gone despite the unsupported schema


def test_uninstall_no_hooks_found(env: Path) -> None:
    result = runner.invoke(app, ["uninstall", "--agent", "all", "--yes"])
    assert result.exit_code == 0
    assert "No Neurobase hooks found" in result.output


def test_uninstall_malformed_claude_settings_no_clobber(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json", encoding="utf-8")

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 1
    assert settings.read_text(encoding="utf-8") == "{ not json"
