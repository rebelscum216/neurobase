"""Runs `scripts/audit_command_redaction.py`'s property check inside pytest.

The script checks the redaction boundary's real invariants, but nothing ever
invoked it — not CI, not pytest, only a human remembering to. This module closes
that gap, and it is worth being precise about how much it actually buys, because
the script's own docstring is emphatic that it is **telemetry, not verification**:

- The script has **no oracle**. It knows what *changed*, never what the output
  *should have been*. That oracle is
  `tests/test_redact.py::test_command_redaction_exact_output` and stays there.
- Of its five counters, exactly **two are assertable properties**:
  `changed_without_marker` (redaction altered a command and left no marker — it
  DELETED captured input) and `not_idempotent` (a second pass is not a no-op).
- `test_redact.py` already asserts idempotence per sample, so that half is
  belt-and-braces. The genuinely new assertion here is
  **`changed_without_marker == 0`**, which was asserted nowhere in pytest.

The second thing this buys is running the **audit code path itself** on every
push, so the script cannot rot into a `NameError` that nobody discovers until
the next time someone runs it by hand.

Why the gate is the *repo* corpus and not the script: the script's corpus is
``~/.claude/projects``, one developer's real Claude transcripts. On a CI runner
that path does not exist, so the script prints "nothing to audit" and returns 0
— a silent no-op that would give false assurance. The in-repo fixture tables run
everywhere, so they are the gate. The real corpus is available opt-in at the
bottom of this file.

This module does not touch the redaction boundary. That boundary is LEXICAL by
hard-won decision (ADR-0013): every attempt at command-grammar or semantic
heuristics has failed open and leaked. Wiring an existing check into the gate is
not a licence to change what is checked.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from test_redact import (
    ALL_SAMPLES,
    EXACT,
    EXECUTED_STRING_RESIDUAL,
    NON_CREDENTIAL_OPTIONS,
    SHELL_LEAKS,
)

# `scripts/` is not a package and is not on `sys.path` under pytest — pytest
# prepends `tests/`, and the editable install contributes `src/`. Import the
# audit module by path so these tests exercise the REAL script code. Copying the
# property in here instead would create a second implementation, free to drift
# from the one the developer actually runs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_command_redaction as audit  # noqa: E402

# The widest in-repo corpus available: every fixture table in `test_redact.py`,
# plus the EXPECTED (already-scrubbed) outputs from the exact-output oracle.
# That last group matters — text that has already been through the scrubber once
# is precisely where the idempotence bug lived (`[REDACTED:env-secret]` carries
# no word-break character, so the value scanner read it as part of the next
# token and ate the following word).
REPO_CORPUS: list[str] = (
    ALL_SAMPLES
    + SHELL_LEAKS
    + NON_CREDENTIAL_OPTIONS
    + EXECUTED_STRING_RESIDUAL
    + [expected for _, expected in EXACT]
)


def test_repo_corpus_holds_the_two_real_invariants() -> None:
    """THE check this module exists to add, run on every push and every OS.

    `changed_without_marker == 0` is the new one: a scrubber that alters a
    command without leaving a marker has deleted captured input rather than
    redacted it, which spec §10 forbids outright. Nothing in pytest asserted it
    before — it was only ever observed by eye in the script's output.
    """
    result = audit.audit_commands(REPO_CORPUS)

    assert result.changed_without_marker == 0
    assert result.not_idempotent == 0


def test_repo_corpus_is_not_silently_empty() -> None:
    """Guards the test above against passing vacuously.

    Both assertions there are `== 0`, so an upstream rename that made the
    imported tables empty would turn the gate green while checking nothing. Pin
    a floor on the corpus size instead of trusting the import.
    """
    result = audit.audit_commands(REPO_CORPUS)

    assert result.unique > 100
    assert result.redacted > 10  # the corpus really does exercise the scrubber


# Field order is part of the contract these cases pin:
# (unique, secret_shaped, redacted, changed_without_marker, not_idempotent)
@pytest.mark.parametrize(
    ("corpus", "expected"),
    [
        pytest.param([], (0, 0, 0, 0, 0), id="empty-corpus"),
        pytest.param(["echo hello"], (1, 0, 0, 0, 0), id="unchanged"),
        pytest.param(["api_token=SECRET ./run"], (1, 1, 1, 0, 0), id="assignment-redacted"),
        # `secret_shaped` counts a *lexical assignment shape*, so a credential
        # OPTION is redacted without ever being counted as secret-shaped. The
        # counter is descriptive and undercounts by design; it is not the
        # invariant, and quoting it as a leak rate would be wrong.
        pytest.param(["pytest --api-key=SECRET"], (1, 0, 1, 0, 0), id="option-not-shape-counted"),
        # Secret-shaped but inert: a quoted argument is data and must survive
        # byte for byte, so this is counted, not redacted.
        pytest.param(["echo 'api_token=example'"], (1, 1, 0, 0, 0), id="shaped-but-data"),
        # Exact dedup — sessions repeat commands and duplicates would weight any
        # rate arbitrarily.
        pytest.param(["ls -la"] * 5, (1, 0, 0, 0, 0), id="deduplicated"),
    ],
)
def test_counters_are_pinned_on_synthetic_corpora(
    corpus: list[str], expected: tuple[int, int, int, int, int]
) -> None:
    """Pins each counter independently, so a refactor of the shared function
    cannot quietly swap two of them and still look plausible in the output."""
    assert tuple(audit.audit_commands(corpus)) == expected


def test_audit_catches_a_scrubber_that_changes_a_command_without_a_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An audit that cannot fail is not a check — so prove this one can.

    Simulates the regression `changed_without_marker` exists to catch: a
    scrubber that eats a delimiter and leaves nothing behind to say it did.
    Round 8's quoted-argument corruption had exactly this shape, and it
    round-tripped "clean" for weeks because no assertion looked for it.
    """
    monkeypatch.setattr(audit, "redact_command", lambda command: command.replace('"', ""))

    result = audit.audit_commands(['echo "hello"'])

    assert result.changed_without_marker == 1
    assert result.redacted == 0
    assert result.not_idempotent == 0  # stable, just lossy


def test_audit_catches_a_scrubber_that_is_not_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other failure mode, and the one property checkable without an oracle.

    Redaction runs more than once over the same text (per captured value, then
    over the whole assembled document as defense in depth), so a scrubber that
    chews its own output loses data on the second pass. Note that `redacted` is
    also 1 here: a marker DID appear, which is exactly why the script's docstring
    refuses to treat a marker as proof of correctness.
    """
    monkeypatch.setattr(audit, "redact_command", lambda command: command + "[REDACTED:test]")

    result = audit.audit_commands(["echo hello"])

    assert result.not_idempotent == 1
    assert result.redacted == 1


def test_audit_is_blind_to_a_deletion_beside_a_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the script docstring's central caveat, so the gate above is never
    mistaken for more than it is. This is a DOCUMENTED BLIND SPOT, not a bug.

    A marker anywhere in the output makes a command look handled. So a scrubber
    that redacts the value AND eats a delimiter elsewhere in the same command
    scores as `redacted`, with `changed_without_marker == 0` — both invariants
    stay green while captured input is being destroyed.

    This was measured, not assumed: replaying four historical failure modes over
    `REPO_CORPUS`, the round-8 quoted-argument corruption, a final-byte eater and
    a non-idempotent marker all trip an invariant; this one does not. What
    catches it is the exact-output oracle, which asserts the whole expected
    string —`test_command_redaction_exact_output` carries
    `("echo $((API_TOKEN=SECRET))", "echo $((API_TOKEN=[REDACTED:env-secret]))")`
    for precisely this reason. This module supplements that oracle; it cannot
    replace it, and adding counters here would not change that.
    """
    real = audit.redact_command

    def redacts_but_eats_the_closing_parens(command: str) -> str:
        return real(command).replace("))", ")")

    monkeypatch.setattr(audit, "redact_command", redacts_but_eats_the_closing_parens)

    result = audit.audit_commands(["echo $((API_TOKEN=SECRET))"])

    assert result.redacted == 1  # a marker appeared, so it looks handled …
    assert result.changed_without_marker == 0  # … and neither invariant fires
    assert result.not_idempotent == 0


def _event(**block: object) -> str:
    """One transcript line carrying a single content block."""
    return json.dumps({"message": {"content": [block]}})


def _bash(command: object) -> str:
    return _event(type="tool_use", name="Bash", input={"command": command})


def _tool(name: str, block_type: str = "tool_use") -> str:
    """A block carrying a `command` input that must NOT be selected."""
    return _event(type=block_type, name=name, input={"command": "x"})


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        pytest.param([_bash("echo one")], ["echo one"], id="bash-tool-use-selected"),
        # Selection is `input.command` of a Bash `tool_use` block and nothing
        # else — another tool's input is not a shell command, and auditing it
        # would pollute every rate the script prints.
        pytest.param([_tool("Read")], [], id="not-bash"),
        pytest.param([_tool("Bash", "tool_result")], [], id="not-tool-use"),
        # Transcripts are appended live and can be torn mid-write, so a
        # malformed line must not abort the audit of the rest of the file.
        pytest.param(["{not json", _bash("echo one")], ["echo one"], id="malformed-line-skipped"),
        pytest.param(["", _bash("echo one")], ["echo one"], id="blank-line-skipped"),
        # Shape defence: every one of these is a real transcript variant, and an
        # unguarded `.get()` chain would raise on it and kill the whole run.
        pytest.param([json.dumps({})], [], id="no-message"),
        pytest.param([json.dumps({"message": "text"})], [], id="message-not-a-dict"),
        pytest.param([json.dumps({"message": {"content": "text"}})], [], id="content-not-a-list"),
        pytest.param([json.dumps({"message": {"content": []}})], [], id="no-blocks"),
        pytest.param([json.dumps({"message": {"content": ["str"]}})], [], id="block-not-a-dict"),
        pytest.param([_event(type="tool_use", name="Bash")], [], id="no-input"),
        pytest.param([_bash(42)], [], id="command-not-a-string"),
        pytest.param([_bash("")], [], id="empty-command-dropped"),
    ],
)
def test_bash_commands_selects_only_bash_tool_use_commands(
    tmp_path: Path, lines: list[str], expected: list[str]
) -> None:
    """The selection rule is the script's stated method, so it is worth pinning:
    a change here silently changes what every printed number means."""
    (tmp_path / "session.jsonl").write_text("\n".join(lines), encoding="utf-8")

    commands, skipped = audit.bash_commands(tmp_path)

    assert commands == expected
    assert skipped == 0


def test_non_utf8_transcripts_are_skipped_not_lossily_decoded(tmp_path: Path) -> None:
    """Selection must stay byte-faithful. Decoding with `errors="replace"` would
    mutate the very command text being audited — the audit would then be
    reporting on strings that were never captured, and a mangled byte could
    destroy or manufacture a secret shape. Skipping is counted, not silent."""
    (tmp_path / "bad.jsonl").write_bytes(b'{"message": {"content": []}}\n\xff\xfe')
    (tmp_path / "good.jsonl").write_text(_bash("echo ok"), encoding="utf-8")

    commands, skipped = audit.bash_commands(tmp_path)

    assert commands == ["echo ok"]
    assert skipped == 1


def test_unreadable_transcripts_are_skipped_rather_than_aborting_the_run(tmp_path: Path) -> None:
    """The `OSError` arm. A directory named `*.jsonl` is the hermetic way to make
    `read_text` fail without `chmod` (which is a no-op for root, so it makes a
    flaky test in a container). One bad entry must not lose the whole corpus."""
    (tmp_path / "notafile.jsonl").mkdir()
    (tmp_path / "good.jsonl").write_text(_bash("echo ok"), encoding="utf-8")

    commands, skipped = audit.bash_commands(tmp_path)

    assert commands == ["echo ok"]
    assert skipped == 1


def test_bash_commands_walks_nested_projects_and_ignores_other_files(tmp_path: Path) -> None:
    """Transcripts live one directory per project, so selection recurses; and a
    sibling non-`.jsonl` file is not a transcript and must not be parsed."""
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-a" / "s.jsonl").write_text(_bash("echo a"), encoding="utf-8")
    (tmp_path / "proj-b" / "s.jsonl").write_text(_bash("echo b"), encoding="utf-8")
    (tmp_path / "proj-a" / "notes.md").write_text(_bash("echo ignored"), encoding="utf-8")

    commands, skipped = audit.bash_commands(tmp_path)

    assert sorted(commands) == ["echo a", "echo b"]
    assert skipped == 0


def test_main_returns_zero_when_there_is_no_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pins the exact behaviour that makes this script unusable AS a CI gate: on
    a runner there is no `~/.claude/projects`, so it prints and returns 0 without
    checking anything. Documented here so nobody "fixes" coverage by adding the
    script to `scripts/ci.py` and mistakes that no-op for assurance.

    `Path.home` is patched rather than `$HOME` because `Path.home()` reads
    `USERPROFILE` on Windows, where this suite also runs.
    """
    monkeypatch.setattr(audit.Path, "home", staticmethod(lambda: tmp_path))

    assert audit.main() == 0
    assert "nothing to audit" in capsys.readouterr().out


def test_main_exit_code_and_report_track_the_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: `main()` returns 1 iff an invariant is violated, and the
    report lines carry their `[must be 0]` annotations. Pinning the format keeps
    the extraction of `audit_commands` honest — the script is read by a human
    scanning those columns, so a reflowed line is a real regression."""
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(_bash("echo hello"), encoding="utf-8")
    monkeypatch.setattr(audit.Path, "home", staticmethod(lambda: tmp_path))

    assert audit.main() == 0
    healthy = capsys.readouterr().out
    assert "unique commands audited              : 1" in healthy
    assert "  changed with NO marker             : 0   [must be 0]" in healthy
    assert "  not idempotent                     : 0   [must be 0]" in healthy

    monkeypatch.setattr(audit, "redact_command", lambda command: command.replace("o", ""))

    assert audit.main() == 1
    assert "  changed with NO marker             : 1   [must be 0]" in capsys.readouterr().out


def test_main_never_prints_command_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output is aggregate counts only. The audit reads real transcripts, so a
    report that echoed a command would relocate a live credential into a
    terminal, a CI log, or a pasted bug report."""
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(_bash("api_token=hunter2 ./run"), encoding="utf-8")
    monkeypatch.setattr(audit.Path, "home", staticmethod(lambda: tmp_path))

    assert audit.main() == 0

    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert "api_token" not in out
    assert "./run" not in out


_REAL_TRANSCRIPTS = Path.home() / ".claude" / "projects"
_OPTED_IN = os.environ.get("NEUROBASE_AUDIT_REAL_TRANSCRIPTS") == "1"


@pytest.mark.skipif(
    not (_OPTED_IN and _REAL_TRANSCRIPTS.is_dir()),
    reason="opt-in: set NEUROBASE_AUDIT_REAL_TRANSCRIPTS=1 with ~/.claude/projects present",
)
def test_real_transcript_corpus_holds_the_invariants() -> None:
    """The original audit, as a test — opt-in, and never run by default.

    Reading the developer's real transcripts is a side effect no test should
    have without being asked, so this is gated on an env var AND on the corpus
    existing. A CI runner can satisfy neither, which is exactly why the
    repo-corpus test above is the actual gate and this is a canary.

    What it adds over the fixtures is real-world SHAPES nobody thought to write
    down: the round-8 quoted-argument corruption and a substitution scanner that
    ate a command's final byte were both found this way, not by a unit test.
    Only counts are asserted — no command text reaches the failure output.
    """
    commands, _ = audit.bash_commands(_REAL_TRANSCRIPTS)
    result = audit.audit_commands(commands)

    assert result.changed_without_marker == 0
    assert result.not_idempotent == 0
