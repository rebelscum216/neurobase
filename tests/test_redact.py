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
