"""Codex CLI backend (spike S1 / ADR-0001).

Shells out to ``codex exec --json <prompt>``, which streams JSONL events; the
model's answer is the ``text`` of the last ``item.completed`` event whose
``item.type == "agent_message"``. Runs strictly as the user's own logged-in
CLI (decision D9's ToS rule).

``--ignore-user-config`` is load-bearing, not an optimization: a live spike
(2026-07-20/21, see ``docs/notes/2026-07-17-claude-usage-runaway-incident.md``)
proved Codex does **not** propagate ``NEUROBASE_INTERNAL_CALL`` to hooks it
spawns for its own headless sessions, so the env-marker guard that protects
Claude gives Codex no protection at all. Hook wiring (both user- and
project-scoped) lives entirely in ``~/.codex/config.toml``; skipping that file
means Codex never discovers any hooks to fire, which a second live spike
confirmed suppresses capture even with the marker absent. Do not remove this
flag without an equivalent live re-verification.

**The call runs in an empty throwaway directory, never the caller's cwd** (G17).
A brain call is a pure function of its prompt; inheriting the caller's working
directory is a coupling with two costs, one of which was a live failure:

1. ``codex exec`` refuses to start outside a trusted directory
   (``Not inside a trusted directory and --skip-git-repo-check was not
   specified``), so a curate pass fired from a registered-but-non-git root
   died entirely. Live-verified 2026-08-07 with a control: the same command
   failed in a non-git registered root and succeeded one directory down in the
   git repo. ``--skip-git-repo-check`` is what clears that guard, and it cannot
   be replaced by marking the directory trusted in ``~/.codex/config.toml`` —
   ``--ignore-user-config`` above is precisely what stops that file being read.
2. Codex treats the cwd as its workspace. Without a fixed one, a curation call's
   workspace is whatever repository the user happened to be sitting in, which is
   a data boundary Neurobase otherwise takes seriously (ADR-0017's egress gate).
   An empty temporary directory gives the model nothing to read.

``-s read-only`` is paired with ``--skip-git-repo-check`` deliberately: the guard
being cleared is a safety guard, so the sandbox is tightened in the same breath.
No ``-s`` was passed before, meaning the policy was whatever Codex defaulted to;
naming it is strictly narrower. Curation is a text transform and needs no shell
command to succeed.

This is ADR-0025's harness-isolation principle ("the curator call must *be* a
curator call") applied to a dimension that ADR did not cover.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable

from neurobase.brain.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    BrainError,
    RetryableBrainError,
    call_with_retry,
    combine_prompt,
    parse_plan_json,
)
from neurobase.core.process_guard import internal_call_env

Runner = Callable[..., subprocess.CompletedProcess]


def _default_runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    # G17: run in an empty throwaway directory, never the caller's cwd. See the
    # module docstring — this is both the fix for a hard failure and a data
    # boundary. `TemporaryDirectory` (not `gettempdir()`) so the workspace is
    # genuinely empty rather than merely not-ours.
    with tempfile.TemporaryDirectory(prefix="neurobase-brain-") as workdir:
        return subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            timeout=timeout,
            env=internal_call_env(),
            cwd=workdir,
        )


def _last_agent_message(stdout: str) -> str | None:
    """Scan the JSONL event stream for the final agent message text."""
    answer: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # non-JSON banner lines; skip
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                answer = text
    return answer


class CodexCLIBrain:
    """`codex exec --json` backend. Uses the CLI's own model."""

    name = "codex-cli"

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        runner: Runner = _default_runner,
    ) -> None:
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._runner = runner

    def _once(self, prompt: str) -> str:
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "--json",
            prompt,
        ]
        try:
            proc = self._runner(cmd, timeout=self._timeout)
        except subprocess.TimeoutExpired as exc:
            raise RetryableBrainError("codex exec timed out") from exc
        except FileNotFoundError as exc:
            raise BrainError("codex CLI not found on PATH") from exc
        if proc.returncode != 0:
            raise BrainError(f"codex exec exited {proc.returncode}: {proc.stderr[-500:]}")
        answer = _last_agent_message(proc.stdout)
        if answer is None:
            raise RetryableBrainError("codex exec produced no agent_message event")
        return answer

    def text(self, system: str, user: str) -> str:
        prompt = combine_prompt(system, user)
        return call_with_retry(lambda: self._once(prompt))

    def plan_json(self, system: str, user: str) -> dict:
        prompt = combine_prompt(system, user)
        return call_with_retry(lambda: parse_plan_json(self._once(prompt)))
