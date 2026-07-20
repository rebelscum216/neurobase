"""Codex CLI backend (spike S1 / ADR-0001).

Shells out to ``codex exec --json <prompt>``, which streams JSONL events; the
model's answer is the ``text`` of the last ``item.completed`` event whose
``item.type == "agent_message"``. Runs strictly as the user's own logged-in
CLI (decision D9's ToS rule).
"""

from __future__ import annotations

import json
import subprocess
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
    return subprocess.run(
        cmd,
        input="",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=internal_call_env(),
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
        cmd = ["codex", "exec", "--json", prompt]
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
