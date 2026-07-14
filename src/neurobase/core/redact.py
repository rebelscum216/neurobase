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
# The bare-word form stops at shell-structural closers, not just at whitespace:
# `\S+` swallowed the `))` of `$((API_TOKEN=1))`, deleting captured structure.
#
# An ALREADY-REDACTED value matches first, and this is load-bearing: redaction is
# applied more than once to the same text (each captured value, then the whole
# document as defense in depth). The marker contains no word-break character, so
# without this alternative a second pass reads `[REDACTED:env-secret]export …` as
# ONE bare value token and replaces it — silently eating the following word.
# Matching the marker alone makes the replacement a no-op, i.e. **idempotent**.
_MARKER = r"\[REDACTED:[a-z-]+\]"
_VALUE = rf"""(?:{_MARKER}|"(?:\\.|[^"])*"|'[^']*'|\$'(?:\\.|[^'])*'|["'][^\n]*|[^\s;&|()<>]+)"""
# `.env`-style line: NAME=value, at the start of a line. `[ \t]*` (not `\s*`)
# so the rule can never eat a newline, and the leading indent is *captured* and
# re-emitted — a body's structural indentation must survive redaction (spec §4).
_ENV_SECRET = re.compile(
    rf"""^([ \t]*)["']?({_SECRET_NAME})["']?[ \t]*=[ \t]*{_VALUE}""",
    re.MULTILINE | re.IGNORECASE,
)
# A shell command pasted into PROSE: an assignment keyword in command position —
# opening a line, or after a shell separator. This gate exists only for the global
# `redact()` path, where most text is not shell: an unquoted `key=…` in a prompt
# or a code snippet is a keyword argument, not a secret, so the shell scrub must
# not run over everything. Inside a matched segment, `_scrub_shell` takes over.
#
# The segment steps OVER quoted spans (so a `;` in quotes can't truncate it) and
# over `\<newline>` continuations (so a value continued onto the next physical
# line stays with its assignment). `setenv` is deliberately absent — its syntax is
# `setenv NAME value`, with no `=`.
_SHELL_SEGMENT = re.compile(
    r"""(?:^|(?<=[;&|(`]))([ \t]*(?:export|env|declare|typeset|local|readonly)\b"""
    r"""(?:'[^']*'|"(?:\\.|[^"])*"|\\\n|[^\n])*)""",
    re.MULTILINE | re.IGNORECASE,
)
# Bare inline assignment with no keyword and no command context — `API_TOKEN=…`
# inside prose or code. The *name's* shape is the only signal here, so this one
# stays case-sensitive: lowercase would make `sort(key=…)` and
# `groupby(key=col, secret=False)` collateral. Lowercase bare assignments are
# still covered when they open a line (the `.env` rule) or sit in a shell
# command (the rules above).
_INLINE_ENV_SECRET = re.compile(rf"(?<![A-Za-z0-9_])({_SECRET_NAME})[ \t]*=[ \t]*{_VALUE}")

_BUILTIN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PRIVATE_KEY, "[REDACTED:private-key]"),
    (_AWS_KEY, "[REDACTED:aws-key]"),
    (_GENERIC_API_KEY, "[REDACTED:api-key]"),
    (_SLACK_TOKEN, "[REDACTED:slack-token]"),
    (_GITHUB_TOKEN, "[REDACTED:github-token]"),
    (_BEARER, "Bearer [REDACTED:bearer]"),
)


def _redact_assignments_in_text(text: str) -> str:
    """The D13 assignment rules for text that is NOT known to be a command —
    prose, a code snippet, or a heredoc body.

    All three are gated so ordinary content survives: the shell scrub runs only
    on a segment led by an assignment keyword in command position, the `.env` rule
    is line-anchored, and the bare inline rule is case-sensitive. An unquoted
    `key=…` in a prompt is a keyword argument, not a secret.
    """
    text = _SHELL_SEGMENT.sub(lambda m: _scrub_shell(m.group(0)), text)
    text = _ENV_SECRET.sub(r"\1\2=[REDACTED:env-secret]", text)
    return _INLINE_ENV_SECRET.sub(r"\1=[REDACTED:env-secret]", text)


def redact(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """Apply the D13 redaction table (+ any config-supplied extras) to ``text``."""
    for pattern, replacement in _BUILTIN_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _redact_assignments_in_text(text)
    for raw_pattern in extra_patterns:
        text = re.sub(raw_pattern, "[REDACTED:custom]", text)
    return text


def redact_command(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """D13 for a value that is *known* to be a shell command — spec §4's
    tool-activity digest, where the scribe captured `input.command` verbatim.

    Knowing the value is shell is what lets us scrub it structurally instead of
    guessing from keywords. It does NOT mean the text is free of prose or code:
    `python -c "…"`, `sqlite3 db "…"` and `echo "…"` carry source, SQL, and prose
    as quoted arguments, and those are data the command consumes — left verbatim.

    The prose-oriented regex rules of `redact` are deliberately NOT run over a
    command: their bare-word value (`\\S+`) has no idea what shell syntax is, and
    it ate the closing `))` of `echo $((API_TOKEN=1))`. On a command the shell
    scanner does that job, and it preserves structure. The literal-secret patterns
    (private keys, AWS/GitHub/Slack tokens, bearers) still apply — those are shape
    matches, not syntax.
    """
    for pattern, replacement in _BUILTIN_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _scrub_shell(text)
    for raw_pattern in extra_patterns:
        text = re.sub(raw_pattern, "[REDACTED:custom]", text)
    return text


# --- shell scrubbing --------------------------------------------------------
#
# Six revisions of this code tried to decide *whether a word sits in assignment
# position* — tracking command names, pipelines, wrappers, `env`'s grammar,
# redirections, option operands. Every revision shipped a leak, because that is
# the full POSIX command grammar and an approximation of it fails open.
#
# So the position model is gone. The rule is now positional-free and fail-closed:
#
#   In UNQUOTED shell text, redact the value of every secret-named assignment,
#   wherever it appears. Never touch a quoted argument — except to recurse into
#   command substitutions, which the shell executes. Heredoc bodies are data.
#
# This needs only three things a lexer can get right — quoting, substitution,
# heredocs — and none of the command grammar. The cost is that a *credential-named
# argument* to some other command (`env PATH=/bin pytest api_key=example`) is
# redacted even though it is not an environment assignment. That is a deliberate
# trade: it is fail-closed, it preserves the command's shape
# (`api_key=[REDACTED:env-secret]`), and only a small minority of real captured
# commands contain a secret-named `name=` token at all — so precise position
# tracking buys little fidelity in practice, while costing correctness we could
# not deliver. The security case rests on that fail-open HISTORY, not on a rate;
# `scripts/audit_command_redaction.py` is a local, reproducible smoke test of the
# no-mangling MUST, not a general guarantee. Spec §10 records this.

# An assignment NAME is a run of fragments, each bare or quoted, concatenated by
# the shell before the `=`: `api_token=`, `"api_token"=`, and `api_"token"=` and
# `api_$'token'=` are all the same assignment to bash. A regex cannot express
# that, so `_match_assignment_name` parses it (see there for why the distinction
# from a wholly quoted ARGUMENT — `'api_token=v'` — is the load-bearing part).
_SECRET_NAME_FULL = re.compile(rf"^{_SECRET_NAME}$", re.IGNORECASE)
_NAME_CHARS = re.compile(r"""[A-Za-z0-9_]+""")
_MARKER_RE = re.compile(_MARKER)
# A credential option: an ALLOW-LIST, never a `*key*` pattern — `--sort-key=name`,
# `--key=id` and `--password-policy=strict` select and configure, they don't auth.
_SECRET_OPTION_NAMES = frozenset(
    {
        "api-key", "apikey", "api-token", "auth", "auth-token", "access-token",
        "bearer", "client-secret", "credential", "credentials", "id-token",
        "password", "passwd", "refresh-token", "secret", "secret-key", "token",
    }
)  # fmt: skip
_OPTION_HEAD = re.compile(r"""--?([A-Za-z0-9][A-Za-z0-9_-]*)=""")
# A heredoc operator, only ever matched in UNQUOTED text (a `<<` inside a quoted
# argument is a bit-shift or prose, not a heredoc — promoting it hid an entire
# following command from redaction).
_HEREDOC_OP = re.compile(r"""<<(-?)[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\2""")
# A word boundary: an assignment can only START a word. `\r` is here so a CRLF
# command keeps its carriage return (dropping it deletes captured input).
_WORD_BREAK = " \t\r\n;&|()<>"
_SEPARATORS_RESET = frozenset({";", "&&", "||", "|"})
# `eval "…"` and `sh -c "…"` EXECUTE their string argument. Under the "a quoted
# argument is data" invariant those would be an unredacted channel, so the
# executed string is scrubbed as shell instead. This is a narrow allow-list, not
# a return of the position model: missing a shell here degrades to "treated as
# data", which spec §10 records as a residual.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_BARE_WORD = re.compile(r"""[A-Za-z0-9_./-]+""")
# `${NAME:=value}` / `${NAME=value}` assign; `${NAME:-value}` does not.
_EXPANSION_ASSIGN = re.compile(rf"""\$\{{({_SECRET_NAME}):?=""", re.IGNORECASE)


def _scrub_shell(text: str) -> str:
    """Redact secret assignments in shell text, without parsing its grammar.

    Walks the text once, tracking only what a lexer can be trusted with:

    - **Single quotes** are inert — copied verbatim, nothing inside them is shell.
    - **Double quotes** are inert *except* for `$(…)` and backticks, which the
      shell executes: those are scrubbed recursively, so `echo "$(api_token=… ./run)"`
      cannot smuggle a secret past us inside a quoted argument.
    - **Heredoc bodies** are data the command consumes (a file, a script, SQL) and
      are copied verbatim. All heredocs a logical line declares are queued and
      consumed in order, and the terminator must match exactly (`<<-` strips
      leading TABS only, per POSIX — not spaces).
    - **Backslash escapes**, including `\\<newline>` line continuations, keep a
      value glued to its assignment across physical lines.
    - Everything else is unquoted shell, where a secret-named assignment at a word
      boundary has its whole value redacted, regardless of position.
    """
    out: list[str] = []
    index, size = 0, len(text)
    heredocs: list[tuple[str, bool]] = []  # (delimiter, strip_leading_tabs)
    at_word_start = True
    executes_next = False  # the next quoted word is CODE (`eval …`, `sh -c …`)
    shell_seen = False
    at_command_start = True  # only an executor HERE can execute a string

    while index < size:
        char = text[index]

        if text.startswith("\\`", index):
            # An ESCAPED backtick opens a NESTED legacy substitution — the standard
            # way backticks nest (``echo `echo \`inner\``` ``). Treated as a plain
            # escape, its body never reached a word start and the secret survived.
            close = text.find("\\`", index + 2)
            closed = close != -1
            inner = text[index + 2 : close] if closed else text[index + 2 :]
            out.append("\\`" + _scrub_shell(inner) + ("\\`" if closed else ""))
            index = close + 2 if closed else size
            at_word_start = False
            continue

        if char == "\\" and index + 1 < size:  # escape, incl. `\<newline>`
            out.append(text[index : index + 2])
            index += 2
            at_word_start = False
            continue

        # Checked BEFORE the quote branches: the NAME may itself be quoted
        # (`export "api_token"=secret` is valid shell for the same assignment),
        # and a quote branch would otherwise swallow it as an opaque argument.
        if at_word_start:
            after_name = _match_assignment_name(text, index)
            if after_name == -1:
                option = _OPTION_HEAD.match(text, index)
                if option is not None and _is_credential_option(option.group(1)):
                    after_name = option.end()
            if after_name != -1:
                out.append(text[index:after_name])
                index = _skip_value(text, after_name)
                out.append("[REDACTED:env-secret]")
                at_word_start = False
                continue

        if char == "\n":
            out.append(char)
            index += 1
            at_word_start = True
            while heredocs:  # bodies begin on the line after the operator
                delimiter, strip_tabs = heredocs.pop(0)
                index = _copy_heredoc_body(text, index, delimiter, strip_tabs, out)
            continue

        if char == "'":
            end = text.find("'", index + 1)
            end = size if end == -1 else end + 1  # unterminated ⇒ to the end
            # `eval '…'` / `sh -c '…'` — the quoted string is CODE the shell
            # executes, not data it consumes, so it gets scrubbed like shell.
            out.append(
                _scrub_executed_string(text[index:end]) if executes_next else text[index:end]
            )
            executes_next = False
            index = end
            at_word_start = False
            continue

        if char == '"':
            end = _end_of_double_quote(text, index)
            span = text[index:end]
            out.append(
                _scrub_executed_string(span) if executes_next else _scrub_double_quoted(span)
            )
            executes_next = False
            index = end
            at_word_start = False
            continue

        if char == "`":
            end = _end_of_backticks(text, index)
            closed = end <= size and text[end - 1 : end] == "`"
            inner = text[index + 1 : end - 1] if closed else text[index + 1 : end]
            out.append("`" + _scrub_shell(inner) + ("`" if closed else ""))
            index = end
            at_word_start = False
            continue

        if text.startswith("$((", index):
            # Arithmetic. `$((API_TOKEN=1))` assigns, so scrub it — but the
            # delimiters are structure and must survive intact.
            end = _end_of_substitution(text, index + 2)
            out.append("$(" + _scrub_substitution(text, index + 2, end))
            index = end
            at_word_start = False
            continue

        if text.startswith("$(", index):
            end = _end_of_substitution(text, index + 1)
            out.append("$(" + _scrub_substitution(text, index + 2, end))
            index = end
            at_word_start = False
            continue

        if text.startswith("${", index):
            # `${API_TOKEN:=SECRET}` and `${API_TOKEN=SECRET}` ASSIGN when unset —
            # they are a secret assignment wearing an expansion's clothes.
            end = _end_of_expansion(text, index + 1)
            out.append(_scrub_expansion(text[index:end]))
            index = end
            at_word_start = False
            continue

        heredoc = _HEREDOC_OP.match(text, index)
        if heredoc is not None:
            heredocs.append((heredoc.group(3), heredoc.group(1) == "-"))
            out.append(heredoc.group(0))
            index = heredoc.end()
            at_word_start = False
            continue

        if at_word_start:
            word = _BARE_WORD.match(text, index)
            if word is not None:
                token = word.group(0)
                # Arm the executed-string channel ONLY when the executor is the
                # command itself — the first word of the text or of a new command.
                # `echo sh -c 'api_token=x'` does not execute anything; `echo`
                # just prints it, and scrubbing that quoted argument would delete
                # captured data. This is the ONE place a scrap of command position
                # survives, and it is safe *because it can only under-arm*: a
                # missed executor degrades to "treated as data" (a documented
                # residual), never to mangled input. That asymmetry is the whole
                # reason the general position model had to go — it failed the
                # other way, on secrets.
                if at_command_start:
                    if token == "eval":
                        executes_next = True
                    elif token in _SHELLS:
                        shell_seen = True
                elif token == "-c" and shell_seen:
                    executes_next = True
                out.append(token)
                index = word.end()
                at_word_start = False
                at_command_start = False
                continue

        out.append(char)
        index += 1
        at_word_start = char in _WORD_BREAK
        if char in ";&|\n(":
            shell_seen = executes_next = False  # a new command begins
            at_command_start = True

    return "".join(out)


def _scrub_expansion(span: str) -> str:
    """`${NAME:=value}` / `${NAME=value}` ASSIGN when NAME is unset — a secret
    assignment wearing an expansion's clothes. `${NAME:-value}` only substitutes
    and is left alone. Delimiters are preserved either way."""
    match = _EXPANSION_ASSIGN.match(span)
    if match is None:
        return span
    closer = "}" if span.endswith("}") else ""
    return f"{match.group(0)}[REDACTED:env-secret]{closer}"


def _scrub_executed_string(span: str) -> str:
    """The string argument of `eval` / `sh -c` is CODE the shell executes, not
    data it consumes, so it is scrubbed as shell. Quotes are preserved."""
    if len(span) >= 2 and span[0] in "\"'" and span[-1] == span[0]:
        return span[0] + _scrub_shell(span[1:-1]) + span[-1]
    return _scrub_shell(span)


def _scrub_substitution(text: str, start: int, end: int) -> str:
    """Scrub the body of a `$(…)` and re-emit its closing `)` if there was one.

    Reconstruction is **loss-proof by construction**: the closer is only stripped
    when it is actually there. The scanners here are heuristics — a lone
    apostrophe inside a heredoc body, for instance, makes `_end_of_substitution`
    run to the end of the text — and an unconditional `end - 1` slice silently
    deleted the command's last character when they disagreed. A scrubber may
    over- or under-scan; it must never *delete captured input*.
    """
    if text[end - 1 : end] == ")":
        return _scrub_shell(text[start : end - 1]) + ")"
    return _scrub_shell(text[start:end])


def _match_assignment_name(text: str, index: int) -> int:
    """If a secret-named assignment head starts at ``index``, return the position
    just past its ``=``; otherwise return ``-1``.

    The shell concatenates quoted and unquoted fragments *before* the ``=``, so
    all of these are the same assignment and all must be caught::

        api_token=v      "api_token"=v      api_"token"=v      api_$'token'=v

    The load-bearing distinction is against a **wholly quoted argument** —
    ``echo 'api_token=v'`` — where the ``=`` lies *inside* the quotes. So: read
    fragments, accumulating the unquoted *content* of each; the assignment exists
    only if a **top-level** ``=`` follows. In `'api_token=v'` the `=` is consumed
    by the quoted fragment, so no top-level `=` is ever seen, and the argument is
    left verbatim.
    """
    size = len(text)
    cursor = index
    name: list[str] = []
    while cursor < size:
        char = text[cursor]
        if char == "=":
            break
        if char == "'":
            end = text.find("'", cursor + 1)
            if end == -1:
                return -1  # unterminated ⇒ not a well-formed name
            name.append(text[cursor + 1 : end])
            cursor = end + 1
            continue
        if char == '"':
            end = _end_of_double_quote(text, cursor)
            if end > size or text[end - 1 : end] != '"':
                return -1
            name.append(text[cursor + 1 : end - 1])
            cursor = end
            continue
        if text.startswith("$'", cursor):  # ANSI-C quoting
            end = text.find("'", cursor + 2)
            if end == -1:
                return -1
            name.append(text[cursor + 2 : end])
            cursor = end + 1
            continue
        chunk = _NAME_CHARS.match(text, cursor)
        if chunk is None:
            return -1  # anything else (space, `/`, `.`, …) — not an assignment
        name.append(chunk.group(0))
        cursor = chunk.end()
    if cursor >= size or text[cursor] != "=" or not name:
        return -1
    if _SECRET_NAME_FULL.match("".join(name)) is None:
        return -1
    return cursor + 1


def _is_credential_option(name: str) -> bool:
    return name.lower().replace("_", "-") in _SECRET_OPTION_NAMES


def _skip_value(text: str, index: int) -> int:
    """Consume a whole shell VALUE, so redaction can never leave half a secret.

    A value is not "up to the next space": it can contain quoted spans, escapes,
    `\\<newline>` continuations, and — the case that leaked — **nested command
    substitutions and expansions**, which legitimately contain spaces
    (`api_token=$(printf SECRET)`). Stopping at the first space inside `$( … )`
    left the rest of the secret in the clear.

    An unterminated quote or substitution consumes to the end: malformed input
    fails CLOSED, because a command that failed to parse still carried a real
    credential.
    """
    size = len(text)
    already = _MARKER_RE.match(text, index)
    if already is not None:
        return already.end()  # already redacted ⇒ the value is exactly the marker
    while index < size:
        char = text[index]
        if char == "\\" and index + 1 < size:
            index += 2  # keeps a `\<newline>`-continued value in one piece
            continue
        if text.startswith("$(", index):
            index = _end_of_substitution(text, index + 1)
            continue
        if text.startswith("${", index):
            index = _end_of_expansion(text, index + 1)
            continue
        if char == "`":
            index = _end_of_backticks(text, index)
            continue
        if char == "'":
            end = text.find("'", index + 1)
            index = size if end == -1 else end + 1
            continue
        if char == '"':
            index = _end_of_double_quote(text, index)
            continue
        if char in _WORD_BREAK:
            break
        index += 1
    return index


def _end_of_backticks(text: str, index: int) -> int:
    """Index just past the closing backtick, honouring `\\``-escaped backticks —
    which is how a legacy substitution NESTS (``echo `echo \\`inner\\`` ``)."""
    size = len(text)
    cursor = index + 1
    while cursor < size:
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == "`":
            return cursor + 1
        cursor += 1
    return size


def _end_of_expansion(text: str, index: int) -> int:
    """Index just past the `}` closing a `${…}`, honouring nesting."""
    size = len(text)
    depth = 0
    cursor = index
    while cursor < size:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return size


def _end_of_double_quote(text: str, index: int) -> int:
    """Index just past the closing `"` (or end of text if unterminated).

    Steps over `$(…)` and backticks: a `"` *inside* a command substitution does
    not close the outer quote (`echo "$(echo "x")"` is one argument). Missing
    that truncates the span and hands the rest of the command to the wrong
    scrubber — which is how a nested substitution lost a quote.
    """
    size = len(text)
    cursor = index + 1
    while cursor < size:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if text.startswith("$(", cursor):
            cursor = _end_of_substitution(text, cursor + 1)
            continue
        if char == "`":
            end = text.find("`", cursor + 1)
            cursor = size if end == -1 else end + 1
            continue
        if char == '"':
            return cursor + 1
        cursor += 1
    return size


def _end_of_substitution(text: str, index: int) -> int:
    """Index just past the `)` closing a `$(`, honouring nesting and quotes."""
    size = len(text)
    depth = 0
    cursor = index
    while cursor < size:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "'":
            end = text.find("'", cursor + 1)
            cursor = size if end == -1 else end + 1
            continue
        if char == '"':
            cursor = _end_of_double_quote(text, cursor)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return size


def _scrub_double_quoted(span: str) -> str:
    """A double-quoted argument is data — EXCEPT `$(…)` and backticks, which the
    shell still executes inside it. Recurse into those and nothing else, so
    `python -c "items.sort(key=lambda x: x.id)"` survives verbatim while
    `echo "$(api_token=… ./run)"` does not hide a secret."""
    out: list[str] = []
    index, size = 0, len(span)
    while index < size:
        char = span[index]
        if char == "\\" and index + 1 < size:
            out.append(span[index : index + 2])
            index += 2
            continue
        if span.startswith("$(", index):
            end = _end_of_substitution(span, index + 1)
            out.append("$(" + _scrub_substitution(span, index + 2, end))
            index = end
            continue
        if char == "`":
            end = span.find("`", index + 1)
            end = size if end == -1 else end
            out.append("`" + _scrub_shell(span[index + 1 : end]))
            if end < size:
                out.append("`")
            index = end + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _copy_heredoc_body(
    text: str, index: int, delimiter: str, strip_tabs: bool, out: list[str]
) -> int:
    """Copy one heredoc body verbatim, up to and including its terminator line.

    The terminator must match EXACTLY — `<<-` strips leading tabs only, never
    spaces (POSIX). A missing terminator means the body runs to the end of the
    text, which is also the safe reading: it is all data, not shell.

    The body is data, but it is **not exempt from D13** (spec §10): `cat > .env
    <<EOF` and `cat > deploy.sh <<EOF` are exactly where real secrets live. It gets
    the same assignment pass as prose — which is gated (keyword in command
    position / line-anchored / case-sensitive) precisely so that arbitrary source
    in a body is not treated as shell. Redacting only `.env`-shaped lines here left
    `export API_TOKEN=…` and `env api_token=… ./run` inside a body in the clear.
    """
    size = len(text)
    while index < size:
        line_end = text.find("\n", index)
        line_end = size if line_end == -1 else line_end + 1
        line = text[index:line_end]
        out.append(_redact_assignments_in_text(line))
        index = line_end
        candidate = line.rstrip("\n")
        if (candidate.lstrip("\t") if strip_tabs else candidate) == delimiter:
            break
    return index
