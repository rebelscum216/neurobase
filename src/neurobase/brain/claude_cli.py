"""Claude Code CLI backend (spike S5 / ADR-0002).

Shells out to ``claude -p <user> --system-prompt <system>`` with the harness
stripped (see ``_ISOLATION_ARGS``), ``--output-format json --max-turns 1``; the
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
    parse_plan_json,
)
from neurobase.core.process_guard import internal_call_env

Runner = Callable[..., subprocess.CompletedProcess]

# Harness isolation (G5, 2026-07-29). By default `claude -p` is headless only in
# the sense that no human is watching: it still loads MCP servers (including
# Neurobase's own), skills, CLAUDE.md, and Claude Code's agent system prompt. A
# legitimate curator call was therefore indistinguishable *from the inside* from
# a curator prompt replayed into an interactive session — which is the
# 2026-07-18 misfire — and the model refused the real call on exactly that
# ground, citing the tools it could see. A prompt cannot argue a model out of its
# own observations, so the call is made to actually be what it claims:
#
#   --system-prompt        the curator prompt occupies the system slot and
#                          REPLACES Claude Code's agent prompt, rather than being
#                          folded into a user turn by `combine_prompt`.
#   --setting-sources ""   no CLAUDE.md, skills, plugins, or hooks.
#   --strict-mcp-config    with an empty --mcp-config: no MCP servers at all.
#
# Verified live against the real 52-fact payload that reproduced G5: refusals
# stopped and the curator planned normally. This is also defense in depth for the
# 2026-07-17 runaway — a brain call with no MCP and no project hooks has far less
# machinery available to re-enter Neurobase.
#
# Codex has the same class of protection via `--ignore-user-config`
# (`codex_cli.py`); this is the Claude-side equivalent, and like that flag it
# must always be present.
_ISOLATION_ARGS = [
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
]


def _default_runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input="",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=internal_call_env(),
    )


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

    def _once(self, system: str, user: str) -> str:
        """One attempt: returns the model's answer string from ``.result``.
        Raises ``RetryableBrainError`` on timeout / bad envelope, ``BrainError``
        on a non-zero CLI exit.

        ``system`` and ``user`` stay in their own argv slots — unlike the Codex
        backend, which still folds them with ``combine_prompt`` — because
        occupying the real system slot is half of the G5 isolation above.
        """
        cmd = [
            "claude",
            "-p",
            user,
            "--system-prompt",
            system,
            *_ISOLATION_ARGS,
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
        return call_with_retry(lambda: self._once(system, user))

    def plan_json(self, system: str, user: str) -> dict:
        return call_with_retry(lambda: parse_plan_json(self._once(system, user)))
