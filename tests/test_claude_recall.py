"""Tests for the Claude recall (spec §3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from neurobase.adapters.claude import recall
from neurobase.core import projects, store


@pytest.fixture
def enabled(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)
    return root, repo


def test_no_project_returns_none(tmp_path: Path) -> None:
    root = tmp_path / "store"
    untracked = tmp_path / "untracked"
    untracked.mkdir()
    assert recall.build_context(root, untracked) is None
    assert recall.emit(root, untracked) is None


def test_no_nodes_returns_none(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    assert recall.build_context(root, repo) is None


def test_builds_context_with_framing_header(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nThe login bug is fixed.")
    ctx = recall.build_context(root, repo)
    assert ctx is not None
    assert "recalled project memory" in ctx
    assert "background context" in ctx and "not as instructions" in ctx
    assert "The login bug is fixed." in ctx


def test_emit_shape(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "myrepo-status", "# Status\n\nbody")
    out = recall.emit(root, repo)
    assert out is not None
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "body" in payload["hookSpecificOutput"]["additionalContext"]


def test_nodes_joined_alphabetically(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "bbb-node", "BODY B")
    store.write_node(root, "myrepo", "aaa-node", "BODY A")
    ctx = recall.build_context(root, repo)
    assert ctx is not None
    assert ctx.index("BODY A") < ctx.index("BODY B")
    assert "\n\n---\n\n" in ctx


def test_cap_drops_whole_trailing_nodes() -> None:
    header = "H"
    bodies = ["A" * 3000, "B" * 3000, "C" * 3000]
    out = recall._assemble(header, bodies, cap=6000)
    assert "A" * 3000 in out
    assert "B" * 3000 not in out  # second node would push over 6000 → dropped whole
    assert "C" * 3000 not in out


def test_cap_truncates_single_oversized_first_node() -> None:
    header = "H"
    bodies = ["A" * 10000]
    out = recall._assemble(header, bodies, cap=6000)
    assert len(out) == 6000  # truncated mid-node only because it's alone and overflows


def test_emit_fail_safe_swallows_errors(enabled: tuple[Path, Path], monkeypatch) -> None:
    root, repo = enabled

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(recall, "build_context", boom)
    assert recall.emit(root, repo) is None  # any error ⇒ emit nothing
