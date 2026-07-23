"""Integration tests for `neurobase uninstall` (Phase 6 lifecycle)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.cli import app
from neurobase.core import backups
from neurobase.core.store_handle import StoreHandle, StoreMode

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


def test_uninstall_purge_opens_purge_handle_on_root_before_delete(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D25 (4d): purge must open a PURGE handle *on the resolved root* and *before*
    `rmtree` of that same root. Spying both the open and the delete — with their roots
    and order — pins all three: a bare `PURGE in modes` would still pass if the open
    moved *after* the delete (PURGE opens an absent root) or targeted a different path
    (Codex round-2 F6)."""
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 1\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )
    events: list[tuple[str, Path, StoreMode | None]] = []
    real_open = cli._open_store_or_exit
    real_rmtree = cli.shutil.rmtree

    def spy_open(root: Path, mode: StoreMode) -> StoreHandle:
        events.append(("open", Path(root), mode))
        return real_open(root, mode)

    def spy_rmtree(path: Any, *args: Any, **kwargs: Any) -> Any:
        events.append(("rmtree", Path(path), None))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cli, "_open_store_or_exit", spy_open)
    monkeypatch.setattr(cli.shutil, "rmtree", spy_rmtree)

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--purge-store", "--yes"])

    assert result.exit_code == 0
    assert not store_root.exists()
    purge_opens = [i for i, e in enumerate(events) if e[0] == "open" and e[2] == StoreMode.PURGE]
    rmtrees = [i for i, e in enumerate(events) if e[0] == "rmtree"]
    assert purge_opens and rmtrees
    assert events[purge_opens[0]][1] == store_root.resolve()  # PURGE opened the resolved root
    assert events[rmtrees[0]][1] == store_root.resolve()  # rmtree hit the same root
    assert purge_opens[0] < rmtrees[0]  # open strictly before delete


def test_uninstall_purge_deletes_store_with_unparseable_metadata(env: Path, tmp_path: Path) -> None:
    """PURGE opens even an *unparseable* store.toml (READ/WRITE/DOCTOR would raise), the
    D25 escape hatch: you can always delete a store you cannot parse. If the purge path
    used a refusing mode instead, this would exit 1 rather than deleting."""
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text("this is not = valid = toml ][", encoding="utf-8")

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--purge-store", "--yes"])

    assert result.exit_code == 0
    assert not store_root.exists()


def test_non_purge_uninstall_backs_up_on_unsupported_store(env: Path, tmp_path: Path) -> None:
    """The config-backup facility is a schema-independent maintenance exception (§10 /
    D25): a *non-purge* uninstall must still back up config + remove hooks even when the
    store's schema is newer than we support — it opens no handle, so it is never refused.
    A future READ guard on this path would brick uninstall on a newer-schema store; this
    pins that it doesn't (Codex round-2 F7)."""
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    claude_install.write_settings(settings, claude_install.build_settings({}, SHIM, ["startup"]))
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["uninstall", "--agent", "claude", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 0
    assert "Backed up" in result.output  # backup happened despite the unsupported schema
    assert json.loads(settings.read_text(encoding="utf-8")) == {}  # owned hooks removed
    assert len(list((store_root / "backups").glob("*/manifest.json"))) == 1


def test_restore_backup_recovers_on_unsupported_store(env: Path, tmp_path: Path) -> None:
    """The same maintenance exception for `--restore-backup`: disaster-recovery must work
    on a store of any schema (it opens no handle). Pins that a newer schema does not block
    restore (Codex round-2 F7)."""
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )
    target = tmp_path / "settings.json"
    target.write_text("before", encoding="utf-8")
    backup_dir = backups.backup_files(store_root, [target])
    assert backup_dir is not None
    target.write_text("after", encoding="utf-8")

    result = runner.invoke(app, ["uninstall", "--restore-backup", backup_dir.name, "--yes"])

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "before"  # recovered despite schema 999


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
