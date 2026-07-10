"""Integration tests for `neurobase seed` (spec §12.3, execution plan
workstream B) — CLI flag validation and project-scope resolution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neurobase.cli import app
from neurobase.core import store
from neurobase.recommender import seed as seed_import

runner = CliRunner()


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    """A store root with `myrepo` registered (via the real `enable` command),
    mirroring `tests/test_cli_curate.py`'s fixture convention."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    root = tmp_path / "store"
    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0
    return root, repo


# --- flag validation (workstream B: "`seed` requires an explicit --from-dir
# or --from-claude-memory; omitting both is a CLI error") -------------------


def test_seed_requires_from_dir_or_from_claude_memory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["seed", "--root", str(tmp_path / "store")])
    assert result.exit_code == 1
    assert "--from-dir" in result.output
    assert "--from-claude-memory" in result.output


def test_seed_project_and_all_projects_mutually_exclusive(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(tmp_path / "store"),
            "--from-claude-memory",
            "--project",
            "x",
            "--all-projects",
        ],
    )
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_seed_all_projects_without_from_claude_memory_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["seed", "--root", str(tmp_path / "store"), "--from-dir", str(tmp_path), "--all-projects"],
    )
    assert result.exit_code == 1
    assert "--all-projects only applies to --from-claude-memory" in result.output


# --- bad --from-dir target: hard CLI error, nothing written -----------------


def test_seed_bad_from_dir_target_is_hard_cli_error(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    missing = repo / "does-not-exist"
    result = runner.invoke(
        app, ["seed", "--root", str(root), "--cwd", str(repo), "--from-dir", str(missing)]
    )
    assert result.exit_code == 1
    curated_dir = store.memory_dir("myrepo", root) / "curated"
    assert not curated_dir.exists() or not list(curated_dir.glob("*.md"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
def test_seed_unreadable_from_dir_target_is_hard_cli_error(enabled: tuple[Path, Path]) -> None:
    """§12.3: an unreadable named --from-dir target must exit non-zero, not
    report a successful empty import (exit 0)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores POSIX permission bits")

    root, repo = enabled
    locked = repo / "locked"
    locked.mkdir()
    (locked / "note.md").write_text("A note behind a locked door.", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = runner.invoke(
            app, ["seed", "--root", str(root), "--cwd", str(repo), "--from-dir", str(locked)]
        )
    finally:
        locked.chmod(0o755)
    assert result.exit_code == 1
    curated_dir = store.memory_dir("myrepo", root) / "curated"
    assert not curated_dir.exists() or not list(curated_dir.glob("*.md"))


# --- --from-dir end to end ---------------------------------------------------


def test_seed_from_dir_writes_curated_facts_and_prints_summary(
    enabled: tuple[Path, Path],
) -> None:
    root, repo = enabled
    notes = repo / "notes"
    notes.mkdir()
    (notes / "topic.md").write_text("Durable fact body.", encoding="utf-8")

    result = runner.invoke(
        app, ["seed", "--root", str(root), "--cwd", str(repo), "--from-dir", str(notes)]
    )

    assert result.exit_code == 0
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["imported"] == ["topic"]
    assert summary["skipped"] == []
    curated = store.memory_dir("myrepo", root) / "curated" / "topic.md"
    assert curated.exists()
    assert store.read_doc(curated).body == "Durable fact body."


def test_seed_from_dir_applies_configured_extra_redact_patterns(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI wires `config.redact.extra_patterns` from the user's real
    config.toml into `import_from_dir` — exercised end to end here rather
    than by passing the kwarg directly to the core function."""
    root, repo = enabled
    home = _isolate_home(tmp_path, monkeypatch)
    config_dir = home / ".config" / "neurobase"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[redact]\nextra_patterns = ["zeta-9000"]\n', encoding="utf-8"
    )

    notes = repo / "notes"
    notes.mkdir()
    (notes / "topic.md").write_text("internal codeword: zeta-9000 is secret", encoding="utf-8")

    result = runner.invoke(
        app, ["seed", "--root", str(root), "--cwd", str(repo), "--from-dir", str(notes)]
    )

    assert result.exit_code == 0
    curated = store.memory_dir("myrepo", root) / "curated" / "topic.md"
    body = store.read_doc(curated).body
    assert "zeta-9000" not in body
    assert "[REDACTED:custom]" in body


def test_seed_from_dir_unresolvable_project_is_hard_cli_error(tmp_path: Path) -> None:
    """`--from-dir` writes into the project resolved from cwd — an
    unregistered cwd is a hard error, same as `curate`/`status`."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "topic.md").write_text("body", encoding="utf-8")
    untracked = tmp_path / "untracked-dir"
    untracked.mkdir()

    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(tmp_path / "store"),
            "--cwd",
            str(untracked),
            "--from-dir",
            str(notes),
        ],
    )
    assert result.exit_code == 1


# --- --from-claude-memory scope resolution (workstream B: "--from-claude-memory
# with neither --project nor --all-projects imports exactly the single project
# resolved from launch cwd; an unresolvable cwd is a CLI error") -------------


def test_seed_from_claude_memory_resolves_single_project_from_cwd(
    enabled: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    _isolate_home(tmp_path, monkeypatch)
    mem_dir = seed_import.claude_memory_dir(repo.resolve())
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("# index", encoding="utf-8")
    (mem_dir / "convention.md").write_text(
        "---\nname: convention\n---\n\nAlways use uv.", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["seed", "--root", str(root), "--cwd", str(repo), "--from-claude-memory"]
    )

    assert result.exit_code == 0
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["imported"] == ["convention"]
    curated = store.memory_dir("myrepo", root) / "curated" / "convention.md"
    assert store.read_doc(curated).body == "Always use uv."


def test_seed_from_claude_memory_unresolvable_cwd_is_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)
    untracked = tmp_path / "untracked-dir"
    untracked.mkdir()

    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(tmp_path / "store"),
            "--cwd",
            str(untracked),
            "--from-claude-memory",
        ],
    )

    assert result.exit_code == 1
    assert "Cannot resolve a project" in result.output


def test_seed_from_claude_memory_project_flag_widens_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--project` names a project other than the one resolved from cwd."""
    _isolate_home(tmp_path, monkeypatch)
    root = tmp_path / "store"
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    result = runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(other_repo)])
    assert result.exit_code == 0

    mem_dir = seed_import.claude_memory_dir(other_repo.resolve())
    mem_dir.mkdir(parents=True)
    (mem_dir / "note.md").write_text("Other project's fact.", encoding="utf-8")

    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(root),
            "--cwd",
            str(unrelated_cwd),
            "--from-claude-memory",
            "--project",
            "other-repo",
        ],
    )

    assert result.exit_code == 0
    curated = store.memory_dir("other-repo", root) / "curated" / "note.md"
    assert store.read_doc(curated).body == "Other project's fact."


def test_seed_from_claude_memory_unknown_project_is_cli_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(tmp_path / "store"),
            "--from-claude-memory",
            "--project",
            "nope",
        ],
    )
    assert result.exit_code == 1
    assert "unknown project" in result.output


def test_seed_all_projects_skips_projects_without_auto_memory_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)
    root = tmp_path / "store"
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    assert runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo_a)]).exit_code == 0
    assert runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo_b)]).exit_code == 0

    # Only repo-a has an auto-memory dir; repo-b's absence must not error.
    mem_dir = seed_import.claude_memory_dir(repo_a.resolve())
    mem_dir.mkdir(parents=True)
    (mem_dir / "note.md").write_text("From repo-a.", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "seed",
            "--root",
            str(root),
            "--cwd",
            str(repo_a),
            "--from-claude-memory",
            "--all-projects",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["imported"] == ["note"]
    assert (store.memory_dir("repo-a", root) / "curated" / "note.md").exists()
    assert not (store.memory_dir("repo-b", root) / "curated").exists() or not list(
        (store.memory_dir("repo-b", root) / "curated").glob("*.md")
    )
