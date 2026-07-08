"""Tests for the Codex hook installer (spec §7): hooks.json ownership/idempotence
and the surgical ~/.codex/config.toml [projects.*] table merge."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from neurobase.adapters.codex import install

SHIM = "/abs/shim/neurobase"


# --- hooks.json -----------------------------------------------------------


def test_build_hooks_into_empty() -> None:
    result = install.build_hooks({}, SHIM)
    hooks = result["hooks"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == f"{SHIM} hook codex session-start"
    assert hooks["Stop"][0]["hooks"][0]["command"] == f"{SHIM} hook codex stop"
    # CamelCase events, no matcher key (unlike Claude's SessionStart).
    assert "matcher" not in hooks["SessionStart"][0]


def test_build_hooks_whole_file_wrapped_in_hooks_key() -> None:
    result = install.build_hooks({}, SHIM)
    assert set(result) == {"hooks"}


def test_build_hooks_preserves_foreign_events_and_keys() -> None:
    foreign = {"hooks": [{"type": "command", "command": "/usr/bin/other-tool"}]}
    existing = {"version": 1, "hooks": {"PreToolUse": [foreign]}}
    result = install.build_hooks(existing, SHIM)
    assert result["version"] == 1
    assert result["hooks"]["PreToolUse"] == [foreign]
    assert "SessionStart" in result["hooks"] and "Stop" in result["hooks"]


def test_build_hooks_idempotent() -> None:
    once = install.build_hooks({}, SHIM)
    twice = install.build_hooks(once, SHIM)
    assert install.render_hooks(once) == install.render_hooks(twice)


def test_build_hooks_replaces_owned_group_not_stacking() -> None:
    old_cmd = "/old/path/neurobase hook codex session-start"
    existing = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": old_cmd}]}]}}
    result = install.build_hooks(existing, SHIM)
    groups = result["hooks"]["SessionStart"]
    assert len(groups) == 1
    assert groups[0]["hooks"][0]["command"] == f"{SHIM} hook codex session-start"


def test_owned_marker_is_path_anchored_not_bare_substring() -> None:
    owned = {"hooks": [{"type": "command", "command": "/x/neurobase hook codex stop"}]}
    win_cmd = r"C:\tools\neurobase.exe hook codex session-start"
    owned_win = {"hooks": [{"type": "command", "command": win_cmd}]}
    # Prose mention — neurobase not preceded by a separator — is not ours.
    prose = {"hooks": [{"type": "command", "command": 'echo "run neurobase hook codex to set up"'}]}
    # The Claude subcommand is a different agent — not owned by the Codex installer.
    claude = {"hooks": [{"type": "command", "command": "/x/neurobase hook claude session-start"}]}
    # `hook codexX` must not match (word-boundary guard).
    codexx = {"hooks": [{"type": "command", "command": "/x/neurobase hook codexXYZ"}]}
    assert install._is_owned_group(owned)
    assert install._is_owned_group(owned_win)
    assert not install._is_owned_group(prose)
    assert not install._is_owned_group(claude)
    assert not install._is_owned_group(codexx)


def test_preserves_foreign_similar_command() -> None:
    foreign = {"hooks": [{"type": "command", "command": "/bin/echo neurobase is cool"}]}
    existing = {"hooks": {"Stop": [foreign]}}
    result = install.build_hooks(existing, SHIM)
    assert foreign in result["hooks"]["Stop"]
    assert any(
        g["hooks"][0]["command"] == f"{SHIM} hook codex stop" for g in result["hooks"]["Stop"]
    )


def test_load_hooks_missing_returns_empty(tmp_path: Path) -> None:
    assert install.load_hooks(tmp_path / "nope.json") == {}


def test_load_hooks_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(install.HooksParseError):
        install.load_hooks(path)


def test_hooks_json_path_scopes(tmp_path: Path) -> None:
    proj = install.hooks_json_path(user=False, cwd=tmp_path)
    assert proj == tmp_path / ".codex" / "hooks.json"
    user = install.hooks_json_path(user=True, cwd=tmp_path)
    assert user == Path.home() / ".codex" / "hooks.json"


def test_write_hooks_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".codex" / "hooks.json"
    doc = install.build_hooks({}, SHIM)
    install.write_hooks(path, doc)
    assert json.loads(path.read_text()) == doc
    assert path.read_text().endswith("\n")


# --- config.toml (surgical merge) -----------------------------------------

KEY = "/Users/dev/repo"


def _project(text: str, key: str = KEY) -> dict:
    return tomllib.loads(text)["projects"][key]


def test_merge_config_appends_to_empty() -> None:
    out = install.merge_config("", KEY)
    entry = _project(out)
    assert entry == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


def test_merge_config_preserves_comments_and_other_tables() -> None:
    existing = (
        "# my codex config\n"
        'model = "gpt-5"  # keep this comment\n'
        "\n"
        '[projects."/some/other/repo"]\n'
        'trust_level = "trusted"\n'
    )
    out = install.merge_config(existing, KEY)
    # Everything preserved verbatim...
    assert "# my codex config" in out
    assert 'model = "gpt-5"  # keep this comment' in out
    assert '[projects."/some/other/repo"]' in out
    # ...and our table added.
    parsed = tomllib.loads(out)
    assert parsed["model"] == "gpt-5"
    assert parsed["projects"]["/some/other/repo"]["trust_level"] == "trusted"
    assert _project(out) == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


def test_merge_config_updates_existing_table_in_place() -> None:
    # Table exists (trusted) but missing the hooks key + has a user comment.
    existing = (
        f'[projects."{KEY}"]\n'
        "# I trust this repo\n"
        'trust_level = "trusted"\n'
        'approved_commands = ["ls"]\n'
    )
    out = install.merge_config(existing, KEY)
    assert "# I trust this repo" in out  # comment preserved
    entry = _project(out)
    assert entry["hooks"] == ".codex/hooks.json"
    assert entry["trust_level"] == "trusted"
    assert entry["approved_commands"] == ["ls"]  # other key preserved


def test_merge_config_overwrites_wrong_hooks_value() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\nhooks = "wrong/path.json"\n'
    out = install.merge_config(existing, KEY)
    assert _project(out)["hooks"] == ".codex/hooks.json"


def test_merge_config_idempotent_noop_returns_verbatim() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\nhooks = ".codex/hooks.json"\n'
    out = install.merge_config(existing, KEY)
    assert out == existing  # byte-for-byte unchanged


def test_merge_config_does_not_touch_following_table() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\n\n[other]\nfoo = 1\n'
    out = install.merge_config(existing, KEY)
    parsed = tomllib.loads(out)
    assert parsed["other"] == {"foo": 1}  # untouched
    assert _project(out)["hooks"] == ".codex/hooks.json"
    # The hooks key landed inside our table, not the [other] table.
    assert "hooks" not in parsed["other"]


def test_merge_config_malformed_raises() -> None:
    with pytest.raises(install.ConfigParseError):
        install.merge_config('[projects."x"\n not valid', KEY)


def test_merge_config_escapes_special_path() -> None:
    weird = '/Users/dev/a "b"/repo'
    out = install.merge_config("", weird)
    assert tomllib.loads(out)["projects"][weird]["hooks"] == ".codex/hooks.json"


def test_merge_config_idempotent_with_escaped_quote_path() -> None:
    weird = '/Users/dev/a "b"/repo'
    once = install.merge_config("", weird)
    twice = install.merge_config(once, weird)
    assert twice == once


def test_load_config_text_missing_returns_empty(tmp_path: Path) -> None:
    assert install.load_config_text(tmp_path / "nope.toml") == ""


def test_load_config_text_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[unterminated\n", encoding="utf-8")
    with pytest.raises(install.ConfigParseError):
        install.load_config_text(path)
