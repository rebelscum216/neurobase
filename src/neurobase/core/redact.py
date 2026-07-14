"""Secret redaction (decision D13; contractual patterns in spec §10).

Runs over every captured body before it touches ``raw/``. The
``[REDACTED:<type>]`` vocabulary is closed for the built-in table;
``extra_patterns`` (config ``[redact].extra_patterns``) always redact to
``[REDACTED:custom]``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GENERIC_API_KEY = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{20,}\b")
_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_GITHUB_TOKEN = re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")
_SECRET_NAME = r"[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*"
# `.env`-style line: NAME=value, at the start of a line. `[ \t]*` (not `\s*`)
# so the rule can never eat a newline, and the leading indent is *captured* and
# re-emitted — a body's structural indentation must survive redaction (spec §4).
_ENV_SECRET = re.compile(
    rf"^([ \t]*)({_SECRET_NAME})[ \t]*=[ \t]*\S+",
    re.MULTILINE | re.IGNORECASE,
)
# ANY secret-named assignment, case-insensitive, anywhere in the string. Only
# ever applied to text already known to be a shell command — never globally,
# where it would eat `sort(key=…)`. See `_SHELL_SEGMENT` and `redact_command`.
_ANY_SECRET_ASSIGNMENT = re.compile(
    rf"(?<![A-Za-z0-9_])({_SECRET_NAME})[ \t]*=[ \t]*\S+",
    re.IGNORECASE,
)
# A shell segment: an assignment keyword **in command position** — opening a
# line, or following a shell separator (`;` `&&` `||` `|` `(` `` ` ``) — through
# the end of that segment. Position is what establishes shell syntax; a bare
# keyword does not, or prose ("we export api_token=x in the docs") and SQL
# ("SQL DECLARE api_key=v") would be redacted as if they were commands.
#
# The whole segment is captured, not just the first assignment after the
# keyword: real commands carry option operands and several assignments
# (`env -u OLD PATH=/bin api_token=… pytest`), and scrubbing only the first
# token leaves the rest exposed. `setenv` is deliberately absent — its syntax is
# `setenv NAME value`, with no `=`, so it cannot match an assignment rule.
_SHELL_SEGMENT = re.compile(
    r"(?:^|(?<=[;&|(`]))([ \t]*(?:export|env|declare|typeset|local)\b[^\n;&|`]*)",
    re.MULTILINE | re.IGNORECASE,
)
# Bare inline assignment with no keyword and no command context — `API_TOKEN=…`
# inside prose or code. The *name's* shape is the only signal here, so this one
# stays case-sensitive: lowercase would make `sort(key=…)` and
# `groupby(key=col, secret=False)` collateral. Lowercase bare assignments are
# still covered when they open a line (the `.env` rule) or sit in a shell
# command (the two rules above).
_INLINE_ENV_SECRET = re.compile(rf"(?<![A-Za-z0-9_])({_SECRET_NAME})[ \t]*=[ \t]*\S+")

# Order matters: private keys span multiple lines and must be consumed before
# any single-line rule could partially match inside one.
_BUILTIN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PRIVATE_KEY, "[REDACTED:private-key]"),
    (_AWS_KEY, "[REDACTED:aws-key]"),
    (_GENERIC_API_KEY, "[REDACTED:api-key]"),
    (_SLACK_TOKEN, "[REDACTED:slack-token]"),
    (_GITHUB_TOKEN, "[REDACTED:github-token]"),
    (_BEARER, "Bearer [REDACTED:bearer]"),
)


def redact(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """Apply the D13 redaction table (+ any config-supplied extras) to ``text``."""
    for pattern, replacement in _BUILTIN_PATTERNS:
        text = pattern.sub(replacement, text)
    # env-secret rules keep the variable name (and any indent), redact the value.
    # Shell segments first: inside one, EVERY secret assignment goes, in either
    # case. Then the line-anchored `.env` rule, then the conservative bare rule.
    text = _SHELL_SEGMENT.sub(lambda m: _scrub_assignments(m.group(0)), text)
    text = _ENV_SECRET.sub(r"\1\2=[REDACTED:env-secret]", text)
    text = _INLINE_ENV_SECRET.sub(r"\1=[REDACTED:env-secret]", text)
    for raw_pattern in extra_patterns:
        text = re.sub(raw_pattern, "[REDACTED:custom]", text)
    return text


def redact_command(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """D13 for a value that is *known* to be a shell command — spec §4's
    tool-activity digest, where the scribe captured `input.command` verbatim.

    The command channel needs no keyword to prove it is shell, so every
    secret-named assignment in it is redacted in either case: `api_token=… ./run`
    leaks exactly as well as `API_TOKEN=… ./run`, and `redact`'s bare-inline rule
    is deliberately case-sensitive to protect ordinary code, which a command is
    not. Knowing the channel is what lets us be aggressive here without taxing
    prose everywhere else.
    """
    return _scrub_assignments(redact(text, extra_patterns))


def _scrub_assignments(text: str) -> str:
    return _ANY_SECRET_ASSIGNMENT.sub(r"\1=[REDACTED:env-secret]", text)
