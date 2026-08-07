"""Tests for the codex-cli backend, using a fake subprocess runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

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
    # Positional indexing into the flag block was brittle -- adding a flag broke
    # this test rather than the behavior it names. Assert the invariant instead:
    # the command opens with `codex exec`, and the prompt is the final argument.
    assert seen["cmd"][:2] == ["codex", "exec"]
    assert "--json" in seen["cmd"]
    assert "SYS" in seen["cmd"][-1] and "USER" in seen["cmd"][-1]


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


def test_invokes_with_skip_git_repo_check_because_cwd_may_not_be_a_repo() -> None:
    """G17. ``codex exec`` refuses to start outside a trusted directory, so a
    curate pass fired from a registered-but-non-git root died entirely
    (live-verified 2026-08-07, with a control that succeeded one directory down
    in the git repo). This flag is the only available remedy: marking the
    directory trusted in ~/.codex/config.toml cannot work, because
    ``--ignore-user-config`` is precisely what stops that file being read."""
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_jsonl(_agent_message("ok")))

    CodexCLIBrain(runner=runner).text("sys", "user")
    assert "--skip-git-repo-check" in seen["cmd"]


def test_invokes_with_read_only_sandbox() -> None:
    """G17. Paired with ``--skip-git-repo-check``: the guard being cleared is a
    safety guard, so the sandbox is named in the same breath. Before this, no
    ``-s`` was passed at all and the policy was whatever Codex defaulted to.
    Curation is a text transform and needs no shell command to succeed."""
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_jsonl(_agent_message("ok")))

    CodexCLIBrain(runner=runner).text("sys", "user")
    assert seen["cmd"][seen["cmd"].index("-s") + 1] == "read-only"


def test_default_runner_uses_an_empty_dir_not_the_callers_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G17. A brain call is a pure function of its prompt. Inheriting the
    caller's cwd made the call fail outside a git repo AND handed the model
    whatever repository the user was sitting in as its workspace. The directory
    must be explicit, must not be the caller's, and must be empty -- an
    unrelated-but-populated dir would still be readable."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        # `.get`, not `[...]`: when the mutation under test is "cwd was never
        # passed", this must fail on the named assertion below rather than
        # crashing here with a KeyError that says nothing about the contract.
        cwd = kwargs.get("cwd")
        # Listed while the context manager is still open, so the directory
        # exists and its emptiness is observable.
        seen["entries"] = os.listdir(cwd) if cwd else None
        return _proc(_jsonl(_agent_message("ok")))

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    codex_cli._default_runner(["codex", "exec", "prompt"], timeout=5)

    assert seen.get("cwd"), "the runner must set cwd explicitly, not inherit it"
    assert Path(seen["cwd"]).resolve() != Path.cwd().resolve()
    assert seen["entries"] == [], "the brain's workspace must be empty"


def test_default_runner_cleans_up_its_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The throwaway directory is throwaway -- one per call, removed after."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        return _proc(_jsonl(_agent_message("ok")))

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    codex_cli._default_runner(["codex", "exec", "prompt"], timeout=5)
    assert not Path(seen["cwd"]).exists()


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
