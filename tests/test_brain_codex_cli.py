"""Tests for the codex-cli backend, using a fake subprocess runner."""

from __future__ import annotations

import json
import subprocess

import pytest

from neurobase.brain import codex_cli
from neurobase.brain.base import BrainError
from neurobase.brain.codex_cli import CodexCLIBrain
from neurobase.core.process_guard import INTERNAL_CALL_ENV


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["codex"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


def _agent_message(text: str) -> dict:
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


def test_text_returns_last_agent_message() -> None:
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "x"},
        {"type": "turn.started"},
        _agent_message("the answer"),
        {"type": "turn.completed"},
    )

    def runner(cmd, *, timeout):
        return _proc(stdout)

    assert CodexCLIBrain(runner=runner).text("sys", "user") == "the answer"


def test_uses_last_agent_message_when_multiple() -> None:
    stdout = _jsonl(_agent_message("first"), _agent_message("second"))

    def runner(cmd, *, timeout):
        return _proc(stdout)

    assert CodexCLIBrain(runner=runner).text("sys", "user") == "second"


def test_plan_json_parses_agent_message() -> None:
    stdout = _jsonl(_agent_message('{"upserts": [], "tombstones": []}'))

    def runner(cmd, *, timeout):
        return _proc(stdout)

    assert CodexCLIBrain(runner=runner).plan_json("sys", "user") == {
        "upserts": [],
        "tombstones": [],
    }


def test_skips_non_json_banner_lines() -> None:
    stdout = "Reading additional input from stdin...\n" + _jsonl(_agent_message("ok"))

    def runner(cmd, *, timeout):
        return _proc(stdout)

    assert CodexCLIBrain(runner=runner).text("sys", "user") == "ok"


def test_invokes_expected_command() -> None:
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_jsonl(_agent_message("ok")))

    CodexCLIBrain(runner=runner).text("SYS", "USER")
    assert seen["cmd"][:4] == ["codex", "exec", "--ignore-user-config", "--json"]
    assert "SYS" in seen["cmd"][4] and "USER" in seen["cmd"][4]


def test_invokes_with_ignore_user_config_to_suppress_hook_reentrancy() -> None:
    """A live spike (2026-07-20/21) proved Codex does not propagate
    NEUROBASE_INTERNAL_CALL to hooks it spawns for its own sessions, unlike
    Claude. ``--ignore-user-config`` skips loading ~/.codex/config.toml, where
    all hook wiring lives, so Codex never discovers a hook to fire -- a live
    spike confirmed this suppresses capture. This flag must always be present;
    removing it reopens the incident's exact recursive-capture failure mode
    for Codex."""
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_jsonl(_agent_message("ok")))

    CodexCLIBrain(runner=runner).text("sys", "user")
    assert "--ignore-user-config" in seen["cmd"]


def test_default_runner_marks_agent_process_as_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return _proc(_jsonl(_agent_message("ok")))

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    codex_cli._default_runner(["codex", "exec", "prompt"], timeout=5)
    assert seen["env"][INTERNAL_CALL_ENV] == "1"


def test_no_agent_message_retries_then_gives_up() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        return _proc(_jsonl({"type": "turn.completed"}))

    with pytest.raises(BrainError):
        CodexCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 2


def test_nonzero_exit_raises_brain_error() -> None:
    def runner(cmd, *, timeout):
        return _proc(stderr="not trusted", returncode=1)

    with pytest.raises(BrainError, match="exited 1"):
        CodexCLIBrain(runner=runner).text("sys", "user")


def test_timeout_retries() -> None:
    calls = []

    def runner(cmd, *, timeout):
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(BrainError, match="timed out"):
        CodexCLIBrain(runner=runner).text("sys", "user")
    assert len(calls) == 2
