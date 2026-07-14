"""Local, privacy-safe audit of `redact_command` against real captured commands.

This is **anecdotal local validation**, not a general guarantee — see spec §10.
It can show "nothing in *this* corpus is mangled"; it cannot show the scanner is
universally lossless, and it never establishes that any percentage generalizes
across shells, projects, or agent workflows. The security case for the
fail-closed rule rests on the concrete fail-open history (seven revisions, each
leaking), not on a rate.

Method (so a number can be reproduced rather than asserted):

- **Selection.** Every Bash `tool_use` block in every Claude transcript under
  ``~/.claude/projects``; the value is ``input.command``.
- **Deduplication.** Exact string dedup, because agent sessions repeat commands
  and duplicates would weight the rate arbitrarily.
- **Equality.** Byte equality of ``redact_command(cmd)`` against ``cmd``.
- **Buckets.** ``redacted`` = changed AND carries a ``[REDACTED:…]`` marker.
  ``mangled`` = changed with NO marker, i.e. captured input was altered or
  deleted without redacting anything. **`mangled` must be 0** — that is §10's
  "redaction must never delete captured input" MUST, checked against reality.

Output is aggregate counts only. No command text is printed.

    uv run python scripts/audit_command_redaction.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from neurobase.core.redact import redact_command

MARKER = re.compile(r"\[REDACTED:[a-z-]+\]")
SECRET_NAMED_ASSIGNMENT = re.compile(
    r"""(?<![A-Za-z0-9_-])["']?[A-Za-z0-9_]*"""
    r"""(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9_]*["']?=""",
    re.IGNORECASE,
)


def bash_commands(root: Path) -> list[str]:
    commands: list[str] = []
    for transcript in sorted(root.rglob("*.jsonl")):
        try:
            lines = transcript.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
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
    return commands


def main() -> int:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        print(f"no transcripts at {root} — nothing to audit")
        return 0

    unique = sorted(set(bash_commands(root)))
    redacted = mangled = secret_shaped = 0
    for command in unique:
        if SECRET_NAMED_ASSIGNMENT.search(command):
            secret_shaped += 1
        scrubbed = redact_command(command)
        if scrubbed == command:
            continue
        if MARKER.search(scrubbed):
            redacted += 1
        else:
            mangled += 1

    total = len(unique) or 1
    print(f"unique commands audited : {len(unique)}")
    print(f"  redacted              : {redacted}")
    print(
        f"  contain a secret-named assignment token : {secret_shaped} "
        f"({100 * secret_shaped / total:.2f}%)"
    )
    print(f"  MANGLED (changed, nothing redacted)     : {mangled}")
    return 1 if mangled else 0


if __name__ == "__main__":
    raise SystemExit(main())
