"""F1 regression (spec §10/D11): the always-on hook paths must refuse a store
whose schema is newer than this binary supports — fail closed (exit 0, capture
nothing, inject nothing), never operate on an incompatible store."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neurobase.adapters.claude import recall as claude_recall
from neurobase.adapters.codex import recall as codex_recall
from neurobase.cli import app
from neurobase.core import projects, store

runner = CliRunner()

NEWER_SCHEMA = 'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n'


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_recall, "spawn_curate_if_stale", lambda root, cwd: None)
    monkeypatch.setattr(codex_recall, "spawn_curate_if_stale", lambda root, cwd: None)


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)  # writes store.toml schema = 1
    return root, repo


def _bump_schema(root: Path) -> None:
    (root / "store.toml").write_text(NEWER_SCHEMA, encoding="utf-8")


def test_claude_session_end_fails_closed(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "cwd": str(repo),
                "message": {"role": "user", "content": "remember: a fact"},
            }
        ),
        encoding="utf-8",
    )
    _bump_schema(root)
    payload = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "claude", "session-end", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []


def test_codex_stop_fails_closed(enabled: tuple[Path, Path], tmp_path: Path) -> None:
    root, repo = enabled
    rollout = tmp_path / "rollout-1.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": "s1",
                        "timestamp": "2026-07-05T23:21:06Z",
                        "cwd": str(repo),
                    },
                },
                {"type": "event_msg", "payload": {"type": "user_message", "message": "a fact"}},
            ]
        ),
        encoding="utf-8",
    )
    _bump_schema(root)
    payload = json.dumps({"transcript_path": str(rollout), "cwd": str(repo), "session_id": "s1"})
    result = runner.invoke(app, ["hook", "codex", "stop", "--root", str(root)], input=payload)
    assert result.exit_code == 0
    assert store.list_raw(root, "myrepo", unconsumed_only=False) == []


def test_claude_session_start_fails_closed(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nUse tabs.")
    _bump_schema(root)
    payload = json.dumps({"cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "claude", "session-start", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_codex_session_start_fails_closed(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nUse tabs.")
    _bump_schema(root)
    payload = json.dumps({"cwd": str(repo)})
    result = runner.invoke(
        app, ["hook", "codex", "session-start", "--root", str(root)], input=payload
    )
    assert result.exit_code == 0
    assert result.output.strip() == ""
