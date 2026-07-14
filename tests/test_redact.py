"""Tests for the D13 redaction table (spec §10)."""

from __future__ import annotations

from neurobase.core.redact import redact


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


def test_shell_assignments_are_redacted_in_either_case() -> None:
    """§4's command digest captures shell commands, and `export API_TOKEN=…` is
    the commonest shape a secret takes there. The keyword marks an assignment
    context, so this rule is case-insensitive: shell names are case-sensitive
    but nothing requires them to be uppercase."""
    assert redact("export api_token=hunter2") == "export api_token=[REDACTED:env-secret]"
    assert (
        redact("export API_TOKEN=hunter2 && ./deploy.sh")
        == "export API_TOKEN=[REDACTED:env-secret] && ./deploy.sh"
    )
    assert redact("env api_key=hunter2 pytest") == "env api_key=[REDACTED:env-secret] pytest"
    assert redact("declare -x my_secret=hunter2") == "declare -x my_secret=[REDACTED:env-secret]"


def test_bare_inline_assignment_is_redacted_without_swallowing_code() -> None:
    """With no keyword to mark an assignment context, the *name's* shape is the
    only signal — so the bare inline rule stays case-sensitive. Lowercase would
    make ordinary keyword arguments collateral."""
    assert redact("API_TOKEN=hunter2 ./run.sh") == "API_TOKEN=[REDACTED:env-secret] ./run.sh"
    assert redact("make && API_TOKEN=hunter2") == "make && API_TOKEN=[REDACTED:env-secret]"
    assert redact("items.sort(key=lambda x: x.id)") == "items.sort(key=lambda x: x.id)"
    assert redact("df.groupby(key=col, secret=False)") == "df.groupby(key=col, secret=False)"


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
