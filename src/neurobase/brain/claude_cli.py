"""Claude Code CLI backend (spike S5 / ADR-0002).

Shells out to ``claude -p <prompt> --output-format json --max-turns 1``; the
model's answer is the string in the envelope's ``.result`` field (spec §11.3).
Runs strictly as the user's own logged-in CLI — Neurobase never touches
credentials (decision D9's ToS rule).
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

Runner = Callable[..., subprocess.CompletedProcess]


def _default_runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input="", capture_output=True, text=True, timeout=timeout)


class ClaudeCLIBrain:
    """`claude -p` backend. CLI backends use the CLI's own model, so no model
    override is passed (spec §10 config note)."""

    name = "claude-cli"

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
        """One attempt: returns the model's answer string from ``.result``.
        Raises ``RetryableBrainError`` on timeout / bad envelope, ``BrainError``
        on a non-zero CLI exit."""
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--max-turns",
            "1",
        ]
        try:
            proc = self._runner(cmd, timeout=self._timeout)
        except subprocess.TimeoutExpired as exc:
            raise RetryableBrainError("claude -p timed out") from exc
        except FileNotFoundError as exc:
            raise BrainError("claude CLI not found on PATH") from exc
        if proc.returncode != 0:
            raise BrainError(f"claude -p exited {proc.returncode}: {proc.stderr[-500:]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RetryableBrainError("claude -p envelope was not JSON") from exc
        if envelope.get("is_error"):
            raise RetryableBrainError(f"claude -p reported is_error: {envelope.get('subtype')}")
        result = envelope.get("result")
        if not isinstance(result, str):
            raise RetryableBrainError("claude -p envelope had no string .result")
        return result

    def text(self, system: str, user: str) -> str:
        prompt = combine_prompt(system, user)
        return call_with_retry(lambda: self._once(prompt))

    def plan_json(self, system: str, user: str) -> dict:
        prompt = combine_prompt(system, user)
        return call_with_retry(lambda: parse_plan_json(self._once(prompt)))
