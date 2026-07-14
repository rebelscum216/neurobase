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
# Order matters. A *balanced* quoted value first; then an UNTERMINATED quote,
# which swallows the rest of the line — malformed input must fail **closed**, or
# `api_token="hunter two` redacts `"hunter` and leaves ` two` in the store. A
# command that failed to parse is still a captured command with a live secret in
# it. Only then the bare-word form.
_VALUE = r"""(?:"(?:\\.|[^"])*"|'[^']*'|\$'(?:\\.|[^'])*'|["'][^\n]*|\S+)"""
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
# The segment runs to end of line and steps OVER quoted spans, so a `;` inside
# quotes can't truncate it (`export api_token="a;b"` must not leave `b"` behind).
# Separators *within* the segment are handled by `_scrub_shell`, which resets to
# a fresh command at each one. `setenv` is deliberately absent — its syntax is
# `setenv NAME value`, with no `=`.
_SHELL_SEGMENT = re.compile(
    r"""(?:^|(?<=[;&|(`]))([ \t]*(?:export|env|declare|typeset|local|readonly)\b"""
    r"""(?:'[^']*'|"(?:\\.|[^"])*"|[^\n])*)""",
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

# A token that IS an assignment (optionally quoting the name: `"api_token"=v`).
_SECRET_ASSIGN_TOKEN = re.compile(rf"""^["']?({_SECRET_NAME})["']?=""", re.IGNORECASE)
_ANY_ASSIGN_TOKEN = re.compile(r"""^["']?[A-Za-z_][A-Za-z0-9_]*["']?=""")

# Options whose name *is* a credential, so the value is a secret wherever it
# appears. Deliberately an ALLOW-LIST, not a `*key*` pattern: `key`, `secret` and
# `password` show up constantly in options that select or configure rather than
# authenticate — `--sort-key=name`, `--key=id`, `--password-policy=strict` — and
# mangling those destroys the captured command for no security gain. The cost of
# the allow-list is the mirror residual (a genuinely secret `--key=…` survives);
# that is recorded in spec §10.
_SECRET_OPTION_NAMES = frozenset(
    {
        "api-key",
        "apikey",
        "auth",
        "auth-token",
        "access-token",
        "bearer",
        "client-secret",
        "credential",
        "credentials",
        "id-token",
        "password",
        "passwd",
        "refresh-token",
        "secret",
        "secret-key",
        "token",
    }
)
_OPTION_TOKEN = re.compile(r"""^(--?)([A-Za-z0-9][A-Za-z0-9_-]*)=""")

# Builtins whose ENTIRE word list is assignments (`export A=1 B=2`).
_ASSIGNMENT_BUILTINS = frozenset({"export", "declare", "typeset", "local", "readonly"})
# Wrappers that run another command: the real command hasn't started yet, so
# assignment position survives them (`sudo -E env api_token=… ./run`).
_WRAPPERS = frozenset({"sudo", "doas", "command", "nohup", "nice", "exec", "stdbuf", "timeout"})
# Short options that take a separate operand — the operand must not be mistaken
# for the command name (`env -u OLD api_token=…`, `sudo -u root env …`).
_OPERAND_FLAGS = frozenset({"-u", "-g", "-p", "-C", "-t", "-S"})

_SEPARATORS = ";&|()\n"

# `<<EOF` / `<<-'EOF'` — everything until the terminator line is a heredoc BODY:
# a file, a script, a SQL blob. It is data the command consumes, not shell, so it
# must survive verbatim — a Python heredoc whose line begins `key=lambda …` is not
# an environment assignment. (A `1 << 2` bit-shift can't match: a digit is not a
# delimiter.) The D13 table still runs over it via `redact`; only the shell
# assignment walker steps around it.
_HEREDOC_START = re.compile(r"""<<-?[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1""")

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


def _is_secret_option(token: str) -> bool:
    """`--api-key=…` yes; `--sort-key=name`, `--password-policy=strict` no."""
    match = _OPTION_TOKEN.match(token)
    if match is None:
        return False
    return match.group(2).lower().replace("_", "-") in _SECRET_OPTION_NAMES


def _lex_shell(text: str) -> list[tuple[str, str]]:
    """Split shell text into ``("word"|"sep"|"gap", text)`` pieces, losslessly.

    Quoted spans are held inside their word, so `a="b c"` is one word and a `;`
    inside quotes is not a separator. An **unterminated** quote swallows the rest
    of the text into that word — which makes malformed input fail *closed*: a
    half-quoted secret (`api_token="hunter two`) is redacted whole rather than
    leaking its tail. A captured command that failed to parse is still a captured
    command, and can still carry a live credential.
    """
    pieces: list[tuple[str, str]] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char in " \t":
            start = index
            while index < size and text[index] in " \t":
                index += 1
            pieces.append(("gap", text[start:index]))
        elif char in _SEPARATORS:
            end = index + 1
            if char in "&|" and end < size and text[end] == char:
                end += 1  # `&&`, `||`
            pieces.append(("sep", text[index:end]))
            index = end
        else:
            start = index
            while index < size and text[index] not in " \t" and text[index] not in _SEPARATORS:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] in "'\"":
                    quote = text[index]
                    index += 1
                    while index < size and text[index] != quote:
                        index += 2 if quote == '"' and text[index] == "\\" else 1
                    if index >= size:
                        index = size  # unterminated ⇒ consume to end (fail closed)
                        break
                    index += 1
                    continue
                index += 1
            pieces.append(("word", text[start : min(index, size)]))
    return pieces


def _scrub_shell(text: str) -> str:
    """Walk shell text, stepping *around* heredoc bodies (which are data, not
    shell) and scrubbing assignments in the shell parts."""
    out: list[str] = []
    position = 0
    while True:
        start = _HEREDOC_START.search(text, position)
        if start is None:
            out.append(_scrub_shell_words(text[position:]))
            break
        newline = text.find("\n", start.end())
        if newline == -1:  # the operator line never ends — no body follows
            out.append(_scrub_shell_words(text[position:]))
            break
        out.append(_scrub_shell_words(text[position : newline + 1]))

        body = text[newline + 1 :]
        delimiter = start.group(2)
        offset = 0
        for line in body.split("\n"):
            if line.strip() == delimiter:
                break
            offset += len(line) + 1
        else:  # unterminated heredoc ⇒ the rest of the text is body
            out.append(body)
            break
        out.append(body[:offset])  # the body, verbatim
        position = newline + 1 + offset
    return "".join(out)


def _scrub_shell_words(text: str) -> str:
    """Redact secret assignments in shell text by walking its words *in position*.

    A word is an assignment only in **assignment position**, and the position
    model is per-command, because a secret can sit in the prefix of *any* command
    in a pipeline or list (`echo ok; api_token=… ./run`):

    - Every separator (`;` `&&` `||` `|` `&` newline `(` `)`) starts a fresh
      command, so assignment position reopens.
    - **Wrappers** (`sudo`, `command`, `nohup`, …) run another command, so the
      real command hasn't begun yet and assignment position survives them.
    - **Assignment builtins** (`export`, `declare`, `readonly`, …) take a word
      list that is assignments all the way down.
    - **`env`** has its own grammar: options, option operands, and assignments —
      and then a COMMAND, after which its arguments are ordinary arguments. This
      is the asymmetry that a single "builtin seen" flag gets wrong in both
      directions: `env PATH=/bin pytest api_key=example` must NOT redact
      `api_key=example` (it is pytest's argument), while `env -u OLD api_token=…
      pytest` MUST redact (an option operand is not the command name).

    Once the command name is seen, every later word is an argument — quoted code,
    SQL, or prose the command consumes — and is left exactly as captured.
    """
    out: list[str] = []
    mode = "prefix"  # prefix | env | assignments | args
    pending_operand = False

    for kind, piece in _lex_shell(text):
        if kind != "word":
            if kind == "sep":
                mode, pending_operand = "prefix", False  # a new command begins
            out.append(piece)
            continue

        word = piece
        if pending_operand:  # the operand of `-u`/`-g`/… — never the command name
            pending_operand = False
        elif _is_secret_option(word):
            word = _redact_assignment(word)  # the option name announces the value
        elif word.startswith("-") and word != "-":
            if word in _OPERAND_FLAGS:
                pending_operand = True
        elif mode != "args" and _SECRET_ASSIGN_TOKEN.match(word):
            word = _redact_assignment(word)
        elif mode != "args" and _ANY_ASSIGN_TOKEN.match(word):
            pass  # a non-secret assignment (PATH=/bin) — keep it, stay in position
        elif mode != "args":
            lowered = word.lower()
            if lowered in _ASSIGNMENT_BUILTINS:
                mode = "assignments"
            elif lowered == "env":
                mode = "env"
            elif lowered in _WRAPPERS:
                mode = "prefix"  # the real command is still ahead
            elif mode != "assignments":
                mode = "args"  # this is the command name
        out.append(word)
    return "".join(out)
