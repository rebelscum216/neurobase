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


# --- G15: --dry-run and --resynth are contradictory ------------------------


def test_dry_run_with_resynth_is_rejected(enabled: tuple[Path, Path]) -> None:
    """G15. `if resynth:` calls `_synthesize` — which writes the node — before the
    first read of `dry_run`, so the combination regenerated the node, called the
    brain, and reported neither, under a flag whose help says "change nothing"."""
    root, repo = enabled
    result = runner.invoke(
        app, ["curate", "--dry-run", "--resynth", "--root", str(root), "--cwd", str(repo)]
    )
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_dry_run_with_resynth_writes_no_node(enabled: tuple[Path, Path]) -> None:
    """The defect itself, pinned by artifact rather than by message: the node is
    what a 4,661-byte file appearing on disk actually meant."""
    root, repo = enabled
    nodes = store.memory_dir("myrepo", root) / "nodes"
    before = sorted(p.name for p in nodes.iterdir()) if nodes.exists() else []
    runner.invoke(
        app, ["curate", "--dry-run", "--resynth", "--root", str(root), "--cwd", str(repo)]
    )
    after = sorted(p.name for p in nodes.iterdir()) if nodes.exists() else []
    assert after == before == []


def test_resynth_alone_still_regenerates_the_node(enabled: tuple[Path, Path]) -> None:
    """The guard must reject the COMBINATION, not --resynth. Without this, a fix
    that simply disabled --resynth would pass the two tests above."""
    root, repo = enabled
    result = runner.invoke(app, ["curate", "--resynth", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    nodes = store.memory_dir("myrepo", root) / "nodes"
    assert [p.name for p in nodes.iterdir()] != []


def test_dry_run_alone_is_still_accepted(enabled: tuple[Path, Path]) -> None:
    """…and symmetrically, --dry-run alone must keep working."""
    root, repo = enabled
    _write_raw(root)
    result = runner.invoke(app, ["curate", "--dry-run", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert "cannot be combined" not in result.output


# --- `neurobase wrap` — decision 8, "derive don't duplicate" ---------------


def _wrap(root: Path, repo: Path, text: str, *args: str):
    return runner.invoke(
        app,
        ["wrap", "--summary", "-", "--root", str(root), "--cwd", str(repo), *args],
        input=text,
    )


def test_wrap_writes_a_v1_raw_marked_agent_authored(enabled: tuple[Path, Path]) -> None:
    """The whole shape in one assertion set: NO `transcript_path` (so the fold
    takes no distill call) and `authority: agent-authored` (so the journal, doctor
    and any UI can name the rung without guessing)."""
    root, repo = enabled

    result = _wrap(root, repo, "## Decisions\n\nPicked A.", "--session-id", "s1")

    assert result.exit_code == 0
    raws = list((store.memory_dir("myrepo", root) / "raw").glob("*.md"))
    assert len(raws) == 1
    doc = store.read_doc(raws[0])
    assert doc["authority"] == "agent-authored"
    assert "transcript_path" not in doc.frontmatter
    assert "capture_version" not in doc.frontmatter
    assert "Picked A." in doc.body


def test_wrap_redacts_the_body_and_the_frontmatter_it_supplies(enabled: tuple[Path, Path]) -> None:
    """ADR-0027 D48: the D13 guarantee is unconditional on `authority`.
    `write_raw` writes the body as-is, so the caller must scrub — nothing about a
    body being agent-authored makes it safe."""
    root, repo = enabled

    _wrap(root, repo, "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA end", "--session-id", "s1")

    doc = store.read_doc(next((store.memory_dir("myrepo", root) / "raw").glob("*.md")))
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA" not in doc.body
    assert "REDACTED" in doc.body


def test_wrap_refuses_an_empty_digest(enabled: tuple[Path, Path]) -> None:
    """It stores what its caller authored; it never authors one itself, so an
    empty stdin is a usage error rather than an invitation to generate text."""
    root, repo = enabled

    result = _wrap(root, repo, "   \n  \n")

    assert result.exit_code == 1
    assert not list((store.memory_dir("myrepo", root) / "raw").glob("*.md"))


def test_wrap_refuses_an_unenabled_project(tmp_path: Path) -> None:
    """A store the repo is not registered in must not silently collect digests."""
    root = tmp_path / "store"
    repo = tmp_path / "elsewhere"
    repo.mkdir()

    result = runner.invoke(
        app,
        ["wrap", "--summary", "-", "--root", str(root), "--cwd", str(repo)],
        input="## Decisions\n\nPicked A.",
    )

    assert result.exit_code == 1
    assert "Not an enabled project" in result.output


def test_wrap_echoes_a_generated_session_id_when_none_is_given(
    enabled: tuple[Path, Path],
) -> None:
    """Omitting `--session-id` must not produce a silent junk identity — the id
    used is reported, so the caller can correlate it with the session's captures."""
    root, repo = enabled

    result = _wrap(root, repo, "## Decisions\n\nPicked A.")

    assert result.exit_code == 0
    assert json.loads(result.output)["session_id"]


def test_wrap_body_reaches_the_fold_unchanged(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consumption half of §11.4, end to end: a wrapped body is what the curator
    plans over — no distill, no transcript read. Pinned at the brain boundary,
    because the CLI's dry-run prints the returned PLAN, not the payload sent."""
    root, repo = enabled
    seen: list[str] = []

    class _RecordingBrain(_FakeBrain):
        def plan_json(self, system: str, user: str) -> dict:
            seen.append(user)
            return {"upserts": [], "tombstones": []}

    monkeypatch.setattr(cli, "resolve_brain", lambda config: (_RecordingBrain(), None))
    _wrap(root, repo, "## Decisions\n\nChose the derive option.", "--session-id", "s1")

    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo)])

    assert result.exit_code == 0
    assert seen, "the fold never called the brain"
    assert "Chose the derive option." in seen[0]
