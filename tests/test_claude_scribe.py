"""Tests for the Claude scribe (spec §4), driven by the §11.1 transcript fixture."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from neurobase.adapters.claude import scribe
from neurobase.core import projects, store


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    """A store with `myrepo` enabled + its git repo."""
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)
    return root, repo


def _write_transcript(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


# The §11.1 fixture, verbatim in shape.
FIXTURE_EVENTS = [
    {
        "type": "user",
        "isSidechain": False,
        "cwd": "/Users/you/proj",
        "gitBranch": "main",
        "sessionId": "3fc4beef",
        "message": {"role": "user", "content": "Fix the login bug"},
    },
    {
        "type": "user",
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": []}],
        },
    },
    {
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done — the null check was missing in auth.py"}],
        },
    },
    {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "(subagent)"}},
    {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": "<command-name>/model</command-name> noise"},
    },
]


def test_parses_fixture_prompts_and_summary(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    t = _write_transcript(tmp_path / "t.jsonl", FIXTURE_EVENTS)
    parsed = scribe.parse_transcript(t)
    assert parsed["prompts"] == ["Fix the login bug"]  # tool_result / sidechain / noise all dropped
    assert parsed["summary"] == "Done — the null check was missing in auth.py"
    assert parsed["branch"] == "main"
    assert parsed["session_id"] == "3fc4beef"


def test_writes_raw_with_body_format(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    t = _write_transcript(tmp_path / "t.jsonl", FIXTURE_EVENTS)
    path = scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear", session_id="s1")
    assert path is not None
    doc = store.read_doc(path)
    assert doc["agent"] == "claude"
    assert "## Session" in doc.body and "- ended: clear" in doc.body
    assert "- prompts captured: 1" in doc.body
    assert "- Fix the login bug" in doc.body
    assert "## Final assistant summary" in doc.body
    assert "null check was missing" in doc.body
    # skipped content never appears
    assert "subagent" not in doc.body
    assert "/model" not in doc.body
    assert "tool_result" not in doc.body


def test_content_as_text_block_list_joined(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(repo),
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "part one "},
                    {"type": "text", "text": "part two"},
                ],
            },
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    parsed = scribe.parse_transcript(t)
    assert parsed["prompts"] == ["part one part two"]


def test_redaction_applied_before_write(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(repo),
            "message": {"role": "user", "content": "my key is AKIAABCDEFGHIJKLMNOP"},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    path = scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear")
    assert path is not None
    doc = store.read_doc(path)
    assert "[REDACTED:aws-key]" in doc.body
    assert "AKIAABCDEFGHIJKLMNOP" not in doc.body


def test_opt_in_untracked_dir_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "store"
    untracked = tmp_path / "untracked"
    untracked.mkdir()
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(untracked),
            "message": {"role": "user", "content": "hi"},
        }
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    assert scribe.scribe(root, transcript_path=t, cwd=str(untracked), reason="clear") is None


def test_empty_capture_writes_nothing(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    # only noise + tool_result + sidechain — nothing capturable, no assistant text
    events = [
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "sub"}},
        {
            "type": "user",
            "isSidechain": False,
            "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    assert scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear") is None


def test_bounds_prompts_and_chars(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(repo),
            "message": {"role": "user", "content": f"prompt {i} " + "x" * 700},
        }
        for i in range(30)
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    path = scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear")
    assert path is not None
    doc = store.read_doc(path)
    assert "- prompts captured: 25" in doc.body  # kept last 25
    # each prompt truncated to 600 chars
    prompt_lines = [ln for ln in doc.body.splitlines() if ln.startswith("- prompt ")]
    assert all(len(ln) <= 2 + 600 for ln in prompt_lines)


def test_missing_transcript_returns_none(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    assert (
        scribe.scribe(root, transcript_path=tmp_path / "nope.jsonl", cwd=str(repo), reason="x")
        is None
    )


def test_unparseable_lines_skipped_not_fatal(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    t = tmp_path / "t.jsonl"
    t.write_text(
        "not json\n"
        + json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "cwd": str(repo),
                "message": {"role": "user", "content": "real prompt"},
            }
        )
        + "\n{ broken json",
        encoding="utf-8",
    )
    path = scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear")
    assert path is not None
    assert "real prompt" in store.read_doc(path).body
