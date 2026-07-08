"""Tests for MCP server registration in the agent installers (Phase 7 WS-D).

User-scope registration: Claude ``~/.claude.json`` mcpServers.neurobase; Codex
``~/.codex/config.toml`` [mcp_servers.neurobase]. Ownership is the reserved
``neurobase`` name; everything else in each file is preserved.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install

SHIM = "/abs/bin/neurobase"


# --- Claude (~/.claude.json) ---------------------------------------------


def test_claude_build_registers_server_and_preserves_other_keys() -> None:
    existing = {"userID": "abc", "mcpServers": {"other": {"command": "x"}}}
    result = claude_install.build_mcp_config(existing, SHIM)
    assert result["mcpServers"]["neurobase"] == {
        "type": "stdio",
        "command": SHIM,
        "args": ["mcp", "serve"],
        "env": {},
    }
    assert result["mcpServers"]["other"] == {"command": "x"}  # untouched
    assert result["userID"] == "abc"  # unrelated keys preserved


def test_claude_build_is_idempotent() -> None:
    once = claude_install.build_mcp_config({}, SHIM)
    twice = claude_install.build_mcp_config(once, SHIM)
    assert claude_install.render(once) == claude_install.render(twice)


def test_claude_remove_deletes_only_neurobase() -> None:
    existing = {"mcpServers": {"neurobase": {"command": SHIM}, "other": {"command": "x"}}}
    result = claude_install.remove_mcp_config(existing)
    assert result["mcpServers"] == {"other": {"command": "x"}}


def test_claude_remove_drops_empty_mcpservers_key() -> None:
    existing = {"userID": "abc", "mcpServers": {"neurobase": {"command": SHIM}}}
    result = claude_install.remove_mcp_config(existing)
    assert "mcpServers" not in result
    assert result["userID"] == "abc"


def test_claude_remove_is_noop_without_registration() -> None:
    existing = {"mcpServers": {"other": {"command": "x"}}}
    assert claude_install.remove_mcp_config(existing) == existing


def test_claude_load_mcp_config_absent_and_malformed(tmp_path) -> None:
    assert claude_install.load_mcp_config(tmp_path / "nope.json") == {}
    bad = tmp_path / ".claude.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(claude_install.SettingsParseError):
        claude_install.load_mcp_config(bad)


def test_claude_is_mcp_registered() -> None:
    reg = claude_install.build_mcp_config({}, SHIM)
    assert claude_install.is_mcp_registered(reg) is True
    assert claude_install.is_mcp_registered(reg, shim=SHIM) is True
    assert claude_install.is_mcp_registered(reg, shim="/other") is False
    assert claude_install.is_mcp_registered({}) is False


def test_claude_is_mcp_registered_rejects_stale_launch_shape() -> None:
    # Right command but wrong args / type ⇒ not a valid registration for the
    # current shim (would not start the server), but still "present".
    stale_args = {"mcpServers": {"neurobase": {"type": "stdio", "command": SHIM, "args": ["bad"]}}}
    assert claude_install.is_mcp_registered(stale_args, shim=SHIM) is False
    assert claude_install.is_mcp_registered(stale_args) is True
    stale_type = {"mcpServers": {"neurobase": {"command": SHIM, "args": ["mcp", "serve"]}}}
    assert claude_install.is_mcp_registered(stale_type, shim=SHIM) is False


# --- Codex (~/.codex/config.toml) ----------------------------------------


def test_codex_merge_registers_table_and_parses() -> None:
    result = codex_install.merge_mcp_config("", SHIM)
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["neurobase"] == {"command": SHIM, "args": ["mcp", "serve"]}


def test_codex_merge_preserves_other_content() -> None:
    existing = '[projects."/x"]\ntrust_level = "trusted"\n\n[mcp_servers.other]\ncommand = "y"\n'
    result = codex_install.merge_mcp_config(existing, SHIM)
    parsed = tomllib.loads(result)
    assert parsed["projects"]["/x"]["trust_level"] == "trusted"
    assert parsed["mcp_servers"]["other"] == {"command": "y"}
    assert parsed["mcp_servers"]["neurobase"]["command"] == SHIM


def test_codex_merge_is_idempotent() -> None:
    once = codex_install.merge_mcp_config("", SHIM)
    assert codex_install.merge_mcp_config(once, SHIM) == once


def test_codex_merge_updates_stale_entry_without_duplicating() -> None:
    stale = "[mcp_servers.neurobase]\ncommand = \"/old/neurobase\"\nargs = [\"mcp\", \"serve\"]\n"
    result = codex_install.merge_mcp_config(stale, SHIM)
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["neurobase"]["command"] == SHIM
    assert result.count("[mcp_servers.neurobase]") == 1  # no duplicate table


def test_codex_remove_drops_table_and_preserves_others() -> None:
    existing = codex_install.merge_mcp_config('[mcp_servers.other]\ncommand = "y"\n', SHIM)
    result = codex_install.remove_mcp_config(existing)
    parsed = tomllib.loads(result)
    assert "neurobase" not in parsed.get("mcp_servers", {})
    assert parsed["mcp_servers"]["other"] == {"command": "y"}


def test_codex_remove_is_noop_without_registration() -> None:
    existing = '[mcp_servers.other]\ncommand = "y"\n'
    assert codex_install.remove_mcp_config(existing) == existing


def test_codex_merge_escapes_windows_shim_path() -> None:
    win = r"C:\tools\neurobase.exe"
    result = codex_install.merge_mcp_config("", win)
    parsed = tomllib.loads(result)  # must still parse — backslashes escaped
    assert parsed["mcp_servers"]["neurobase"]["command"] == win


def test_codex_is_mcp_registered() -> None:
    reg = codex_install.merge_mcp_config("", SHIM)
    assert codex_install.is_mcp_registered(reg) is True
    assert codex_install.is_mcp_registered(reg, shim=SHIM) is True
    assert codex_install.is_mcp_registered(reg, shim="/other") is False
    assert codex_install.is_mcp_registered("") is False
    assert codex_install.is_mcp_registered("{ broken toml") is False


def test_codex_is_mcp_registered_rejects_stale_launch_shape() -> None:
    stale = '[mcp_servers.neurobase]\ncommand = "%s"\nargs = ["bad"]\n' % SHIM
    assert codex_install.is_mcp_registered(stale, shim=SHIM) is False
    assert codex_install.is_mcp_registered(stale) is True  # still present
