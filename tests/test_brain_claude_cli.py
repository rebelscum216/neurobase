"""Tests for the claude-cli backend, using a fake subprocess runner."""

from __future__ import annotations

import json
import subprocess

import pytest

from neurobase.brain import claude_cli
from neurobase.brain.base import BrainError
from neurobase.brain.claude_cli import ClaudeCLIBrain
from neurobase.core.process_guard import INTERNAL_CALL_ENV


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _envelope(result: str, *, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "is_error": is_error, "result": result})


def test_text_returns_result_string() -> None:
    def runner(cmd, *, timeout):
        return _proc(_envelope("hello from claude"))

    brain = ClaudeCLIBrain(runner=runner)
    assert brain.text("sys", "user") == "hello from claude"


def test_plan_json_parses_result() -> None:
    def runner(cmd, *, timeout):
        return _proc(_envelope('{"upserts": [], "tombstones": []}'))

    brain = ClaudeCLIBrain(runner=runner)
    assert brain.plan_json("sys", "user") == {"upserts": [], "tombstones": []}


def test_plan_json_tolerates_fences_in_result() -> None:
    def runner(cmd, *, timeout):
        return _proc(_envelope('```json\n{"a": 1}\n```'))

    brain = ClaudeCLIBrain(runner=runner)
    assert brain.plan_json("sys", "user") == {"a": 1}


def test_invokes_expected_command() -> None:
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_envelope("ok"))

    ClaudeCLIBrain(runner=runner).text("SYS", "USER")
    assert seen["cmd"][:2] == ["claude", "-p"]
    assert "--output-format" in seen["cmd"] and "json" in seen["cmd"]
    assert "--max-turns" in seen["cmd"]
    # System and user occupy separate argv slots (G5): the user prompt is the
    # `-p` value and the system prompt REPLACES Claude Code's agent prompt.
    # They are no longer folded together by `combine_prompt`.
    assert seen["cmd"][2] == "USER"
    assert seen["cmd"][seen["cmd"].index("--system-prompt") + 1] == "SYS"


def test_invokes_with_harness_isolation_so_the_call_is_what_it_claims_to_be() -> None:
    """G5 (2026-07-29): `claude -p` loads MCP servers, skills and CLAUDE.md by
    default, so a legitimate curator call looked — from the inside — exactly
    like a curator prompt replayed into an interactive session. The model
    refused the real call on that ground, citing the tools it could see, and no
    amount of prompt wording talks a model out of its own observations.

    These flags make the claim true rather than merely asserted. Verified live
    against the real payload that reproduced G5. They must always be present:
    removing any of them reopens the refusal, and weakens the 2026-07-17
    runaway containment by handing the brain call MCP and hooks again. This is
    the Claude-side counterpart to Codex's `--ignore-user-config`.
    """
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_envelope("ok"))

    ClaudeCLIBrain(runner=runner).text("sys", "user")
    cmd = seen["cmd"]

    # No CLAUDE.md, skills, plugins or hooks.
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    # No MCP servers at all — including Neurobase's own.
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}


def test_plan_json_is_isolated_too_not_just_text() -> None:
    """The refusal was observed on the *plan* call, so the path that actually
    broke must carry the isolation — not only `text`, which distillation uses.
    """
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_envelope('{"upserts": [], "tombstones": []}'))

    ClaudeCLIBrain(runner=runner).plan_json("SYS", "USER")

    assert seen["cmd"][seen["cmd"].index("--system-prompt") + 1] == "SYS"
    assert "--strict-mcp-config" in seen["cmd"]


def test_default_runner_marks_agent_process_as_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return _proc(_envelope("ok"))

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    claude_cli._default_runner(["claude", "-p", "prompt"], timeout=5)
    assert seen["env"][INTERNAL_CALL_ENV] == "1"


def test_nonzero_exit_raises_brain_error() -> None:
    def runner(cmd, *, timeout):
        return _proc(stderr="boom", returncode=1)

    with pytest.raises(BrainError, match="exited 1"):
        ClaudeCLIBrain(runner=runner).text("sys", "user")


def test_timeout_retries_then_gives_up() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(BrainError, match="timed out"):
        ClaudeCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 2  # retried once


def test_bad_envelope_retries_then_gives_up() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        return _proc("not json")

    with pytest.raises(BrainError):
        ClaudeCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 2


def test_is_error_envelope_retries() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        return _proc(_envelope("", is_error=True))

    with pytest.raises(BrainError):
        ClaudeCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 2


def test_missing_binary_raises_brain_error_without_retry() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        raise FileNotFoundError("claude")

    with pytest.raises(BrainError, match="not found"):
        ClaudeCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 1  # not retryable
