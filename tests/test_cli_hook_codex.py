"""Integration tests for `neurobase hook codex …` (spec §5 hook wiring)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neurobase.adapters.codex import recall as codex_recall
from neurobase.adapters.codex import scribe as codex_scribe
from neurobase.cli import app
from neurobase.core import projects, store

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_recall, "spawn_curate_if_stale", lambda root, cwd: None)


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)
    return root, repo


def _rollout(path: Path, cwd: str, *, session_id: str = "019fsess") -> Path:
    events = [
        {
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "timestamp": "2026-07-05T23:21:06Z",
                "cwd": cwd,
                "git": {"branch": "main"},
            },
        },
        {"type": "event_msg", "payload": {"type": "user_message", "message": "teach codex a fact"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "noted"}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def test_codex_session_start_emits_context(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nPrefer pathlib.")
    result = runner.invoke(
        app,
        ["hook", "codex", "session-start", "--root", str(root)],
        input=json.dumps({"cwd": str(repo)}),
    )
    assert result.exit_code == 0
    out = json.loads(result.output.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Prefer pathlib." in out["hookSpecificOutput"]["additionalContext"]


def test_codex_stop_writes_raw_from_transcript_path(
    enabled: tuple[Path, Path], tmp_path: Path
) -> None:
    root, repo = enabled
    rollout = _rollout(tmp_path / "rollout-1.jsonl", str(repo))
    payload = json.dumps(
        {"session_id": "019fsess", "transcript_path": str(rollout), "cwd": str(repo)}
    )
    result = runner.invoke(app, ["hook", "codex", "stop", "--root", str(root)], input=payload)
    assert result.exit_code == 0
    raws = store.list_raw(root, "myrepo", unconsumed_only=False)
    assert len(raws) == 1
    assert raws[0].get("agent") == "codex"
    assert "teach codex a fact" in raws[0].body


def test_codex_stop_rollout_flag_override(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    rollout = _rollout(tmp_path / "rollout-1.jsonl", str(repo))
    # No transcript_path in the payload; --rollout supplies it (testing path).
    result = runner.invoke(
        app,
        ["hook", "codex", "stop", "--rollout", str(rollout), "--cwd", str(repo), "--root", str(root)],  # noqa: E501
        input="{}",
    )
    assert result.exit_code == 0
    assert len(store.list_raw(root, "myrepo", unconsumed_only=False)) == 1


def test_codex_notify_discovers_rollout_and_writes(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    sessions = tmp_path / "sessions" / "2026" / "07" / "05"
    sessions.mkdir(parents=True)
    _rollout(sessions / "rollout-abc.jsonl", str(repo), session_id="019fsess")
    monkeypatch.setattr(codex_scribe, "_SESSIONS_ROOT", tmp_path / "sessions")

    notify = json.dumps(
        {"type": "agent-turn-complete", "thread-id": "019fsess", "cwd": str(repo)}
    )
    result = runner.invoke(app, ["hook", "codex", "notify", notify, "--root", str(root)], input="")
    assert result.exit_code == 0
    raws = store.list_raw(root, "myrepo", unconsumed_only=False)
    assert len(raws) == 1
    assert raws[0].get("agent") == "codex"


def test_codex_notify_no_rollout_exits_zero(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = enabled
    monkeypatch.setattr(codex_scribe, "_SESSIONS_ROOT", tmp_path / "empty")
    notify = json.dumps({"type": "agent-turn-complete", "thread-id": "nope"})
    result = runner.invoke(app, ["hook", "codex", "notify", notify, "--root", str(root)], input="")
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []


def test_codex_notify_thread_id_mismatch_captures_nothing(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex F1: a present-but-non-matching rollout must not be captured — the
    thread-id cross-check fails closed rather than grabbing the newest."""
    root, repo = enabled
    sessions = tmp_path / "sessions" / "2026" / "07" / "05"
    sessions.mkdir(parents=True)
    _rollout(sessions / "rollout-other.jsonl", str(repo), session_id="019fsess")
    monkeypatch.setattr(codex_scribe, "_SESSIONS_ROOT", tmp_path / "sessions")

    notify = json.dumps({"type": "agent-turn-complete", "thread-id": "UNRELATED", "cwd": str(repo)})
    result = runner.invoke(app, ["hook", "codex", "notify", notify, "--root", str(root)], input="")
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []


def test_codex_stop_garbage_exits_zero(enabled: tuple[Path, Path]) -> None:
    root, _ = enabled
    result = runner.invoke(
        app, ["hook", "codex", "stop", "--root", str(root)], input="not json at all"
    )
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []
