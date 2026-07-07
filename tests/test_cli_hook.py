"""Integration tests for `neurobase hook claude ...` (Phase 4)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters.claude import recall
from neurobase.cli import app
from neurobase.core import projects, store

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never spawn a real detached `curate` from tests.
    monkeypatch.setattr(recall, "spawn_curate_if_stale", lambda root, cwd: None)


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)
    return root, repo


def test_session_end_writes_raw_from_stdin(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "cwd": str(repo),
                "message": {"role": "user", "content": "remember: use tabs"},
            }
        ),
        encoding="utf-8",
    )
    payload = json.dumps(
        {
            "session_id": "s1",
            "transcript_path": str(transcript),
            "cwd": str(repo),
            "reason": "clear",
        }
    )
    result = runner.invoke(
        app, ["hook", "claude", "session-end", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    raws = store.list_raw(root, "myrepo", unconsumed_only=False)
    assert len(raws) == 1
    assert "remember: use tabs" in raws[0].body


def test_session_start_emits_additional_context(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nUse tabs, not spaces.")
    payload = json.dumps({"cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "claude", "session-start", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    out = json.loads(result.output.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Use tabs, not spaces." in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_no_nodes_emits_nothing(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    payload = json.dumps({"cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "claude", "session-start", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_hook_always_exits_zero_on_garbage(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    # garbage stdin, missing transcript — must still exit 0, write nothing.
    result = runner.invoke(
        app, ["hook", "claude", "session-end", "--root", str(root)], input="not json at all"
    )
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []


def test_hook_unknown_agent_exits_zero(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    result = runner.invoke(app, ["hook", "codex", "session-end", "--root", str(root)], input="{}")
    assert result.exit_code == 0  # codex is Phase 5; no-op for now


def test_hook_no_args_exits_zero() -> None:
    result = runner.invoke(app, ["hook"], input="")
    assert result.exit_code == 0


def test_session_end_scribe_failure_exits_zero(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("scribe blew up")

    monkeypatch.setattr(cli.scribe, "scribe", boom)
    payload = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "claude", "session-end", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0  # fail-safe: never wedge teardown
