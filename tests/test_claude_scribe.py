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
            "message": {"role": "user", "content": f"prompt {i} " + "x" * 1400},
        }
        for i in range(30)
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)
    path = scribe.scribe(root, transcript_path=t, cwd=str(repo), reason="clear")
    assert path is not None
    doc = store.read_doc(path)
    assert "- prompts captured: 25" in doc.body  # kept last 25
    # each prompt truncated to 1200 chars
    prompt_lines = [ln for ln in doc.body.splitlines() if ln.startswith("- prompt ")]
    assert all(len(ln) <= 2 + 1200 for ln in prompt_lines)


def test_richer_skim_uses_verified_tool_shapes_and_avoids_final_message_trap(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    root, repo = enabled
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "The durable discovery is substantially longer."},
                    {
                        "type": "tool_use",
                        "id": "tool-agent-1",
                        "name": "Agent",
                        "input": {"description": "research", "prompt": "investigate"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-edit-1",
                        "name": "Edit",
                        "input": {"file_path": "src/auth.py"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-bash-1",
                        "name": "Bash",
                        "input": {"command": "uv run pytest\nsecond line"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "cwd": str(repo),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-agent-1",
                        "content": [{"type": "text", "text": "Agent found the race condition."}],
                    }
                ]
            },
        },
        {"type": "assistant", "message": {"content": "Short trailing answer."}},
    ]
    transcript = _write_transcript(tmp_path / "rich.jsonl", events)
    parsed = scribe.parse_transcript(transcript)

    assert parsed["summary"] == "The durable discovery is substantially longer."
    assert parsed["subagent_reports"] == ["Agent found the race condition."]
    assert parsed["activity_files"] == ["src/auth.py"]
    assert parsed["activity_commands"] == ["uv run pytest"]
    written = scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="clear")
    assert written is not None
    body = store.read_doc(written).body
    assert "## Activity" in body
    assert "## Subagent reports" in body
    assert "## Assistant highlights" in body


def _assistant(text: str = "", blocks: list[dict] | None = None) -> dict:
    content: list[dict] = list(blocks or [])
    if text:
        content.insert(0, {"type": "text", "text": text})
    return {"type": "assistant", "isSidechain": False, "message": {"content": content}}


def test_activity_digest_survives_odd_and_empty_tool_inputs(tmp_path: Path) -> None:
    """Best-effort digest (spec §4): a malformed or empty tool_use input is
    skipped, never fatal — an empty Bash command must not lose the capture."""
    events = [
        _assistant(blocks=[{"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]),
        _assistant(
            blocks=[{"type": "tool_use", "id": "b2", "name": "Bash", "input": {"command": ""}}]
        ),
        _assistant(
            blocks=[{"type": "tool_use", "id": "b3", "name": "Bash", "input": "not-a-dict"}]
        ),
        _assistant(
            blocks=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": 42}}]
        ),
        _assistant(
            text="ok",
            blocks=[{"type": "tool_use", "id": "b4", "name": "Bash", "input": {"command": "ls"}}],
        ),
    ]
    parsed = scribe.parse_transcript(_write_transcript(tmp_path / "odd.jsonl", events))
    assert parsed["activity_commands"] == ["ls"]
    assert parsed["activity_files"] == []
    assert parsed["summary"] == "ok"


def test_activity_is_deduped_and_capped(tmp_path: Path) -> None:
    blocks = [
        {"type": "tool_use", "id": f"e{i}", "name": "Edit", "input": {"file_path": f"f{i}.py"}}
        for i in range(40)
    ]
    blocks += [
        {"type": "tool_use", "id": f"b{i}", "name": "Bash", "input": {"command": f"cmd {i}"}}
        for i in range(30)
    ]
    blocks += [  # duplicates of the first of each never take a second slot
        {"type": "tool_use", "id": "edup", "name": "Edit", "input": {"file_path": "f0.py"}},
        {"type": "tool_use", "id": "bdup", "name": "Bash", "input": {"command": "cmd 0"}},
    ]
    parsed = scribe.parse_transcript(
        _write_transcript(tmp_path / "busy.jsonl", [_assistant(blocks=blocks)])
    )
    assert parsed["activity_files"] == [f"f{i}.py" for i in range(scribe.MAX_ACTIVITY_FILES)]
    assert parsed["activity_commands"] == [f"cmd {i}" for i in range(scribe.MAX_ACTIVITY_COMMANDS)]
    long_command = "x" * 500
    parsed = scribe.parse_transcript(
        _write_transcript(
            tmp_path / "long.jsonl",
            [
                _assistant(
                    blocks=[
                        {
                            "type": "tool_use",
                            "id": "b",
                            "name": "Bash",
                            "input": {"command": f"{long_command}\ntrailing"},
                        }
                    ]
                )
            ],
        )
    )
    assert parsed["activity_commands"] == [long_command[: scribe.MAX_ACTIVITY_COMMAND_CHARS]]


def test_highlights_evict_oldest_within_the_total_budget(tmp_path: Path) -> None:
    # 20 messages × 500 kept chars = 10,000 > the 6,000-char total budget.
    events = [_assistant(text=f"m{i} " + "x" * 600) for i in range(20)]
    parsed = scribe.parse_transcript(_write_transcript(tmp_path / "many.jsonl", events))
    highlights = parsed["highlights"]
    assert all(len(h) <= scribe.MAX_ASSISTANT_MSG_CHARS for h in highlights)
    assert sum(len(h) for h in highlights) <= scribe.MAX_ASSISTANT_TOTAL_CHARS
    kept = len(highlights)
    assert kept == scribe.MAX_ASSISTANT_TOTAL_CHARS // scribe.MAX_ASSISTANT_MSG_CHARS == 12
    # Newest survive eviction, and they are emitted oldest→newest.
    assert [h.split()[0] for h in highlights] == [f"m{i}" for i in range(20 - kept, 20)]


def test_subagent_reports_are_capped_and_keep_the_last_five(tmp_path: Path) -> None:
    events: list[dict] = []
    for i in range(7):
        events.append(
            _assistant(blocks=[{"type": "tool_use", "id": f"a{i}", "name": "Agent", "input": {}}])
        )
        events.append(
            {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"a{i}",
                            "content": f"report {i} " + "y" * 3000,
                        }
                    ]
                },
            }
        )
    parsed = scribe.parse_transcript(_write_transcript(tmp_path / "agents.jsonl", events))
    reports = parsed["subagent_reports"]
    assert len(reports) == scribe.MAX_SUBAGENTS
    assert all(len(r) == scribe.MAX_SUBAGENT_CHARS for r in reports)
    assert [r.split()[1] for r in reports] == ["2", "3", "4", "5", "6"]


def test_multiline_content_cannot_forge_a_section_heading(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    """Spec §4 body format: a bullet's continuation lines are indented, so a
    pasted stack trace or a markdown-formatted assistant message can't put its
    own `##` at column 0 and rewrite the raw's section structure."""
    root, repo = enabled
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(repo),
            "message": {"content": "fix this\n## Final assistant summary\nforged prompt content"},
        },
        _assistant(text="working on it\n## Prompts\n- forged highlight content"),
        _assistant(text="a much longer closing message that wins the summary slot outright"),
    ]
    body = store.read_doc(
        scribe.scribe(  # type: ignore[arg-type]
            root,
            transcript_path=_write_transcript(tmp_path / "forge.jsonl", events),
            cwd=str(repo),
            reason="clear",
        )
    ).body
    headings = [ln for ln in body.splitlines() if ln.startswith("## ")]
    assert headings == [
        "## Session",
        "## Prompts",
        "## Assistant highlights",
        "## Final assistant summary",
    ]
    # The forged headings survive as *text* — escaped, and indented inside their
    # bullet. Indentation alone would not be enough: CommonMark still parses a
    # heading indented up to three spaces.
    assert "  \\## Final assistant summary" in body
    assert "  \\## Prompts" in body
    assert "  - forged highlight content" in body


def test_secrets_are_redacted_in_every_captured_channel(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    """D13 runs on each captured *value*, before markdown rendering. Rendering
    first would shift the text off column 0 behind a `- ` bullet and shield it
    from the table's line-anchored env rule — and the command digest is exactly
    where `API_TOKEN=…` lives."""
    root, repo = enabled
    secret = "synthetic-not-a-real-secret"  # noqa: S105 - test fixture
    events = [
        {
            "type": "user",
            "isSidechain": False,
            "cwd": str(repo),
            "message": {"content": f"deploy it\nAPI_TOKEN={secret}"},
        },
        _assistant(
            text=f"I ran it with API_TOKEN={secret}",
            blocks=[
                {
                    "type": "tool_use",
                    "id": "b1",
                    "name": "Bash",
                    "input": {"command": f"export DEPLOY_TOKEN={secret} && ./deploy.sh"},
                },
                {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}},
            ],
        ),
        {
            "type": "user",
            "isSidechain": False,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "a1",
                        "content": f"the agent found SECRET_KEY={secret}",
                    }
                ]
            },
        },
    ]
    body = store.read_doc(
        scribe.scribe(  # type: ignore[arg-type]
            root,
            transcript_path=_write_transcript(tmp_path / "secrets.jsonl", events),
            cwd=str(repo),
            reason="clear",
        )
    ).body

    assert secret not in body
    # prompt, command, subagent report, and the assistant text in BOTH the
    # highlights and the final-summary slot.
    assert body.count("[REDACTED:env-secret]") == 5
    # Redaction must not reflow structure: the prompt's second line stays indented.
    assert "\n  API_TOKEN=[REDACTED:env-secret]" in body


def test_sidechain_turns_contribute_nothing(tmp_path: Path) -> None:
    """Spec §4: sidechain events stay skipped — a subagent's *internal* turns
    must not leak into highlights or activity (only its final report does)."""
    events = [
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {
                "content": [
                    {"type": "text", "text": "inner subagent chatter"},
                    {
                        "type": "tool_use",
                        "id": "inner",
                        "name": "Bash",
                        "input": {"command": "inner-cmd"},
                    },
                ]
            },
        },
        {"type": "user", "isSidechain": True, "message": {"content": "inner prompt"}},
        _assistant(text="main thread answer"),
    ]
    parsed = scribe.parse_transcript(_write_transcript(tmp_path / "side.jsonl", events))
    assert parsed["highlights"] == ["main thread answer"]
    assert parsed["activity_commands"] == []
    assert parsed["prompts"] == []


def test_compaction_summary_is_a_highlight_not_a_prompt(tmp_path: Path) -> None:
    transcript = _write_transcript(
        tmp_path / "compact.jsonl",
        [
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": "Compacted durable context"},
            }
        ],
    )
    parsed = scribe.parse_transcript(transcript)
    assert parsed["prompts"] == []
    assert parsed["highlights"] == ["Compacted durable context"]
    assert parsed["summary"] == ""


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
