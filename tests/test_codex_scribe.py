"""Tests for the Codex scribe (spec §5), driven by the §11.2 rollout fixture."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from neurobase.adapters.codex import scribe
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


def _write_rollout(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _meta(cwd: str, *, ts: str = "2026-07-05T23:21:06Z", branch: str = "main") -> dict:
    return {
        "type": "session_meta",
        "payload": {
            "session_id": "019fsess",
            "id": "019fsess",
            "timestamp": ts,
            "cwd": cwd,
            "originator": "codex_cli",
            "git": {"commit_hash": "abc123", "branch": branch},
        },
    }


def _user(msg: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "user_message", "message": msg, "images": []}}


def _agent(msg: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "agent_message", "message": msg}}


# The §11.2 fixture, verbatim in shape.
def _fixture_events(cwd: str) -> list[dict]:
    return [
        _meta(cwd),
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        _user("Fix the login bug"),
        _agent("Done — the null check was missing in auth.py"),
        {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
    ]


def test_parse_rollout_fields(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    _, repo = enabled
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", _fixture_events(str(repo)))
    parsed = scribe.parse_rollout(rollout)
    assert parsed["prompts"] == ["Fix the login bug"]
    assert parsed["summary"] == "Done — the null check was missing in auth.py"
    assert parsed["cwd"] == str(repo)
    assert parsed["branch"] == "main"
    assert parsed["session_id"] == "019fsess"
    assert parsed["started_at"] == "2026-07-05T23:21:06Z"
    assert parsed["ide_context"] == ""


def test_scribe_writes_raw_with_agent_codex(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", _fixture_events(str(repo)))
    written = scribe.scribe(root, rollout_path=rollout)
    assert written is not None
    doc = store.read_doc(written)
    assert doc.get("agent") == "codex"
    assert doc.get("branch") == "main"
    assert "- agent: codex" in doc.body
    assert "Fix the login bug" in doc.body
    assert "Done — the null check was missing in auth.py" in doc.body
    # Filename derives from the session-start timestamp (per-turn overwrite key).
    assert written.name.startswith("2026-07-05T23-21-06Z_codex_")


def test_per_turn_overwrite_one_raw_last_turn_wins(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    root, repo = enabled
    rollout_path = tmp_path / "rollout-1.jsonl"

    _write_rollout(rollout_path, _fixture_events(str(repo)))
    first = scribe.scribe(root, rollout_path=rollout_path)

    # Next turn: the rollout grows (a new prompt + summary); same session start.
    grown = _fixture_events(str(repo)) + [
        _user("Also add a test"),
        _agent("Added tests/test_auth.py"),
    ]
    _write_rollout(rollout_path, grown)
    second = scribe.scribe(root, rollout_path=rollout_path)

    assert first == second  # same file — overwritten in place
    raws = store.list_raw(root, "myrepo")
    assert len(raws) == 1
    assert "Also add a test" in raws[0].body
    assert "Added tests/test_auth.py" in raws[0].body


def test_ide_wrapper_split(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    wrapped = (
        "# Context from my IDE setup:\n"
        "Open tabs: auth.py, login.py\nActive: auth.py\n"
        "## My request for Codex:\nFix the login bug"
    )
    events = [_meta(str(repo)), _user(wrapped)]
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", events)
    parsed = scribe.parse_rollout(rollout)
    assert parsed["prompts"] == ["Fix the login bug"]  # request kept, wrapper stripped
    assert "Open tabs: auth.py" in parsed["ide_context"]
    assert parsed["ide_context"].startswith("Open tabs")  # context header stripped

    written = scribe.scribe(root, rollout_path=rollout)
    assert written is not None
    assert "## Files in focus (IDE)" in store.read_doc(written).body


def test_ide_context_capped(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    big = "x" * 2000
    wrapped = f"# Context from my IDE setup:\n{big}\n## My request for Codex:\ndo it"
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", [_meta(str(repo)), _user(wrapped)])
    written = scribe.scribe(root, rollout_path=rollout)
    assert written is not None
    body = store.read_doc(written).body
    # The IDE section holds exactly MAX_IDE_CHARS of the context, no more.
    assert "x" * scribe.MAX_IDE_CHARS in body
    assert "x" * (scribe.MAX_IDE_CHARS + 1) not in body


def test_consecutive_duplicate_prompts_skipped(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    events = [
        _meta(str(repo)),
        _user("same prompt"),
        _user("same prompt"),  # thread_rolled_back re-emit
        _user("different"),
    ]
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", events)
    assert scribe.parse_rollout(rollout)["prompts"] == ["same prompt", "different"]


def test_highlights_and_longest_of_last_three_summary(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    _, repo = enabled
    events = [
        _meta(str(repo)),
        _agent("An early message that should only remain a highlight."),
        _agent("The durable discovery is the longest recent assistant message."),
        _agent("Short follow-up."),
        _agent("Tiny end."),
    ]
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", events)
    parsed = scribe.parse_rollout(rollout)
    assert parsed["summary"] == "The durable discovery is the longest recent assistant message."
    assert parsed["highlights"] == [
        "An early message that should only remain a highlight.",
        "The durable discovery is the longest recent assistant message.",
        "Short follow-up.",
        "Tiny end.",
    ]


def test_highlights_share_the_agent_agnostic_budget(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    """Spec §8's assistant bounds are agent-agnostic: the Codex scribe evicts
    exactly like the Claude one (both go through ``scribe_common``)."""
    _, repo = enabled
    events = [_meta(str(repo))] + [_agent(f"m{i} " + "x" * 600) for i in range(20)]
    parsed = scribe.parse_rollout(_write_rollout(tmp_path / "rollout-2.jsonl", events))
    highlights = parsed["highlights"]
    assert all(len(h) <= scribe.MAX_ASSISTANT_MSG_CHARS for h in highlights)
    assert sum(len(h) for h in highlights) <= scribe.MAX_ASSISTANT_TOTAL_CHARS
    assert [h.split()[0] for h in highlights] == [f"m{i}" for i in range(8, 20)]


def test_ide_context_and_summary_cannot_forge_headings_or_hide_secrets(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    """The IDE context is a section *body*, not a bullet — and it lands before
    `## Prompts`, so an un-neutralized heading inside it shadows every section
    that follows. It goes through the same structural + D13 handling as the rest."""
    root, repo = enabled
    secret = "synthetic-not-a-real-secret"  # noqa: S105 - test fixture
    wrapped = (
        "# Context from my IDE setup:\n"
        "open files: a.py\n"
        "## Prompts\n"
        "- forged IDE bullet\n"
        f"export api_token={secret}\n"  # command position ⇒ scrubbed even lowercase
        "Setext forgery\n"
        "---\n"
        "## My request for Codex:\n"
        "ship it"
    )
    events = [
        _meta(str(repo)),
        _user(wrapped),
        _agent("done\n## Session\nforged summary heading"),
    ]
    written = scribe.scribe(
        root, rollout_path=_write_rollout(tmp_path / "rollout-3.jsonl", events), cwd=str(repo)
    )
    assert written is not None
    body = store.read_doc(written).body

    assert secret not in body
    assert [ln for ln in body.splitlines() if ln.startswith("## ")] == [
        "## Session",
        "## Files in focus (IDE)",
        "## Prompts",
        "## Assistant highlights",
        "## Final assistant summary",
    ]
    assert "\\## Prompts" in body  # the forged ATX IDE heading, neutralized
    assert "\\## Session" in body  # the forged summary heading, neutralized
    assert "\\---" in body  # the forged Setext underline, neutralized


def test_redaction_applied_before_write(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    secret = "sk-ant-api03-" + "A" * 40  # noqa: S105 - test fixture, not a real key
    events = [_meta(str(repo)), _user(f"my key is {secret}"), _agent("ok")]
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", events)
    written = scribe.scribe(root, rollout_path=rollout)
    assert written is not None
    assert secret not in store.read_doc(written).body


def test_no_project_tree_writes_nothing(tmp_path: Path) -> None:
    # Registered project but no memory tree (opt-in): write nothing.
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", _fixture_events(str(repo)))
    assert scribe.scribe(root, rollout_path=rollout) is None


def test_untracked_dir_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "store"
    untracked = tmp_path / "untracked"
    untracked.mkdir()
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", _fixture_events(str(untracked)))
    assert scribe.scribe(root, rollout_path=rollout) is None


def test_empty_capture_writes_nothing(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", [_meta(str(repo))])  # no turns
    assert scribe.scribe(root, rollout_path=rollout) is None


def test_consumed_raw_retries_with_fresh_filename(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    root, repo = enabled
    rollout = _write_rollout(tmp_path / "rollout-1.jsonl", _fixture_events(str(repo)))
    first = scribe.scribe(root, rollout_path=rollout)
    assert first is not None

    # Simulate the curator having folded (consumed) this raw mid-session.
    doc = store.read_doc(first)
    store.write_doc(first, {**doc.frontmatter, "consumed": True}, doc.body)

    second = scribe.scribe(root, rollout_path=rollout)
    assert second is not None
    assert second != first  # fresh filename, not an overwrite of the consumed one


def test_discover_rollout_newest_and_session_match(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions" / "2026" / "07" / "05"
    sessions.mkdir(parents=True)
    older = _write_rollout(sessions / "rollout-old.jsonl", [_meta("/x")])  # session_id 019fsess
    newer = sessions / "rollout-new.jsonl"
    _write_rollout(
        newer,
        [
            {
                "type": "session_meta",
                "payload": {
                    "session_id": "OTHER",
                    "timestamp": "2026-07-05T23:59:00Z",
                    "cwd": "/y",
                },
            }
        ],
    )
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    root = tmp_path / "sessions"

    # No session id → newest by mtime.
    assert scribe.discover_rollout(sessions_root=root) == newer
    # Session id cross-check picks the matching (older) rollout.
    assert scribe.discover_rollout(session_id="019fsess", sessions_root=root) == older
    # mtime floor excludes the older one.
    assert scribe.discover_rollout(min_mtime=1_500_000, sessions_root=root) == newer


def test_discover_rollout_none_when_empty(tmp_path: Path) -> None:
    assert scribe.discover_rollout(sessions_root=tmp_path / "nope") is None


def test_discover_rollout_fails_closed_on_session_mismatch(tmp_path: Path) -> None:
    """Codex F1 (spec §5/§11.4): a thread/session id is a hard requirement —
    a newer, non-matching rollout must NOT be captured; discovery returns None."""
    sessions = tmp_path / "sessions" / "2026" / "07" / "05"
    sessions.mkdir(parents=True)
    _write_rollout(sessions / "rollout-new.jsonl", [_meta("/x", ts="2026-07-05T23:59:00Z")])
    root = tmp_path / "sessions"
    # Newest exists, but its session_id ("019fsess") != the requested id.
    assert scribe.discover_rollout(session_id="DIFFERENT", sessions_root=root) is None
