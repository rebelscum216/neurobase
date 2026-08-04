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

    Scope (review F5): `--setting-sources` selects among user/project/local and
    cannot deselect enterprise-MANAGED settings, so on a managed installation
    hooks, plugins and a policy CLAUDE.md still load. This test pins the argv
    contract, not a guarantee about managed machines — `doctor`'s "brain
    isolation" check is what surfaces that case.
    """
    seen = {}

    def runner(cmd, *, timeout):
        seen["cmd"] = cmd
        return _proc(_envelope("ok"))

    ClaudeCLIBrain(runner=runner).text("sys", "user")
    cmd = seen["cmd"]

    # No user/project/local CLAUDE.md, skills, plugins or hooks.
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    # No MCP servers at all — including Neurobase's own.
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}
    # No built-in tools. `--system-prompt` replaces the prompt but leaves Bash,
    # Edit and Read advertised — two of which the observed refusal cited as its
    # evidence. Review F1: without this the call is not isolated, and an
    # injection in the untrusted payload can spend the single turn on a tool
    # call, stalling curation at the same boundary this module repairs.
    assert cmd[cmd.index("--tools") + 1] == ""


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
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == ""


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


# --- token accounting (BrainUsage) ---------------------------------------
#
# Curation cost was previously unanswerable from any artifact: the envelope's
# `usage` block was parsed for `.result` and discarded. `models` is the
# load-bearing field — two probes of `claude -p` with identical flags reported
# different model ids, so "these two calls are comparable" needs evidence.


def _usage_envelope(
    result: str = "ok",
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 5_000,
    cache_creation: int = 0,
    cost: float = 0.01,
    model: str = "claude-opus-5[1m]",
) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": result,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
            "total_cost_usd": cost,
            "modelUsage": {model: {"inputTokens": input_tokens}},
        }
    )


def test_usage_accumulates_across_calls() -> None:
    def runner(cmd, *, timeout):
        return _proc(_usage_envelope())

    brain = ClaudeCLIBrain(runner=runner)
    brain.text("sys", "a")
    brain.text("sys", "b")

    assert brain.usage.calls == 2
    assert brain.usage.input_tokens == 200
    assert brain.usage.output_tokens == 40
    assert brain.usage.cache_read_input_tokens == 10_000
    assert brain.usage.cost_usd == pytest.approx(0.02)


def test_usage_records_which_model_served_each_call() -> None:
    """The reason this field exists: the CLI does not pin a model. A pass whose
    calls were served by two different models cannot support a same-model claim,
    and without this the mixture is invisible."""
    envelopes = iter(
        [
            _usage_envelope(model="claude-opus-5[1m]"),
            _usage_envelope(model="claude-opus-4-8[1m]"),
            _usage_envelope(model="claude-opus-5[1m]"),
        ]
    )

    def runner(cmd, *, timeout):
        return _proc(next(envelopes))

    brain = ClaudeCLIBrain(runner=runner)
    for _ in range(3):
        brain.text("sys", "user")

    assert brain.usage.models == {"claude-opus-5[1m]": 2, "claude-opus-4-8[1m]": 1}
    assert len(brain.usage.models) > 1  # the mixture is what a reader must see


def test_a_missing_or_malformed_usage_block_never_fails_the_call() -> None:
    """Accounting is observability, not correctness: an envelope that answers
    correctly but reports no usage must still return its answer. The pre-existing
    envelopes in this file have no `usage` key at all, which is the same path."""

    def runner(cmd, *, timeout):
        return _proc(
            json.dumps(
                {
                    "type": "result",
                    "is_error": False,
                    "result": "still fine",
                    "usage": "not-a-dict",
                    "total_cost_usd": "free",
                    "modelUsage": ["not", "a", "dict"],
                }
            )
        )

    brain = ClaudeCLIBrain(runner=runner)
    assert brain.text("sys", "user") == "still fine"
    assert brain.usage.calls == 1  # the call happened…
    assert brain.usage.input_tokens == 0  # …but nothing was claimed about it
    assert brain.usage.models == {}


def test_a_failed_attempt_is_not_counted() -> None:
    """`record()` runs after the success checks, so a non-zero exit contributes
    nothing — otherwise a retried call would inflate the totals and a failing
    backend would look expensive rather than broken."""

    def runner(cmd, *, timeout):
        return _proc("boom", stderr="nope", returncode=1)

    brain = ClaudeCLIBrain(runner=runner)
    with pytest.raises(BrainError):
        brain.text("sys", "user")
    assert brain.usage.calls == 0


def test_as_dict_is_json_safe_and_rounds_cost() -> None:
    def runner(cmd, *, timeout):
        return _proc(_usage_envelope(cost=0.0123456789))

    brain = ClaudeCLIBrain(runner=runner)
    brain.text("sys", "user")
    payload = brain.usage.as_dict()
    json.dumps(payload)  # must survive the journal writer
    assert payload["cost_usd"] == 0.012346
    assert payload["models"] == {"claude-opus-5[1m]": 1}
