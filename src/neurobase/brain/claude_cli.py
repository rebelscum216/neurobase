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
#   --setting-sources ""   no user/project/local CLAUDE.md, skills, plugins or
#                          hooks. NOT the managed scope — see the caveat below.
#   --strict-mcp-config    with an empty --mcp-config: no MCP servers at all.
#   --tools ""             no built-in tools — no Bash, Edit, Read, the lot.
#
# SCOPE — this isolation covers UNMANAGED installations (review F5).
# `--setting-sources` selects among `user,project,local`; it cannot deselect
# enterprise-**managed** settings, which outrank all three, cannot be overridden
# from the command line, and may carry hooks, force-enabled plugins and a policy
# CLAUDE.md that load into the brain call anyway. On such a machine the harness
# is NOT fully stripped and G5's refusal fix is unverified. `doctor`'s "brain
# isolation" check reports when a managed settings file is present rather than
# leaving it silent; it deliberately does not fail closed, because refusing every
# brain call on a managed machine would disable curation to prevent a hazard that
# may not be there. Closing this properly needs an isolation boundary managed
# policy cannot populate, which is out of scope here.
#
# `--tools ""` is load-bearing, not garnish (review F1, 2026-07-29). The first
# version of this change omitted it on the reasoning that `--system-prompt`
# already made the call look like what it is; it does not — it replaces the
# prompt and leaves every built-in tool advertised. Bash and Edit are two of the
# exact observations the refusal cited. It also closes a reachable failure path
# rather than merely hardening: `curated_facts` and `raw_captures` are untrusted
# model-authored input, so an injection that persuades the model to spend its one
# allowed turn on a tool call leaves curation stalled at the same brain boundary
# this module exists to repair. A pure transform has no business holding Bash.
#
# CLI coupling, stated because it is a real dependency: these four flags are the
# contract as of Claude Code 2.1.218. A future CLI that renames or drops one
# should fail loudly here rather than silently restore the harness.
#
# Verified live against the real 52-fact payload that reproduced G5 (refusals
# stopped, the curator planned normally) and against an adversarial payload whose
# raw body asks the model to use a tool. This is also defense in depth for the
# 2026-07-17 runaway — a brain call with no MCP, no hooks and no tools has very
# little machinery available to re-enter Neurobase.
#
# Codex has the same class of protection via `--ignore-user-config`
# (`codex_cli.py`); this is the Claude-side equivalent, and like that flag every
# one of these must always be present.
_ISOLATION_ARGS = [
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
    "--tools",
    "",
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
