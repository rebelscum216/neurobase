"""Tests for the Claude hook installer (spec §7) + backups (spec §10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neurobase.adapters.claude import install
from neurobase.core import backups

SHIM = "/abs/shim/neurobase"


def test_build_settings_into_empty() -> None:
    result = install.build_settings({}, SHIM, ["startup", "clear"])
    hooks = result["hooks"]
    assert hooks["SessionEnd"][0]["hooks"][0]["command"] == f"{SHIM} hook claude session-end"
    start = hooks["SessionStart"][0]
    assert start["matcher"] == "startup|clear"
    assert start["hooks"][0]["command"] == f"{SHIM} hook claude session-start"


def test_preserves_unrelated_keys_and_hooks() -> None:
    pre_tool_use = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/audit"}]}
    ]
    existing: dict = {"model": "claude-sonnet-5", "hooks": {"PreToolUse": pre_tool_use}}
    result = install.build_settings(existing, SHIM, ["startup", "clear"])
    assert result["model"] == "claude-sonnet-5"
    assert result["hooks"]["PreToolUse"] == pre_tool_use
    assert "SessionEnd" in result["hooks"] and "SessionStart" in result["hooks"]


def test_idempotent() -> None:
    once = install.build_settings({}, SHIM, ["startup", "clear"])
    twice = install.build_settings(once, SHIM, ["startup", "clear"])
    assert install.render(once) == install.render(twice)


def test_remove_owned_settings_preserves_foreign_hooks() -> None:
    owned = install.build_settings({}, SHIM, ["startup", "clear"])
    foreign = {"hooks": [{"type": "command", "command": "/usr/bin/audit"}]}
    owned["hooks"]["PreToolUse"] = [foreign]
    result = install.remove_owned_settings(owned)
    assert result == {"hooks": {"PreToolUse": [foreign]}}


def test_replaces_owned_group_not_stacking() -> None:
    # An old Neurobase entry (different shim path) should be replaced, not kept.
    old_cmd = "/old/path/neurobase hook claude session-end"
    existing = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": old_cmd}]}]}}
    result = install.build_settings(existing, SHIM, ["startup", "clear"])
    end_groups = result["hooks"]["SessionEnd"]
    assert len(end_groups) == 1
    assert end_groups[0]["hooks"][0]["command"] == f"{SHIM} hook claude session-end"


def test_owned_marker_leaves_foreign_similar_command() -> None:
    # A user command that merely mentions neurobase but isn't a hook entry stays.
    foreign = {"hooks": [{"type": "command", "command": "/bin/echo neurobase is cool"}]}
    existing = {"hooks": {"SessionEnd": [foreign]}}
    result = install.build_settings(existing, SHIM, ["startup", "clear"])
    # foreign kept + our owned appended
    assert foreign in result["hooks"]["SessionEnd"]
    assert any(
        g["hooks"][0]["command"] == f"{SHIM} hook claude session-end"
        for g in result["hooks"]["SessionEnd"]
    )


def test_ownership_fence_is_path_anchored_not_bare_substring() -> None:
    # Codex F1 (spec §7): the fence is `<shim>/neurobase hook`, not a bare
    # `neurobase hook ` substring. A foreign command that mentions the phrase in
    # prose — neurobase NOT preceded by a path separator — must be preserved.
    foreign = {
        "hooks": [{"type": "command", "command": '/bin/echo "run neurobase hook to install"'}]
    }
    existing = {"hooks": {"SessionEnd": [foreign]}}
    result = install.build_settings(existing, SHIM, ["startup", "clear"])
    assert foreign in result["hooks"]["SessionEnd"]  # untouched
    assert not install._is_owned_group(foreign)


def test_is_owned_group() -> None:
    owned = {"hooks": [{"type": "command", "command": "/x/neurobase hook claude session-end"}]}
    # Windows-style path with .exe is still ours.
    owned_win = {
        "hooks": [{"type": "command", "command": r"C:\tools\neurobase.exe hook claude session-end"}]
    }
    foreign = {"hooks": [{"type": "command", "command": "/x/other-tool run"}]}
    # Prose mention (neurobase not preceded by a separator) is not ours.
    prose = {"hooks": [{"type": "command", "command": "echo please run neurobase hook manually"}]}
    # `neurobase hookX` must not match (word-boundary guard).
    hookx = {"hooks": [{"type": "command", "command": "/x/neurobase hookXYZ"}]}
    assert install._is_owned_group(owned)
    assert install._is_owned_group(owned_win)
    assert not install._is_owned_group(foreign)
    assert not install._is_owned_group(prose)
    assert not install._is_owned_group(hookx)


def test_load_settings_missing_returns_empty(tmp_path: Path) -> None:
    assert install.load_settings(tmp_path / "nope.json") == {}


def test_load_settings_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(install.SettingsParseError):
        install.load_settings(path)


def test_settings_path_scopes(tmp_path: Path) -> None:
    proj = install.settings_path(user=False, cwd=tmp_path)
    assert proj == tmp_path / ".claude" / "settings.json"
    user = install.settings_path(user=True, cwd=tmp_path)
    assert user == Path.home() / ".claude" / "settings.json"


def test_write_settings_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    settings = install.build_settings({}, SHIM, ["startup", "clear"])
    install.write_settings(path, settings)
    assert json.loads(path.read_text()) == settings
    assert path.read_text().endswith("\n")


# --- backups (spec §10) ---------------------------------------------------


def test_backup_files_writes_manifest(tmp_path: Path) -> None:
    root = tmp_path / "store"
    target = tmp_path / "cfg.json"
    target.write_text('{"a":1}', encoding="utf-8")
    backup_dir = backups.backup_files(root, [target])
    assert backup_dir is not None
    assert (backup_dir / "cfg.json").read_text() == '{"a":1}'
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert manifest == [
        {"original_abs_path": str(target.resolve()), "stored_as": str(backup_dir / "cfg.json")}
    ]


def test_backup_files_none_when_nothing_exists(tmp_path: Path) -> None:
    root = tmp_path / "store"
    assert backups.backup_files(root, [tmp_path / "missing.json"]) is None


def test_restore_backup_round_trips_manifest(tmp_path: Path) -> None:
    root = tmp_path / "store"
    target = tmp_path / "settings.json"
    target.write_text("before", encoding="utf-8")
    backup_dir = backups.backup_files(root, [target])
    assert backup_dir is not None
    target.write_text("after", encoding="utf-8")

    restored = backups.restore_backup(root, backup_dir.name)

    assert restored == [target.resolve()]
    assert target.read_text(encoding="utf-8") == "before"
