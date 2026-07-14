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
# A complete shell VALUE: a quoted string (single, double, or ANSI-C) or a run of
# bare characters. `\S+` is NOT enough — `api_token="hunter two"` would redact
# only `"hunter` and leave ` two"` behind, leaking half the secret. Quoted values
# with spaces are exactly what passwords and tokens look like.
_VALUE = r"""(?:"(?:\\.|[^"])*"|'[^']*'|\$'(?:\\.|[^'])*'|\S+)"""
# `.env`-style line: NAME=value, at the start of a line. `[ \t]*` (not `\s*`)
# so the rule can never eat a newline, and the leading indent is *captured* and
# re-emitted — a body's structural indentation must survive redaction (spec §4).
_ENV_SECRET = re.compile(
    rf"""^([ \t]*)["']?({_SECRET_NAME})["']?[ \t]*=[ \t]*{_VALUE}""",
    re.MULTILINE | re.IGNORECASE,
)
# A shell segment: an assignment keyword **in command position** — opening a
# line, or following a shell separator (`;` `&&` `||` `|` `(` `` ` ``) — through
# the end of that segment. Position is what establishes shell syntax; a bare
# keyword does not, or prose ("we export api_token=x in the docs") and SQL
# ("SQL DECLARE api_key=v") would be redacted as if they were commands.
#
# The segment body steps OVER quoted spans, so a `;` inside quotes can't truncate
# it (`export api_token="a;b"` must not leave `b"` behind). `setenv` is
# deliberately absent — its syntax is `setenv NAME value`, with no `=`.
_SHELL_SEGMENT = re.compile(
    r"""(?:^|(?<=[;&|(`]))([ \t]*(?:export|env|declare|typeset|local|readonly)\b"""
    r"""(?:'[^']*'|"(?:\\.|[^"])*"|[^\n;&|`])*)""",
    re.MULTILINE | re.IGNORECASE,
)
# Bare inline assignment with no keyword and no command context — `API_TOKEN=…`
# inside prose or code. The *name's* shape is the only signal here, so this one
# stays case-sensitive: lowercase would make `sort(key=…)` and
# `groupby(key=col, secret=False)` collateral. Lowercase bare assignments are
# still covered when they open a line (the `.env` rule) or sit in a shell
# command (the rules above).
_INLINE_ENV_SECRET = re.compile(rf"(?<![A-Za-z0-9_])({_SECRET_NAME})[ \t]*=[ \t]*{_VALUE}")

# --- shell tokenization -----------------------------------------------------
#
# A shell command is NOT "not prose and not code" — `python -c "…"`, `sqlite3 db
# "…"`, and `echo "…"` all carry source, SQL, and prose as *quoted arguments*.
# So a secret assignment cannot be found by scanning for a substring: it has to
# be a whole TOKEN sitting in an assignment POSITION. Anything inside a quoted
# argument is data the command consumes, and must be left exactly as captured.

# One shell word, with quoted spans kept intact (so `a="b c"` is a single token).
_TOKEN = re.compile(r"""(?:'[^']*'|\$'(?:\\.|[^'])*'|"(?:\\.|[^"])*"|\\.|[^\s'"\\])+""")
# A token that IS an assignment (optionally quoting the name: `"api_token"=v`).
_SECRET_ASSIGN_TOKEN = re.compile(rf"""^["']?({_SECRET_NAME})["']?=""", re.IGNORECASE)
_ANY_ASSIGN_TOKEN = re.compile(r"""^["']?[A-Za-z_][A-Za-z0-9_]*["']?=""")
# An option whose *name* announces a secret: `--api-key=…`, `--password=…`.
_SECRET_OPTION_TOKEN = re.compile(
    r"""^--?[A-Za-z0-9-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9-]*=""",
    re.IGNORECASE,
)
# Builtins after which every following assignment word is still an assignment.
_ASSIGN_BUILTINS = frozenset({"export", "env", "declare", "typeset", "local", "readonly"})

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
    # Shell segments first — inside one, assignments are found by TOKEN, not by
    # substring. Then the line-anchored `.env` rule, then the conservative bare
    # rule for text that is neither a command nor a `.env` line.
    text = _SHELL_SEGMENT.sub(lambda m: _scrub_shell(m.group(0)), text)
    text = _ENV_SECRET.sub(r"\1\2=[REDACTED:env-secret]", text)
    text = _INLINE_ENV_SECRET.sub(r"\1=[REDACTED:env-secret]", text)
    for raw_pattern in extra_patterns:
        text = re.sub(raw_pattern, "[REDACTED:custom]", text)
    return text


def redact_command(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """D13 for a value that is *known* to be a shell command — spec §4's
    tool-activity digest, where the scribe captured `input.command` verbatim.

    Being a command means we can find assignments *structurally* (a token in
    assignment position) instead of guessing from a keyword. It does NOT mean the
    text is free of prose or code: `python -c "…"`, `sqlite3 db "…"` and
    `echo "…"` all carry source, SQL, and prose as quoted arguments, and those are
    data the command consumes — they are left exactly as captured.
    """
    return _scrub_shell(redact(text, extra_patterns))


def _redact_assignment(token: str) -> str:
    """`name=<value>` → `name=[REDACTED:env-secret]`, keeping the name verbatim
    (including any quoting) and dropping the whole value token."""
    name, _, _value = token.partition("=")
    return f"{name}=[REDACTED:env-secret]"


def _scrub_shell(text: str) -> str:
    """Redact secret assignments in shell text by walking its TOKENS.

    A shell word is an assignment only in *assignment position*: before the
    command name (`api_token=… ./run.sh`), or anywhere in the argument list of an
    assignment builtin (`env -u OLD PATH=/bin api_token=… pytest`). Once a plain
    command word is seen, the rest are that command's arguments — a quoted Python
    snippet or SQL string is data, not an assignment, and scanning it for
    `key=` substrings destroys captured content without redacting any secret.
    Secret-*named* options (`--api-key=…`) are redacted in any position, since
    the option name itself announces the value.
    """
    out: list[str] = []
    cursor = 0
    assignment_zone = True  # the command prefix, before the command name
    builtin_seen = False  # `env`/`export`/… keeps the whole word list in play
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        out.append(text[cursor : match.start()])
        cursor = match.end()

        if _SECRET_OPTION_TOKEN.match(token) or (
            assignment_zone and _SECRET_ASSIGN_TOKEN.match(token)
        ):
            token = _redact_assignment(token)
        elif assignment_zone and _ANY_ASSIGN_TOKEN.match(token):
            pass  # a non-secret assignment (PATH=/bin) — keep it, stay in the zone
        elif token.lower() in _ASSIGN_BUILTINS:
            builtin_seen = True
        elif not builtin_seen and not token.startswith("-"):
            # The command name. Everything after it is that command's arguments —
            # `-c "items.sort(key=…)"` is source code, not an assignment. After a
            # builtin we stay in the zone instead, so an option operand (the `OLD`
            # in `env -u OLD`) can't hide the assignments that follow it.
            assignment_zone = False
        out.append(token)
    out.append(text[cursor:])
    return "".join(out)
