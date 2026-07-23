"""Integration tests for `neurobase init --agent claude` (spec §7)."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.cli import app

runner = CliRunner()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _which_agents(name: str) -> str | None:
    return {
        "claude": "/usr/bin/claude",
        "codex": "/usr/bin/codex",
        "neurobase": "/usr/local/bin/neurobase",
    }.get(name)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the home dir (so config_path()/`--user` scope resolve into tmp,
    never the real home) and pin the backup store root. Returns the isolated
    home dir."""
    home = tmp_path / "home"
    home.mkdir()
    # HOME is honored by Path.home() on POSIX; Windows reads USERPROFILE. Set
    # both so user-scope agent-config paths isolate to tmp on every platform.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
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

    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo)], input="y\n")
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

    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo)], input="n\n")
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
    foreign: dict[str, Any] = {
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
    result = runner.invoke(app, ["init", "--agent", "gemini"])
    assert result.exit_code == 1
    assert "gemini" in result.output


def test_guided_init_yes_enables_repo_and_installs_detected_agents(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cli.shutil, "which", _which_agents)

    result = runner.invoke(app, ["init", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 0
    assert "Enabled project 'repo'" in result.output
    assert (tmp_path / "store" / "projects" / "repo" / "memory").is_dir()
    assert (repo / ".claude" / "settings.json").exists()
    assert (repo / ".codex" / "hooks.json").exists()
    assert (env / ".codex" / "config.toml").exists()


def test_guided_init_no_detected_agents_still_enables_repo(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)

    result = runner.invoke(app, ["init", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 0
    assert "Enabled project 'repo'" in result.output
    assert "No supported agents found" in result.output
    assert (tmp_path / "store" / "projects" / "repo" / "memory").is_dir()


def test_guided_init_refuses_newer_schema_without_registering(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1 closure (ADR-0015): init --guided must obtain the WRITE handle — and so
    run the schema guard — BEFORE register_project touches registry.toml, so a
    store newer than we support aborts without a partial mutation. Pre-fix, the
    guided path registered first and only hit the guard inside ensure_tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 1
    assert "schema" in result.output
    assert not (store_root / "registry.toml").exists()  # never partially mutated
    assert not (store_root / "projects" / "repo").exists()


def test_init_agent_claude_refuses_newer_schema_before_any_write(env: Path, tmp_path: Path) -> None:
    """G1 closure (ADR-0015 step 4d): the direct `init --agent claude` path must refuse
    a store whose schema is newer than we support — BEFORE it backs up or installs the
    hooks that would then capture into a store we cannot operate on (spec §10, D11).
    Pre-4d only the *guided* flow guarded; the `--agent` path backed up + installed."""
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = '{"model": "claude-opus-4-8"}\n'
    settings.write_text(original, encoding="utf-8")
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 1
    assert "schema" in result.output
    # Refused before touching anything: settings unchanged, no backup written into the store.
    assert settings.read_text(encoding="utf-8") == original
    assert not (store_root / "backups").exists()


def test_init_agent_codex_refuses_newer_schema(env: Path, tmp_path: Path) -> None:
    """The same 4d guard on the Codex installer (refuses at the top, before config
    parsing or the backup)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    store_root = tmp_path / "store"
    store_root.mkdir()
    (store_root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])

    assert result.exit_code == 1
    assert "schema" in result.output
    assert not (store_root / "backups").exists()


def test_init_agent_does_not_materialize_store_toml(env: Path, tmp_path: Path) -> None:
    """READ-not-WRITE (4d): installing hooks must refuse an *unsupported* store but must
    NOT *create* one — a successful `init --agent` against a not-yet-initialized store
    leaves `store.toml` absent (the store is created later, on first capture / `enable`).
    A WRITE handle would materialize it; this pins the mode choice behaviorally, for both
    installers. (The guided flow keeps WRITE because it enables a repo = real store writes.)"""
    store_toml = tmp_path / "store" / "store.toml"

    claude_repo = tmp_path / "claude_repo"
    claude_repo.mkdir()
    claude = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(claude_repo), "--yes"])
    assert claude.exit_code == 0
    assert not store_toml.exists()

    codex_repo = tmp_path / "codex_repo"
    codex_repo.mkdir()
    codex = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(codex_repo), "--yes"])
    assert codex.exit_code == 0
    assert not store_toml.exists()


# --- init --agent codex (spec §7) -----------------------------------------


def _codex_hook_commands(hooks_doc: dict) -> list[str]:
    cmds: list[str] = []
    for groups in hooks_doc.get("hooks", {}).values():
        for group in groups:
            for entry in group.get("hooks", []):
                cmds.append(entry["command"])
    return cmds


def test_init_codex_writes_hooks_and_config_and_backs_up(env: Path, tmp_path: Path) -> None:
    """Project scope, consent 'y' → hooks.json + config.toml written, existing
    config backed up, trust-gate reminder printed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    key = str(repo.resolve())
    # Pre-existing config.toml so the backup path is exercised.
    cfg = env / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('model = "gpt-5"\n', encoding="utf-8")

    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo)], input="y\n")
    assert result.exit_code == 0
    assert "Backed up" in result.output
    assert "approve the hook in Codex" in result.output  # trust-gate reminder
    assert "Takes effect next session" in result.output

    hooks_doc = json.loads((repo / ".codex" / "hooks.json").read_text())
    cmds = _codex_hook_commands(hooks_doc)
    assert any(c.endswith("hook codex session-start") for c in cmds)
    assert any(c.endswith("hook codex stop") for c in cmds)
    # CamelCase events on disk.
    assert set(hooks_doc["hooks"]) == {"SessionStart", "Stop"}

    config = tomllib.loads(cfg.read_text())
    assert config["model"] == "gpt-5"  # preserved
    assert config["projects"][key] == {
        "trust_level": "trusted",
        "hooks": ".codex/hooks.json",
    }

    manifests = list((tmp_path / "store" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1


def test_init_codex_project_scope_uses_git_root_from_subdir(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    _git("init", "-q", cwd=repo)

    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(nested), "--yes"])
    assert result.exit_code == 0

    assert (repo / ".codex" / "hooks.json").exists()
    assert not (nested / ".codex" / "hooks.json").exists()

    config = tomllib.loads((env / ".codex" / "config.toml").read_text())
    assert str(repo.resolve()) in config["projects"]
    assert str(nested.resolve()) not in config["projects"]


def test_init_codex_consent_no_aborts(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo)], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert not (repo / ".codex" / "hooks.json").exists()
    assert not (env / ".codex" / "config.toml").exists()
    assert not (tmp_path / "store" / "backups").exists()


def test_init_codex_user_scope_skips_projects_table_but_registers_mcp(
    env: Path, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["init", "--agent", "codex", "--user", "--yes"])
    assert result.exit_code == 0
    hooks_doc = json.loads((env / ".codex" / "hooks.json").read_text())
    assert any(c.endswith("hook codex session-start") for c in _codex_hook_commands(hooks_doc))
    # User scope: global hooks.json is auto-discovered — no [projects.*] table.
    # But the MCP server IS registered (user-scope, spec §13), so config.toml
    # now exists with the mcp_servers table and no projects table.
    config = tomllib.loads((env / ".codex" / "config.toml").read_text())
    assert "projects" not in config
    assert config["mcp_servers"]["neurobase"]["args"] == ["mcp", "serve"]


def test_init_claude_registers_mcp_server(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["init", "--agent", "claude", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 0
    mcp = json.loads((env / ".claude.json").read_text())
    entry = mcp["mcpServers"]["neurobase"]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["mcp", "serve"]


def test_init_codex_registers_mcp_server(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 0
    config = tomllib.loads((env / ".codex" / "config.toml").read_text())
    assert config["mcp_servers"]["neurobase"]["args"] == ["mcp", "serve"]


def test_init_codex_idempotent_second_run(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert second.exit_code == 0
    assert "already up to date" in second.output


def test_init_codex_preserves_existing_config(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = env / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '# user config\nmodel = "gpt-5"\n\n[projects."/other/repo"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 0
    text = cfg.read_text()
    assert "# user config" in text
    assert 'model = "gpt-5"' in text
    parsed = tomllib.loads(text)
    assert parsed["projects"]["/other/repo"]["trust_level"] == "trusted"
    assert parsed["projects"][str(repo.resolve())]["hooks"] == ".codex/hooks.json"


def test_init_codex_malformed_hooks_no_clobber(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    hooks = repo / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{ not valid json", encoding="utf-8")
    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 1
    assert hooks.read_text() == "{ not valid json"


def test_init_codex_malformed_config_no_clobber(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = env / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[unterminated\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "--agent", "codex", "--cwd", str(repo), "--yes"])
    assert result.exit_code == 1
    assert cfg.read_text() == "[unterminated\n"
