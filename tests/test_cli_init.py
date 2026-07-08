"""Integration tests for `neurobase init --agent claude` (spec §7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neurobase.cli import app

runner = CliRunner()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate HOME (so config_path()/`--user` scope resolve into tmp, never the
    real home) and pin the backup store root. Returns the isolated home dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEUROBASE_ROOT", str(tmp_path / "store"))
    return home


def _hook_commands(settings: dict) -> list[str]:
    cmds: list[str] = []
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for entry in group.get("hooks", []):
                cmds.append(entry["command"])
    return cmds


def test_init_consent_yes_writes_and_backs_up(env: Path, tmp_path: Path) -> None:
    """Existing config → consent 'y' → backup taken, hooks written."""
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model": "claude-opus-4-8"}\n', encoding="utf-8")

    result = runner.invoke(
        app, ["init", "--agent", "claude", "--cwd", str(repo)], input="y\n"
    )
    assert result.exit_code == 0
    assert "Backed up" in result.output
    assert "Takes effect next session" in result.output

    written = json.loads(settings.read_text())
    assert written["model"] == "claude-opus-4-8"  # preserved
    cmds = _hook_commands(written)
    assert any(c.endswith("hook claude session-end") for c in cmds)
    assert any(c.endswith("hook claude session-start") for c in cmds)

    # The backup landed under the pinned store root with a manifest.
    backups_root = tmp_path / "store" / "backups"
    manifests = list(backups_root.glob("*/manifest.json"))
    assert len(manifests) == 1


def test_init_consent_no_aborts_unchanged(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = '{"model": "claude-opus-4-8"}\n'
    settings.write_text(original, encoding="utf-8")

    result = runner.invoke(
        app, ["init", "--agent", "claude", "--cwd", str(repo)], input="n\n"
    )
    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert settings.read_text() == original  # untouched
    assert not (tmp_path / "store" / "backups").exists()


def test_init_user_scope_targets_home(env: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--agent", "claude", "--user", "--yes"])
    assert result.exit_code == 0
    user_settings = env / ".claude" / "settings.json"
    assert user_settings.exists()
    cmds = _hook_commands(json.loads(user_settings.read_text()))
    assert any(c.endswith("hook claude session-start") for c in cmds)


def test_init_preserves_existing_hooks(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    foreign = {
        "model": "claude-opus-4-8",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/audit"}]}
            ]
        },
    }
    settings.write_text(json.dumps(foreign), encoding="utf-8")

    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 0
    written = json.loads(settings.read_text())
    assert written["model"] == "claude-opus-4-8"
    assert written["hooks"]["PreToolUse"] == foreign["hooks"]["PreToolUse"]


def test_init_idempotent_second_run(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    first = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])
    assert second.exit_code == 0
    assert "already up to date" in second.output


def test_init_malformed_settings_no_clobber(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not valid json", encoding="utf-8")

    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 1
    assert settings.read_text() == "{ not valid json"  # never clobbered


def test_init_unsupported_agent_exits_1(env: Path) -> None:
    result = runner.invoke(app, ["init", "--agent", "codex"])
    assert result.exit_code == 1
    assert "codex" in result.output
