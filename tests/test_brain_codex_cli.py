"""Tests for the codex-cli backend, using a fake subprocess runner."""

from __future__ import annotations

import json
import subprocess

import pytest

from neurobase.brain.base import BrainError
from neurobase.brain.codex_cli import CodexCLIBrain


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
    assert seen["cmd"][:3] == ["codex", "exec", "--json"]
    assert "SYS" in seen["cmd"][3] and "USER" in seen["cmd"][3]


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
