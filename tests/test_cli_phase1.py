"""Integration tests for `neurobase enable` / `neurobase status` (Phase 1)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from neurobase.cli import app
from neurobase.core import projects, store

runner = CliRunner()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_enable_creates_tree_and_registers(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"

    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert "myrepo" in result.output
    assert (root / "projects" / "myrepo" / "memory" / "raw").is_dir()


def test_status_untracked_dir_exits_nonzero(tmp_path: Path) -> None:
    untracked = tmp_path / "untracked"
    untracked.mkdir()
    root = tmp_path / "store"
    result = runner.invoke(app, ["status", "--root", str(root), "--cwd", str(untracked)])
    assert result.exit_code == 1
    assert "Not an enabled project" in result.output


def test_enable_then_handwritten_raw_then_status_shows_it(tmp_path: Path) -> None:
    """The Phase 1 demo: enable → hand-write a raw file → status shows it."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"

    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0

    store.write_raw(
        root,
        "myrepo",
        agent="claude",
        session_id="s1",
        cwd=str(repo),
        branch="main",
        captured_at=datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC),
        body="hand-written capture",
    )

    result = runner.invoke(app, ["status", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert "myrepo" in result.output
    assert "1 unconsumed, 0 consumed" in result.output


def test_enable_refuses_newer_schema_without_touching_registry(tmp_path: Path) -> None:
    """spec §10/D11: refuse to operate on a newer schema — and never partially
    mutate the store (registry.toml) before that refusal."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"
    root.mkdir(parents=True)
    (root / "store.toml").write_text('schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n')

    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 1
    assert not (root / "registry.toml").exists()


def test_status_refuses_newer_schema(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"

    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0

    (root / "store.toml").write_text('schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n')

    result = runner.invoke(app, ["status", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 1
    assert "schema" in result.output


# --- `neurobase disable` (Phase P) -------------------------------------------
#
# The inverse of `enable`, over the same core primitives the web UI's Projects
# directory uses — the CLI must not be the front door that can do less.


def _enabled_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"
    runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    return root, repo


def test_disable_unregisters_and_keeps_the_memory(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    result = runner.invoke(app, ["disable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    assert projects.load_registry(root) == {}
    assert (root / "projects" / "myrepo" / "memory" / "raw").is_dir()
    assert "Memory kept" in result.output


def test_disable_outside_an_enabled_project_exits_nonzero(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = runner.invoke(
        app, ["disable", "--root", str(tmp_path / "store"), "--cwd", str(elsewhere)]
    )
    assert result.exit_code == 1
    assert "Not an enabled project" in result.output


def test_disable_by_slug_does_not_need_the_directory(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = runner.invoke(
        app, ["disable", "--root", str(root), "--slug", "myrepo", "--cwd", str(elsewhere)]
    )
    assert result.exit_code == 0
    assert projects.load_registry(root) == {}


def test_disable_repo_root_removes_only_that_root(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    projects.register_project(root, second, slug="myrepo")
    result = runner.invoke(
        app,
        ["disable", "--root", str(root), "--slug", "myrepo", "--repo-root", str(second)],
    )
    assert result.exit_code == 0
    assert projects.load_registry(root) == {"myrepo": [str(repo)]}


def test_disable_unknown_repo_root_exits_nonzero_without_writing(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    result = runner.invoke(
        app,
        ["disable", "--root", str(root), "--slug", "myrepo", "--repo-root", "/not/a/root"],
    )
    assert result.exit_code == 1
    assert projects.load_registry(root) == {"myrepo": [str(repo)]}


def test_disable_delete_memory_prompts_and_a_refusal_keeps_everything(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    result = runner.invoke(
        app,
        ["disable", "--root", str(root), "--cwd", str(repo), "--delete-memory"],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Left the memory tree in place" in result.output
    assert (root / "projects" / "myrepo" / "memory").is_dir()


def test_disable_delete_memory_with_yes_removes_the_tree(tmp_path: Path) -> None:
    root, repo = _enabled_repo(tmp_path)
    result = runner.invoke(
        app,
        ["disable", "--root", str(root), "--cwd", str(repo), "--delete-memory", "--yes"],
    )
    assert result.exit_code == 0
    assert not (root / "projects" / "myrepo").exists()
    assert projects.load_registry(root) == {}
