"""Tests for the D13 redaction table (spec §10)."""

from __future__ import annotations

import pytest

from neurobase.core.redact import redact, redact_command


def test_private_key_block() -> None:
    text = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA...\nmore lines...\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    out = redact(text)
    assert "[REDACTED:private-key]" in out
    assert "MIIEowIBAAKCAQEA" not in out
    assert "before" in out and "after" in out


def test_aws_key() -> None:
    out = redact("key is AKIAABCDEFGHIJKLMNOP end")
    assert out == "key is [REDACTED:aws-key] end"


def test_generic_api_key() -> None:
    out = redact("token sk-abcdefghijklmnopqrstuvwx here")
    assert "[REDACTED:api-key]" in out
    assert "sk-abcdefghijklmnopqrstuvwx" not in out


def test_slack_token() -> None:
    out = redact("xoxb-1234567890-abcdefghij")
    assert out == "[REDACTED:slack-token]"


def test_github_token_classic() -> None:
    out = redact("ghp_" + "a" * 36)
    assert out == "[REDACTED:github-token]"


def test_github_token_fine_grained() -> None:
    out = redact("github_pat_" + "a" * 25)
    assert out == "[REDACTED:github-token]"


def test_bearer_token() -> None:
    out = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "Bearer [REDACTED:bearer]" in out
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out


def test_env_secret_keeps_name_redacts_value() -> None:
    out = redact("API_SECRET_KEY=supersecretvalue123")
    assert out == "API_SECRET_KEY=[REDACTED:env-secret]"


def test_env_secret_case_insensitive() -> None:
    out = redact("my_password=hunter2")
    assert out == "my_password=[REDACTED:env-secret]"


def test_env_rule_does_not_redact_non_secret_vars() -> None:
    out = redact("PATH=/usr/bin:/bin")
    assert out == "PATH=/usr/bin:/bin"


# A shell segment carries a keyword in COMMAND POSITION. Inside one, every
# secret-named assignment is redacted in either case — real commands set several
# variables and carry option operands, so scrubbing only the token right after
# the keyword leaves the rest exposed.
SHELL_LEAKS = [
    "export api_token=SECRET",
    "export API_TOKEN=SECRET && ./deploy.sh",
    "env api_key=SECRET pytest",
    "declare -x my_secret=SECRET",
    "env PATH=/bin api_token=SECRET pytest",  # a non-secret assignment first
    "export PATH=/bin api_token=SECRET",
    "declare -x PATH=/bin my_secret=SECRET",
    "env -u OLD api_token=SECRET pytest",  # an option operand in the way
    "make && export api_token=SECRET",  # after a shell separator
    "api_token=SECRET",  # line-anchored .env rule
    "API_TOKEN=SECRET cmd",  # bare inline, uppercase
    # Quoting. A value is a whole shell word, not `\S+` — otherwise a quoted
    # value with a space leaks everything after the space, and passwords and
    # tokens are exactly the values people quote.
    'env api_token="SECRET two" pytest',
    "export api_token='SECRET two'",
    'export api_token="SECRET;two"',  # a `;` inside quotes must not end the segment
    'export "api_token"=SECRET',  # the NAME may be quoted too — still an assignment
    # Malformed quoting must fail CLOSED: a command that failed to parse is still
    # a captured command, and can still carry a live credential.
    'api_token="SECRET two',
    "export api_token='SECRET two",
]

# These need the COMMAND channel. The global table has no keyword in command
# position to latch onto (the line starts with `echo`/`sudo`), and inventing one
# would mean treating any prose keyword as shell — the F9 mistake. Knowing the
# value IS a command is exactly what buys this coverage.
#
# NOTE the shape of this list: it is all the syntax that six revisions of a
# *position model* got wrong — pipelines, wrappers, redirections, option
# operands, substitutions, continuations. The current rule tracks no position at
# all, so none of it is special: an unquoted secret assignment is redacted
# wherever it appears.
COMMAND_ONLY_LEAKS = [
    # A secret can sit in the prefix of ANY command in a pipeline or list.
    "echo ok; api_token=SECRET ./run",
    "echo ok && api_token=SECRET ./run",
    "echo ok | api_token=SECRET ./run",
    # Wrappers — with short OR long option operands.
    "sudo -E env api_token=SECRET ./run",
    "command env api_token=SECRET ./run",
    "sudo -u root env api_token=SECRET ./run",
    "sudo --user root env api_token=SECRET ./run",
    "timeout --signal TERM env api_token=SECRET ./run",
    "nice -n 5 env api_token=SECRET ./run",
    # Redirections are shell syntax, not a command name.
    "2>&1 api_token=SECRET /usr/bin/env",
    "env 2>&1 api_token=SECRET /usr/bin/env",
    # The shell EXECUTES a substitution, even inside double quotes.
    'echo "$(api_token=SECRET ./run)"',
    'echo "`api_token=SECRET ./run`"',
    'echo "$(echo "$(api_token=SECRET x)")"',  # nested
    # A `<<` inside a quoted argument is not a heredoc — treating it as one hid
    # the whole next command from redaction.
    'echo "1 << EOF"\nsudo api_token=SECRET ./run',
    # A backslash-newline continues the VALUE onto the next physical line.
    "export api_token=FIRST\\\nSECRET /usr/bin/env",
    # Credential options, in any position.
    "pytest --api-key=SECRET",
    "curl --token=SECRET",
    "deploy --client-secret=SECRET",
    # A VALUE can contain spaces without being quoted — inside a substitution or
    # an expansion. Stopping the value scan at the first space leaked the rest.
    "api_token=$(printf SECRET)",
    "--token=$(printf SECRET)",
    "api_token=`printf SECRET`",
    # `${NAME:=v}` and `${NAME=v}` ASSIGN when NAME is unset: a secret assignment
    # wearing an expansion's clothes.
    "echo ${API_TOKEN:=SECRET}",
    "echo ${API_TOKEN=SECRET}",
    # Arithmetic assigns too — and its delimiters must survive.
    "echo $((API_TOKEN=SECRET))",
    # An ESCAPED backtick is how a legacy substitution nests.
    "echo `echo \\`api_token=SECRET ./run\\``",
    # `eval` / `sh -c` EXECUTE their string argument: it is code, not data.
    "eval 'api_token=SECRET ./run'",
    'sh -c "api_token=SECRET ./run"',
    'bash -c "api_token=SECRET ./run"',
]

# A quoted argument is DATA and must survive byte for byte — *including* when its
# content happens to start with a secret-named assignment. That case was the hole:
# the scanner read the opening quote as a quoted NAME and ate the closing one.
QUOTED_ARGUMENTS_ARE_DATA = [
    "echo 'api_token=example'",
    'python -c "api_token=example"',
    "echo 'hello world'",
    "echo ${API_TOKEN:-default}",  # `:-` substitutes, it does not assign
    # An executor NAME appearing as an argument executes nothing. Arming on any
    # `sh`/`bash`/`eval` token anywhere would mangle these — it is armed only when
    # the executor is the command itself.
    "echo sh -c 'api_token=example'",
    'echo bash -c "api_token=example"',
    "echo eval 'api_token=example'",
    "printf '%s' 'api_token=example'",
]

# `key`/`secret`/`password` appear constantly in options that SELECT or CONFIGURE
# rather than authenticate. The recognized option vocabulary is an allow-list, not
# a `*key*` pattern, or the digest gets mangled for no security gain.
NON_CREDENTIAL_OPTIONS = [
    "sort --sort-key=name file.csv",
    "csvtool --key=id in.csv",
    "useradd --password-policy=strict bob",
]

# Heredoc bodies are DATA the command consumes — a file, a script, SQL. All the
# heredocs one line declares are consumed in order, and the terminator must match
# exactly (`<<-` strips leading TABS only, never spaces).
HEREDOC_BODIES_ARE_DATA = [
    "cat <<ONE <<TWO\nfirst\nONE\nitems.sort(key=lambda x: x.id)\nTWO",
    "cat <<EOF\nitems.sort(key=lambda x: x.id)\n EOF\nEOF",
    "cat <<-EOF\n\titems.sort(key=lambda x: x.id)\n\tEOF",
]

# Position — not the keyword alone — is what establishes shell syntax. A keyword
# in the middle of a sentence is prose, and redacting it would destroy exactly
# the technical content the richer skim exists to keep.
NOT_SECRETS = [
    "we export api_token=example in docs",
    "SQL DECLARE api_key=value",
    "items.sort(key=lambda x: x.id)",
    "df.groupby(key=col, secret=False)",
    "the local secret_key=... convention we discussed",
    "PATH=/usr/bin",
]

# A shell command is NOT free of prose and code — it carries both as quoted
# ARGUMENTS. An assignment is a token in assignment position, never a substring
# inside an argument the command merely consumes.
COMMANDS_WITH_EMBEDDED_CONTENT = [
    'python -c "items.sort(key=lambda x: x.id)"',
    'python -c "df.groupby(key=col, secret=False)"',
    'sqlite3 db "DECLARE api_key=value"',
    'echo "we export api_token=example in docs"',
]


R = "[REDACTED:env-secret]"

# The EXACT-OUTPUT oracle. The boolean assertions elsewhere ("no SECRET" +
# "a marker appeared") are each half-blind: a marker can coexist with a surviving
# secret tail, and a marker can coexist with a deleted delimiter somewhere else in
# the command. Both have actually happened here. Writing the whole expected string
# is the only assertion that catches a leak and a structural loss at once, so
# every syntax family gets one.
EXACT: list[tuple[str, str]] = [
    # value forms
    ("api_token=SECRET ./run", f"api_token={R} ./run"),
    ('env api_token="SECRET two" x', f"env api_token={R} x"),
    ("api_token=$(printf SECRET) x", f"api_token={R} x"),
    ("api_token=`printf SECRET` x", f"api_token={R} x"),
    ('api_token="SECRET two', f"api_token={R}"),  # unterminated ⇒ fail closed
    # name forms — bare, quoted, and quote-CONCATENATED (the shell joins them)
    ('export "api_token"=SECRET', f'export "api_token"={R}'),
    ('export api_"token"=SECRET', f'export api_"token"={R}'),
    ("export api_$'token'=SECRET", f"export api_$'token'={R}"),
    # assignment-shaped syntax that is not an assignment WORD
    ("echo ${API_TOKEN:=SECRET}", f"echo ${{API_TOKEN:={R}}}"),
    ("echo $((API_TOKEN=SECRET))", f"echo $((API_TOKEN={R}))"),
    # structure around the value survives exactly
    ("echo ok; api_token=SECRET ./run", f"echo ok; api_token={R} ./run"),
    ('echo "$(api_token=SECRET ./run)"', f'echo "$(api_token={R} ./run)"'),
    ("echo api_token=SECRET\r\n", f"echo api_token={R}\r\n"),
    ("pytest --api-key=SECRET -q", f"pytest --api-key={R} -q"),
    # executed strings: the executor must be the COMMAND, not an argument
    ("eval 'api_token=SECRET ./run'", f"eval 'api_token={R} ./run'"),
    ("echo sh -c 'api_token=SECRET'", "echo sh -c 'api_token=SECRET'"),  # data!
    # heredoc: body is data, but D13's assignment forms still apply to it
    (
        "cat > d.sh <<EOF\nexport API_TOKEN=SECRET\nEOF",
        f"cat > d.sh <<EOF\nexport API_TOKEN={R}\nEOF",
    ),
    (
        "cat <<'PY'\nitems.sort(key=lambda x: x.id)\nPY",
        "cat <<'PY'\nitems.sort(key=lambda x: x.id)\nPY",
    ),
]


@pytest.mark.parametrize(
    "text",
    [c for c, _ in EXACT] + NOT_SECRETS + COMMANDS_WITH_EMBEDDED_CONTENT,
    ids=None,
)
def test_redaction_is_idempotent(text: str) -> None:
    """Redaction runs MORE THAN ONCE over the same text — the scribe scrubs each
    captured value, then the whole assembled document as defense in depth. So a
    second pass must be a no-op.

    It wasn't: `[REDACTED:env-secret]` contains no word-break character, so the
    value scanner read `[REDACTED:env-secret]export …` as a single bare token and
    replaced it, **silently eating the following word**. Caught by the idempotence
    check in `scripts/audit_command_redaction.py`, which is the one real property
    that script can test without an oracle.
    """
    for scrub in (redact, redact_command):
        once = scrub(text)
        assert scrub(once) == once


@pytest.mark.parametrize(("command", "expected"), EXACT, ids=[c for c, _ in EXACT])
def test_command_redaction_exact_output(command: str, expected: str) -> None:
    """Byte-exact expectation: catches a surviving secret AND a deleted delimiter
    in one assertion. `scripts/audit_command_redaction.py` structurally cannot —
    it has no oracle for what the output *should* be, only for what changed."""
    assert redact_command(command) == expected


@pytest.mark.parametrize("command", SHELL_LEAKS)
def test_shell_assignments_are_redacted_in_either_case(command: str) -> None:
    for out in (redact(command), redact_command(command)):
        assert "SECRET" not in out, out
        assert "[REDACTED:env-secret]" in out


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_prose_and_code_are_not_redacted_as_shell(text: str) -> None:
    assert redact(text) == text


@pytest.mark.parametrize("command", COMMANDS_WITH_EMBEDDED_CONTENT)
def test_command_scrub_preserves_embedded_code_sql_and_prose(command: str) -> None:
    """The command channel may be aggressive about *assignments*, but a quoted
    argument is data the command consumes — corrupting it destroys the very
    activity digest this capture exists to record."""
    assert redact_command(command) == command


@pytest.mark.parametrize("command", COMMAND_ONLY_LEAKS)
def test_command_channel_closes_pipelines_wrappers_and_credential_options(command: str) -> None:
    out = redact_command(command)
    assert "SECRET" not in out, out
    assert "[REDACTED:" in out


@pytest.mark.parametrize(
    "command",
    ["env PATH=/bin pytest api_key=example", "awk -v api_key=example file"],
)
def test_unquoted_credential_named_argument_is_redacted_by_design(command: str) -> None:
    """The accepted cost of having NO position model.

    `api_key=example` here is an argument to `pytest`/`awk`, not an environment
    assignment, and a perfect shell parser would leave it alone. We redact it
    anyway: deciding otherwise requires tracking command position through
    pipelines, wrappers, redirections, `env`'s grammar, substitutions and
    continuations — six revisions of exactly that leaked secrets every time.

    Fail-closed is the right side to err on here, and the cost is tiny: the
    command's SHAPE survives (`api_key=[REDACTED:env-secret]`), and only a small
    minority of real captured commands carry a secret-named `name=` token at all
    (`scripts/audit_command_redaction.py`). The justification is the fail-open
    history, not the rate. See spec §10.
    """
    out = redact_command(command)
    assert "example" not in out
    assert "api_key=[REDACTED:env-secret]" in out


@pytest.mark.parametrize("command", HEREDOC_BODIES_ARE_DATA)
def test_heredoc_bodies_are_data_not_shell(command: str) -> None:
    assert redact_command(command) == command


def test_heredoc_body_is_data_but_not_exempt_from_d13() -> None:
    """A body is data — and `cat > .env <<EOF` / `cat > deploy.sh <<EOF` is exactly
    where real secrets live. It gets the same *gated* assignment pass as prose, so
    `export`/`env`/`.env` forms are caught while body source is left alone."""
    command = (
        "cat > deploy.sh <<EOF\n"
        "export API_TOKEN=hunter2\n"
        "env api_token=hunter2 ./run\n"
        "MY_SECRET=hunter2\n"
        "items.sort(key=lambda x: x.id)\n"
        "aws AKIAABCDEFGHIJKLMNOP\n"
        "internal-id-77123\n"
        "EOF"
    )
    out = redact_command(command, extra_patterns=[r"internal-id-\d+"])
    assert "hunter2" not in out
    assert out.count("[REDACTED:env-secret]") == 3
    assert "[REDACTED:aws-key]" in out  # literal shapes still apply to a body
    assert "[REDACTED:custom]" in out  # configured extra patterns too
    assert "items.sort(key=lambda x: x.id)" in out  # body source untouched


@pytest.mark.parametrize("command", QUOTED_ARGUMENTS_ARE_DATA)
def test_quoted_arguments_survive_byte_for_byte(command: str) -> None:
    assert redact_command(command) == command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # The `))` is structure, not part of the value. The prose regex rules have
        # no idea what shell syntax is — their bare-word value ate both parens —
        # so they are not run over a command at all; the shell scanner does it.
        ("echo $((API_TOKEN=123456))", "echo $((API_TOKEN=[REDACTED:env-secret]))"),
        # The CR is captured input, so it is a word boundary, not part of a value.
        ("echo api_token=hunter2\r\n", "echo api_token=[REDACTED:env-secret]\r\n"),
    ],
)
def test_redaction_preserves_shell_structure_and_crlf(command: str, expected: str) -> None:
    """§10: redaction MUST NOT delete captured input."""
    assert redact_command(command) == expected


def test_redaction_never_deletes_captured_input() -> None:
    """The scanners are heuristics and WILL disagree with a real shell. When they
    do, the failure must be a mis-scan — never a lost character.

    This exact command (a heredoc inside `$(…)`, whose body contains a lone
    apostrophe) made the substitution scanner run to end-of-text, and an
    unconditional `end - 1` slice then silently ate the command's final byte.
    Found by round-tripping the real captured-command corpus, not by a unit test
    (`scripts/audit_command_redaction.py`).
    """
    command = "git commit -m \"$(cat <<'EOF'\nit can't be wrong\nEOF\n)\"\ngit log --stat"
    assert redact_command(command) == command


@pytest.mark.parametrize("command", NON_CREDENTIAL_OPTIONS)
def test_selection_and_policy_options_are_not_credentials(command: str) -> None:
    assert redact_command(command) == command


def test_heredoc_body_is_data_not_shell() -> None:
    """A heredoc body is a file/script/SQL blob the command consumes. Its lines
    are not shell words, so `key=lambda …` there is not an env assignment — but a
    `.env`-style line still redacts, because `cat > .env <<EOF` is exactly where
    real secrets live. Shell *after* the terminator is shell again."""
    out = redact_command(
        "cat > /tmp/s.py <<'PYEOF'\n"
        "items.sort(key=lambda x: x.id)\n"
        "API_KEY=hunter2\n"
        "PYEOF\n"
        "api_token=hunter2 ./run"
    )
    assert "items.sort(key=lambda x: x.id)" in out  # body code intact
    assert "API_KEY=[REDACTED:env-secret]" in out  # .env-shaped line still caught
    assert "api_token=[REDACTED:env-secret] ./run" in out  # shell resumes after
    assert "hunter2" not in out


def test_env_assignments_redact_every_occurrence_not_just_the_first() -> None:
    assert (
        redact("env api_token=one other_secret=two pytest")
        == "env api_token=[REDACTED:env-secret] other_secret=[REDACTED:env-secret] pytest"
    )


def test_redact_command_needs_no_keyword_to_prove_it_is_shell() -> None:
    """The §4 activity digest captured `input.command` — we KNOW it is a shell
    command, so no keyword is needed and case carries no weight. That knowledge
    is what lets `redact_command` be aggressive where `redact` must not be."""
    assert redact_command("api_token=hunter2 ./run.sh") == (
        "api_token=[REDACTED:env-secret] ./run.sh"
    )
    assert redact_command("pytest --api-key=hunter2") == "pytest --api-key=[REDACTED:env-secret]"
    # The same lowercase assignment mid-line is left alone by the global table,
    # which is the whole point of separating the channel.
    assert redact("run it with api_token=hunter2 please") == "run it with api_token=hunter2 please"


def test_env_rule_preserves_leading_indent() -> None:
    """A scribe body indents a bullet's continuation lines (spec §4); redaction
    must not reflow them back to column 0."""
    assert (
        redact("- fix this\n  API_TOKEN=hunter2") == "- fix this\n  API_TOKEN=[REDACTED:env-secret]"
    )


def test_extra_patterns_redact_as_custom() -> None:
    out = redact("internal-id-77123", extra_patterns=[r"internal-id-\d+"])
    assert out == "[REDACTED:custom]"


def test_multiple_patterns_in_one_body() -> None:
    text = "AKIAABCDEFGHIJKLMNOP and ghp_" + "b" * 36
    out = redact(text)
    assert "[REDACTED:aws-key]" in out
    assert "[REDACTED:github-token]" in out
