"""Tests for the Codex hook installer (spec §7): hooks.json ownership/idempotence
and the surgical ~/.codex/config.toml [projects.*] table merge."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from neurobase.adapters.codex import install

SHIM = "/abs/shim/neurobase"


# --- hooks.json -----------------------------------------------------------


def test_build_hooks_into_empty() -> None:
    result = install.build_hooks({}, SHIM)
    hooks = result["hooks"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == f"{SHIM} hook codex session-start"
    assert hooks["Stop"][0]["hooks"][0]["command"] == f"{SHIM} hook codex stop"
    # CamelCase events, no matcher key (unlike Claude's SessionStart).
    assert "matcher" not in hooks["SessionStart"][0]


def test_build_hooks_whole_file_wrapped_in_hooks_key() -> None:
    result = install.build_hooks({}, SHIM)
    assert set(result) == {"hooks"}


def test_build_hooks_preserves_foreign_events_and_keys() -> None:
    foreign = {"hooks": [{"type": "command", "command": "/usr/bin/other-tool"}]}
    existing = {"version": 1, "hooks": {"PreToolUse": [foreign]}}
    result = install.build_hooks(existing, SHIM)
    assert result["version"] == 1
    assert result["hooks"]["PreToolUse"] == [foreign]
    assert "SessionStart" in result["hooks"] and "Stop" in result["hooks"]


def test_build_hooks_idempotent() -> None:
    once = install.build_hooks({}, SHIM)
    twice = install.build_hooks(once, SHIM)
    assert install.render_hooks(once) == install.render_hooks(twice)


def test_remove_owned_hooks_preserves_foreign_events() -> None:
    owned = install.build_hooks({}, SHIM)
    foreign = {"hooks": [{"type": "command", "command": "/usr/bin/audit"}]}
    owned["hooks"]["PreToolUse"] = [foreign]
    result = install.remove_owned_hooks(owned)
    assert result == {"hooks": {"PreToolUse": [foreign]}}


def test_build_hooks_replaces_owned_group_not_stacking() -> None:
    old_cmd = "/old/path/neurobase hook codex session-start"
    existing = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": old_cmd}]}]}}
    result = install.build_hooks(existing, SHIM)
    groups = result["hooks"]["SessionStart"]
    assert len(groups) == 1
    assert groups[0]["hooks"][0]["command"] == f"{SHIM} hook codex session-start"


def test_owned_marker_is_path_anchored_not_bare_substring() -> None:
    owned = {"hooks": [{"type": "command", "command": "/x/neurobase hook codex stop"}]}
    win_cmd = r"C:\tools\neurobase.exe hook codex session-start"
    owned_win = {"hooks": [{"type": "command", "command": win_cmd}]}
    # Prose mention — neurobase not preceded by a separator — is not ours.
    prose = {"hooks": [{"type": "command", "command": 'echo "run neurobase hook codex to set up"'}]}
    # The Claude subcommand is a different agent — not owned by the Codex installer.
    claude = {"hooks": [{"type": "command", "command": "/x/neurobase hook claude session-start"}]}
    # `hook codexX` must not match (word-boundary guard).
    codexx = {"hooks": [{"type": "command", "command": "/x/neurobase hook codexXYZ"}]}
    assert install._is_owned_group(owned)
    assert install._is_owned_group(owned_win)
    assert not install._is_owned_group(prose)
    assert not install._is_owned_group(claude)
    assert not install._is_owned_group(codexx)


def test_preserves_foreign_similar_command() -> None:
    foreign = {"hooks": [{"type": "command", "command": "/bin/echo neurobase is cool"}]}
    existing = {"hooks": {"Stop": [foreign]}}
    result = install.build_hooks(existing, SHIM)
    assert foreign in result["hooks"]["Stop"]
    assert any(
        g["hooks"][0]["command"] == f"{SHIM} hook codex stop" for g in result["hooks"]["Stop"]
    )


def test_load_hooks_missing_returns_empty(tmp_path: Path) -> None:
    assert install.load_hooks(tmp_path / "nope.json") == {}


def test_load_hooks_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(install.HooksParseError):
        install.load_hooks(path)


def test_hooks_json_path_scopes(tmp_path: Path) -> None:
    proj = install.hooks_json_path(user=False, cwd=tmp_path)
    assert proj == tmp_path / ".codex" / "hooks.json"
    user = install.hooks_json_path(user=True, cwd=tmp_path)
    assert user == Path.home() / ".codex" / "hooks.json"


def test_write_hooks_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".codex" / "hooks.json"
    doc = install.build_hooks({}, SHIM)
    install.write_hooks(path, doc)
    assert json.loads(path.read_text()) == doc
    assert path.read_text().endswith("\n")


# --- config.toml (surgical merge) -----------------------------------------

KEY = "/Users/dev/repo"


def _project(text: str, key: str = KEY) -> dict:
    return tomllib.loads(text)["projects"][key]


def test_merge_config_appends_to_empty() -> None:
    out = install.merge_config("", KEY)
    entry = _project(out)
    assert entry == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


def test_merge_config_preserves_comments_and_other_tables() -> None:
    existing = (
        "# my codex config\n"
        'model = "gpt-5"  # keep this comment\n'
        "\n"
        '[projects."/some/other/repo"]\n'
        'trust_level = "trusted"\n'
    )
    out = install.merge_config(existing, KEY)
    # Everything preserved verbatim...
    assert "# my codex config" in out
    assert 'model = "gpt-5"  # keep this comment' in out
    assert '[projects."/some/other/repo"]' in out
    # ...and our table added.
    parsed = tomllib.loads(out)
    assert parsed["model"] == "gpt-5"
    assert parsed["projects"]["/some/other/repo"]["trust_level"] == "trusted"
    assert _project(out) == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


def test_merge_config_updates_existing_table_in_place() -> None:
    # Table exists (trusted) but missing the hooks key + has a user comment.
    existing = (
        f'[projects."{KEY}"]\n'
        "# I trust this repo\n"
        'trust_level = "trusted"\n'
        'approved_commands = ["ls"]\n'
    )
    out = install.merge_config(existing, KEY)
    assert "# I trust this repo" in out  # comment preserved
    entry = _project(out)
    assert entry["hooks"] == ".codex/hooks.json"
    assert entry["trust_level"] == "trusted"
    assert entry["approved_commands"] == ["ls"]  # other key preserved


def test_merge_config_overwrites_wrong_hooks_value() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\nhooks = "wrong/path.json"\n'
    out = install.merge_config(existing, KEY)
    assert _project(out)["hooks"] == ".codex/hooks.json"


def test_merge_config_idempotent_noop_returns_verbatim() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\nhooks = ".codex/hooks.json"\n'
    out = install.merge_config(existing, KEY)
    assert out == existing  # byte-for-byte unchanged


def test_merge_config_does_not_touch_following_table() -> None:
    existing = f'[projects."{KEY}"]\ntrust_level = "trusted"\n\n[other]\nfoo = 1\n'
    out = install.merge_config(existing, KEY)
    parsed = tomllib.loads(out)
    assert parsed["other"] == {"foo": 1}  # untouched
    assert _project(out)["hooks"] == ".codex/hooks.json"
    # The hooks key landed inside our table, not the [other] table.
    assert "hooks" not in parsed["other"]


def test_merge_config_malformed_raises() -> None:
    with pytest.raises(install.ConfigParseError):
        install.merge_config('[projects."x"\n not valid', KEY)


def test_merge_config_escapes_special_path() -> None:
    weird = '/Users/dev/a "b"/repo'
    out = install.merge_config("", weird)
    assert tomllib.loads(out)["projects"][weird]["hooks"] == ".codex/hooks.json"


def test_merge_config_idempotent_with_escaped_quote_path() -> None:
    weird = '/Users/dev/a "b"/repo'
    once = install.merge_config("", weird)
    twice = install.merge_config(once, weird)
    assert twice == once


def test_remove_project_hooks_config_preserves_trust_and_other_keys() -> None:
    existing = (
        f'[projects."{KEY}"]\n'
        'trust_level = "trusted"\n'
        'hooks = ".codex/hooks.json"\n'
        'approved_commands = ["ls"]\n'
    )
    out = install.remove_project_hooks_config(existing, KEY)
    entry = _project(out)
    assert entry == {"trust_level": "trusted", "approved_commands": ["ls"]}


def test_remove_project_hooks_config_noops_foreign_hooks_value() -> None:
    existing = f'[projects."{KEY}"]\nhooks = "foreign/hooks.json"\n'
    assert install.remove_project_hooks_config(existing, KEY) == existing


def test_load_config_text_missing_returns_empty(tmp_path: Path) -> None:
    assert install.load_config_text(tmp_path / "nope.toml") == ""


def test_load_config_text_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[unterminated\n", encoding="utf-8")
    with pytest.raises(install.ConfigParseError):
        install.load_config_text(path)


# --- _parse_dotted_key (TOML dotted-key tokenizer) -------------------------
#
# This tokenizer is how the surgical config editor recognises the *header line*
# of the table it is about to rewrite (``_find_table_header``). If it decodes a
# segment differently from tomllib the editor rewrites the wrong table; if it
# fails on a header tomllib accepts, ``merge_config`` cannot locate a table it
# can see in the parse tree and aborts with ``ConfigParseError`` — init then
# refuses to install. So the unit under test is decoding *correctness*, not
# merely "did it return something".

# Well-formed dotted keys → their decoded segments. Expectations come from the
# TOML 1.0 key grammar (bare / basic-string / literal-string segments joined by
# dots, with optional whitespace around the dots).
_WELL_FORMED_KEYS: list[tuple[str, list[str]]] = [
    ("projects", ["projects"]),
    ("A-z_0-9", ["A-z_0-9"]),  # every character class a bare key may use
    ("projects.hooks.trust", ["projects", "hooks", "trust"]),
    ('projects."/Users/dev/repo"', ["projects", "/Users/dev/repo"]),
    ("projects.'/Users/dev/repo'", ["projects", "/Users/dev/repo"]),
    (' projects . "/x" ', ["projects", "/x"]),  # whitespace around dots and at both ends
    ('"a.b"', ["a.b"]),  # a dot inside a quoted segment is not a separator
    ('"a.b".c', ["a.b", "c"]),
    ("'a.b'.c", ["a.b", "c"]),
    ('""', [""]),  # the empty key is legal TOML, however odd
    ("''", [""]),
    ('"a\\"b"', ['a"b']),  # an escaped quote must not terminate the segment
    ('"\'"', ["'"]),  # a bare single quote inside a basic string
    ("'\"'", ['"']),  # a bare double quote inside a literal string
    ("mixed.'lit'.\"basic\"", ["mixed", "lit", "basic"]),
]


@pytest.mark.parametrize(("text", "expected"), _WELL_FORMED_KEYS)
def test_parse_dotted_key_well_formed(text: str, expected: list[str]) -> None:
    """Every shape a real ``[projects.*]`` header can take must round-trip to the
    same segments tomllib would produce, so the editor targets the right table."""
    assert install._parse_dotted_key(text) == expected


def test_escapes_table_matches_toml_1_0() -> None:
    """TOML 1.0 defines exactly these compact escapes. A *missing* entry makes a
    legitimate header unparseable, so init aborts on a config it should handle; a
    *spurious* one decodes a sequence TOML itself would have rejected."""
    assert install._ESCAPES == {
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "f": "\f",
        "r": "\r",
        '"': '"',
        "\\": "\\",
    }


@pytest.mark.parametrize(("escape", "decoded"), sorted(install._ESCAPES.items()))
def test_parse_dotted_key_decodes_simple_escapes(escape: str, decoded: str) -> None:
    """Each compact escape collapses to one character and the scan resumes *after*
    the two-character sequence — an off-by-one would leak the escape letter."""
    assert install._parse_dotted_key('"a\\' + escape + 'b"') == ["a" + decoded + "b"]


# Unicode escapes → decoded segments. The widths are load-bearing: ``\u`` takes
# exactly 4 hex digits and ``\U`` exactly 8, so hex digits that merely *follow* a
# complete escape are literal text.
_UNICODE_ESCAPES: list[tuple[str, list[str]]] = [
    (r'"\u00e9"', ["\N{LATIN SMALL LETTER E WITH ACUTE}"]),
    (r'"caf\u00e9"', ["caf\N{LATIN SMALL LETTER E WITH ACUTE}"]),
    (r'"\u0041\u0042"', ["AB"]),  # back-to-back escapes
    (r'"\u0041BCD"', ["ABCD"]),  # \u stops at 4 digits; BCD stays literal
    (r'"\U0001F600"', ["\N{GRINNING FACE}"]),  # astral plane needs all 8 digits
    (r'"\U0001F600!"', ["\N{GRINNING FACE}!"]),
    (r'"\U000000e9"', ["\N{LATIN SMALL LETTER E WITH ACUTE}"]),  # 8-digit form of a BMP char
    (r'"\u0041".b', ["A", "b"]),  # decoding leaves the dotted scan intact
]


@pytest.mark.parametrize(("text", "expected"), _UNICODE_ESCAPES)
def test_parse_dotted_key_decodes_unicode_escapes(text: str, expected: list[str]) -> None:
    """A wrong escape width silently yields a *different* key — 4 digits read for
    ``\\U`` would turn an astral character into a control char plus stray digits,
    and the editor would then rewrite the wrong table."""
    assert install._parse_dotted_key(text) == expected


def test_parse_dotted_key_literal_string_does_not_process_escapes() -> None:
    """TOML literal (single-quoted) strings have no escape sequences at all. A
    Windows path is the case that bites: ``\\Users`` would otherwise be read as a
    truncated 8-digit ``\\U`` escape and reject a perfectly valid header."""
    assert install._parse_dotted_key(r"'C:\Users\dev'") == [r"C:\Users\dev"]
    assert install._parse_dotted_key(r"'a\nb'") == [r"a\nb"]


# Escape sequences that are not valid TOML → None. The parser must refuse rather
# than guess, because a guessed decoding aims the editor at the wrong table.
_BAD_ESCAPES: list[str] = [
    r'"\q"',  # unrecognised escape character
    r'"\e"',  # \e is TOML 1.1 only; this parser targets 1.0
    r'"\u12"',  # \u truncated: only 3 characters before the closing quote
    r'"\U0001F6',  # \U truncated at end of input
    r'"\uZZZZ"',  # 4 characters but not hex
    r'"\Ugarbage"',  # 8 characters but not hex
    r'"a\"',  # the escaped quote is consumed, leaving the string unterminated
]


@pytest.mark.parametrize("text", _BAD_ESCAPES)
def test_parse_dotted_key_rejects_bad_escapes(text: str) -> None:
    """Truncated, non-hex and unrecognised escapes are rejected outright — the
    caller then raises rather than silently editing some other table."""
    assert install._parse_dotted_key(text) is None


def test_parse_dotted_key_rejects_single_quote_escape_in_basic_string() -> None:
    """``\\'`` is *not* a TOML escape (only ``\\"`` is). Accepting it would mean
    accepting a header tomllib already rejected."""
    assert install._parse_dotted_key('"a\\\'b"') is None


# Malformed dotted keys → None. Each entry names the shape it guards.
_MALFORMED_KEYS: list[str] = [
    "",  # empty input
    "   ",  # whitespace only
    "a.",  # trailing dot
    ".a",  # leading dot
    "a..b",  # doubled dot
    "a b",  # missing dot between bare segments
    '"a"b',  # missing dot after a quoted segment
    "a.$",  # segment starts with a character no bare key may contain
    "[a]",  # a nested header, not a key
    '"unterminated',  # unterminated basic string
    "'unterminated",  # unterminated literal string
    'projects."x',  # unterminated basic string in a later segment
]


@pytest.mark.parametrize("text", _MALFORMED_KEYS)
def test_parse_dotted_key_malformed_returns_none(text: str) -> None:
    """Anything that is not a well-formed dotted key must return ``None`` so
    ``_find_table_header`` reports "no header here" instead of half-matching."""
    assert install._parse_dotted_key(text) is None


def test_parse_dotted_key_trailing_whitespace_is_not_a_missing_segment() -> None:
    """Trailing whitespace ends the scan cleanly; it must not be mistaken for a
    dangling dot (which would reject a header the regex already matched)."""
    assert install._parse_dotted_key("a  ") == ["a"]
    assert install._parse_dotted_key('"a" \t') == ["a"]


# --- parser correctness as the caller sees it -------------------------------


def test_merge_config_finds_header_quoted_as_a_literal_string() -> None:
    """A user may have written the project table with single quotes. The editor
    must recognise it as the same table and update in place. If the tokenizer
    missed the literal-string form, ``merge_config`` would see the key in the
    parse tree but find no header line and raise ``ConfigParseError`` — init
    would refuse to install against a perfectly valid config."""
    existing = f"[projects.'{KEY}']\n" + 'trust_level = "trusted"\n'
    out = install.merge_config(existing, KEY)
    assert out.count("[projects.") == 1
    assert _project(out) == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


def test_merge_config_finds_header_written_with_unicode_escapes() -> None:
    """Escaped non-ASCII in the header decodes to the same key tomllib sees, so
    the table is updated in place rather than duplicated."""
    key = "/Users/dev/caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    existing = '[projects."/Users/dev/caf\\u00e9"]\ntrust_level = "trusted"\n'
    out = install.merge_config(existing, key)
    assert out.count("[projects.") == 1
    entry = tomllib.loads(out)["projects"][key]
    assert entry == {"trust_level": "trusted", "hooks": ".codex/hooks.json"}


# --- _parse_dotted_key vs tomllib (differential) ---------------------------
#
# `_parse_dotted_key` reimplements TOML's dotted-key grammar so the installer can
# find an existing `[projects."…"]` header no matter how its path segment was
# quoted or escaped. Any reimplementation can drift from the spec, and hand-written
# expectations only catch the drift someone thought to write down.
#
# So these tests use `tomllib` as the ORACLE: for each key text, our parser must
# accept exactly what tomllib accepts, and when both accept, must decode to the
# same segments. That is a much stronger claim than "returns the value I observed",
# and it is what caught the three divergences these cases now pin.


def _tomllib_key_parts(key_text: str) -> list[str] | None:
    """The decoded segments of `[<key_text>]` per tomllib, or None if it rejects.

    A single table header nests exactly one key per level, so walking down the
    parsed mapping recovers the segments the spec says the header denotes.
    """
    try:
        parsed = tomllib.loads(f"[{key_text}]\n")
    except tomllib.TOMLDecodeError:
        return None
    parts: list[str] = []
    node: object = parsed
    while isinstance(node, dict) and node:
        (key,) = node.keys()
        parts.append(key)
        node = node[key]
    return parts


_KEY_TEXTS = [
    # --- well-formed: both must accept, and agree on the decoded segments ---
    "plain_key",
    "with-dash",
    "a.b.c",
    "a . b",
    "0123",
    '"quoted"',
    "'literal'",
    '"a.b"',  # a quoted dot is one segment, not two
    "'a.b'",
    '"caf\\u00e9"',  # 4-digit escape
    '"\\U0001F600"',  # 8-digit escape, astral
    '"tab\\there"',
    '"\\u0041"',
    "a.\"b\".'c'",  # mixed quoting across segments
    r"'C:\Users\dev'",  # literal strings do not process escapes
    '""',  # the empty key is legal TOML
    # --- malformed: both must reject ---
    "",
    "   ",
    ".",
    "a.",
    ".a",
    "a..b",
    "a b",  # missing dot between segments
    '"unterminated',
    "'unterminated",
    '"bad\\escape"',
    '"\\q"',
    # --- the three divergences this file's fix closed ---
    '"\\u+123"',  # int(x,16) accepts a leading sign; TOML does not
    '"\\u1_23"',  # ...and an underscore separator
    '"\\u 123"',  # ...and leading whitespace
    '"\\ud800"',  # a lone surrogate is not a Unicode scalar value
    '"\\U00110000"',  # beyond U+10FFFF
    '"\\u12"',  # too few digits
    "café",  # str.isalnum() is Unicode-aware; TOML bare keys are ASCII
    "中文",
    # --- a fourth divergence, spotted by the reviewer in round 1 ---
    # TOML forbids RAW control characters in both string forms; they must be
    # escaped. Tab is the sole exception, so it is the control case that keeps
    # the rule from being "reject every control character".
    '"a\x00b"',
    '"a\x07b"',
    '"a\x1fb"',
    '"a\x7fb"',
    "'a\x00b'",  # literal strings have no escapes, but the rule still applies
    "'a\x1fb'",
    '"a\tb"',  # legal: raw tab is permitted in a basic string
    "'a\tb'",  # legal: and in a literal string
]


@pytest.mark.parametrize("key_text", _KEY_TEXTS, ids=[repr(k) for k in _KEY_TEXTS])
def test_parse_dotted_key_agrees_with_tomllib(key_text: str) -> None:
    """Accept/reject and the decoded segments must both match the spec oracle.

    Asserting the decoded parts (not just accept/reject) is what makes this
    load-bearing: a parser that accepted the right set of keys but decoded
    `\\U0001F600` as two characters, or dropped a segment, would pass an
    accept-only check and still corrupt the user's config.toml by rewriting the
    wrong table.
    """
    assert install._parse_dotted_key(key_text) == _tomllib_key_parts(key_text)
