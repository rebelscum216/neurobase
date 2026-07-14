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
# Shell-style: the same assignment anywhere in a line, e.g. `export API_TOKEN=…`
# or `API_TOKEN=… cmd`. Reachable since the Claude scribe began capturing a
# command digest (ADR-0013) — commands are exactly where this shape lives. Kept
# case-sensitive and word-boundary-anchored so ordinary code (`sort_key=…`) is
# not swallowed; the line-anchored rule above still covers lowercase `.env` lines.
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
    text = _ENV_SECRET.sub(r"\1\2=[REDACTED:env-secret]", text)
    text = _INLINE_ENV_SECRET.sub(r"\1=[REDACTED:env-secret]", text)
    for raw_pattern in extra_patterns:
        text = re.sub(raw_pattern, "[REDACTED:custom]", text)
    return text
