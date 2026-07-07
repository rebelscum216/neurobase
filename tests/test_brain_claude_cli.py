"""Tests for the claude-cli backend, using a fake subprocess runner."""

from __future__ import annotations

import json
import subprocess

import pytest

from neurobase.brain.base import BrainError
from neurobase.brain.claude_cli import ClaudeCLIBrain


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
    # system + user folded into the single prompt arg.
    assert "SYS" in seen["cmd"][2] and "USER" in seen["cmd"][2]


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
