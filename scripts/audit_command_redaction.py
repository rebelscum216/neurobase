"""Local telemetry for `redact_command` over real captured commands.

**This is anecdotal telemetry, not a check of any MUST.** It has no oracle: it
knows what *changed*, never what the output *should have been*. Read that
limitation before quoting any number from it.

What it structurally CANNOT catch (each has actually bitten):

- A **leak beside a redaction.** If one secret is replaced and another survives
  in the same command, the marker is present and this script calls it "redacted".
  That is exactly the round-7 `api_token=$(printf SECRET)` bug.
- A **deletion beside a redaction.** Same reason: a marker anywhere makes the
  command look handled, even if a delimiter vanished elsewhere.
- **Anything the corpus does not contain.** No real command here happens to quote
  an argument that *starts* with a secret-named assignment, so the round-8
  quoted-argument corruption round-tripped "clean" for weeks. Absence of evidence
  is not evidence.

The oracle lives in `tests/test_redact.py::test_command_redaction_exact_output`,
which asserts the *whole expected output* per syntax family — the only assertion
that catches a surviving secret and a lost delimiter at once. Treat this script
as a canary over real-world shapes the fixtures might not imagine, and treat the
security case as resting on the fail-open history in ADR-0013, not on a rate.

Method (so the numbers are reproducible rather than asserted):

- **Selection.** Every Bash `tool_use` block in every Claude transcript under
  ``~/.claude/projects``; the value is ``input.command``. Files that are not
  valid UTF-8 are skipped rather than lossily decoded, so selection is
  byte-faithful.
- **Deduplication.** Exact string dedup — sessions repeat commands, and
  duplicates would weight any rate arbitrarily.
- **Equality.** Byte equality of ``redact_command(cmd)`` against ``cmd``.
- **Idempotence.** ``redact_command(redact_command(cmd)) == redact_command(cmd)``.
  This one IS a real property, checkable without an oracle, and a violation means
  the scrubber is chewing its own output.

Output is aggregate counts only — no command text is ever printed.

    uv run python scripts/audit_command_redaction.py

The two counters that ARE invariants (`changed_without_marker`, `not_idempotent`)
are checked on every push by `tests/test_redact_audit.py`, which runs
`audit_commands` over the in-repo fixture tables. This corpus — real transcripts
under ``~/.claude/projects`` — exists on one developer's machine and on no CI
runner, which is why the gate cannot be this script and has to be that test.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from neurobase.core.redact import redact_command

MARKER = re.compile(r"\[REDACTED:[a-z-]+\]")
SECRET_NAMED_ASSIGNMENT = re.compile(
    r"""(?<![A-Za-z0-9_-])["']?[A-Za-z0-9_]*"""
    r"""(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9_]*["']?=""",
    re.IGNORECASE,
)


def bash_commands(root: Path) -> tuple[list[str], int]:
    commands: list[str] = []
    skipped = 0
    for transcript in sorted(root.rglob("*.jsonl")):
        try:
            lines = transcript.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            skipped += 1  # not lossily decoded — selection stays byte-faithful
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("name") != "Bash":
                    continue
                if block.get("type") != "tool_use":
                    continue
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and command:
                    commands.append(command)
    return commands, skipped


class AuditResult(NamedTuple):
    """Aggregate counts over an audited corpus. No command text, by construction.

    Only two of these fields are assertable without an oracle — see
    `audit_commands`. The rest are descriptive.
    """

    unique: int
    secret_shaped: int
    redacted: int
    changed_without_marker: int
    not_idempotent: int


def audit_commands(commands: Iterable[str]) -> AuditResult:
    """Run the audit's counters over any corpus of shell commands.

    Corpus-agnostic on purpose: `main()` feeds it the developer's real
    transcripts, and `tests/test_redact_audit.py` feeds it the in-repo fixture
    tables so the two properties below are checked on every push and every OS,
    rather than by hand on one machine. There is exactly one implementation.

    Of the five counters, exactly two are real invariants — the rest are
    telemetry with no oracle (see the module docstring):

    - `changed_without_marker` MUST be 0. Redaction that alters a command
      without leaving a marker is deleting captured input, not scrubbing it.
    - `not_idempotent` MUST be 0. Redaction runs more than once over the same
      text, so a second pass must be a no-op.

    Commands are deduplicated exactly, because sessions repeat commands and
    duplicates would weight any rate arbitrarily.
    """
    unique = sorted(set(commands))

    redacted = changed_without_marker = secret_shaped = not_idempotent = 0
    for command in unique:
        if SECRET_NAMED_ASSIGNMENT.search(command):
            secret_shaped += 1
        scrubbed = redact_command(command)
        if redact_command(scrubbed) != scrubbed:
            not_idempotent += 1
        if scrubbed == command:
            continue
        if MARKER.search(scrubbed):
            redacted += 1
        else:
            changed_without_marker += 1

    return AuditResult(
        unique=len(unique),
        secret_shaped=secret_shaped,
        redacted=redacted,
        changed_without_marker=changed_without_marker,
        not_idempotent=not_idempotent,
    )


def main() -> int:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        print(f"no transcripts at {root} — nothing to audit")
        return 0

    raw, skipped = bash_commands(root)
    result = audit_commands(raw)

    total = result.unique or 1
    print(f"transcripts skipped (not valid UTF-8) : {skipped}")
    print(f"unique commands audited              : {result.unique}")
    print(
        f"  carrying a secret-named assignment : {result.secret_shaped} "
        f"({100 * result.secret_shaped / total:.2f}%)   [descriptive only]"
    )
    print(f"  redacted (a marker appeared)       : {result.redacted}   [NOT proof it is correct]")
    print(f"  changed with NO marker             : {result.changed_without_marker}   [must be 0]")
    print(f"  not idempotent                     : {result.not_idempotent}   [must be 0]")
    print()
    print("This is telemetry, not verification — see the module docstring.")
    print("The oracle is tests/test_redact.py::test_command_redaction_exact_output.")
    return 1 if (result.changed_without_marker or result.not_idempotent) else 0


if __name__ == "__main__":
    raise SystemExit(main())
