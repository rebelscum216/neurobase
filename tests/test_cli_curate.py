"""Integration tests for `neurobase curate` (Phase 3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.brain.base import Brain
from neurobase.cli import app
from neurobase.core import locks, store
from neurobase.core.store_handle import StoreMode, open_store

runner = CliRunner()


class _FakeBrain:
    name = "fake"

    def plan_json(self, system: str, user: str) -> dict:
        return {
            "upserts": [{"slug": "fact-a", "body": "durable fact", "from_raw": ["r1.md"]}],
            "tombstones": [],
        }

    def text(self, system: str, user: str) -> str:
        return "# Node\n\ncurrent work"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"
    runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    # Force the CLI to resolve our fake brain regardless of the dev machine.
    monkeypatch.setattr(cli, "resolve_brain", lambda config: (_FakeBrain(), None))
    return root, repo


def _write_raw(root: Path, name: str = "r1.md") -> None:
    store.write_doc(
        store.memory_dir("myrepo", root) / "raw" / name,
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": "2026-07-07T12:00:00Z",
            "consumed": False,
        },
        "fix the login bug",
    )


def test_curate_folds_raw_into_facts(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    _write_raw(root)
    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["status"] == "ok"
    assert summary["upserts"] == 1
    assert (store.memory_dir("myrepo", root) / "curated" / "fact-a.md").exists()


def test_curate_dry_run_prints_plan_changes_nothing(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    _write_raw(root)
    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo), "--dry-run"])
    assert result.exit_code == 0
    assert "fact-a" in result.output
    assert not (store.memory_dir("myrepo", root) / "curated" / "fact-a.md").exists()


def test_curate_untracked_dir_exits_nonzero(tmp_path: Path) -> None:
    untracked = tmp_path / "untracked"
    untracked.mkdir()
    root = tmp_path / "store"
    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(untracked)])
    assert result.exit_code == 1
    assert "Not an enabled project" in result.output


def test_curate_if_stale_skips_recent(enabled: tuple[Path, Path], monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    root, repo = enabled
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    store.write_doc(
        store.memory_dir("myrepo", root) / "raw" / "r1.md",
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": recent,
            "consumed": False,
        },
        "recent capture",
    )
    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo), "--if-stale"])
    assert result.exit_code == 0
    assert "Not stale" in result.output
    assert not (store.memory_dir("myrepo", root) / "curated" / "fact-a.md").exists()


def test_curate_busy_lock_skips_before_brain(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    _write_raw(root)

    def fail_if_resolved(config):
        raise AssertionError("a lock loser must not resolve or invoke a brain")

    monkeypatch.setattr(cli, "resolve_brain", fail_if_resolved)
    with locks.try_curate_lock(open_store(root, StoreMode.READ), "myrepo") as acquired:
        assert acquired
        result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert "already running" in result.output
    assert not (store.memory_dir("myrepo", root) / "curated" / "fact-a.md").exists()


def test_status_shows_fact_count_trend_after_curate(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    _write_raw(root)
    runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo)])
    result = runner.invoke(app, ["status", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert "Fact-count trend" in result.output


def test_curate_brain_typing_contract() -> None:
    # The fake satisfies the Brain protocol used by the engine.
    assert isinstance(_FakeBrain(), Brain)
