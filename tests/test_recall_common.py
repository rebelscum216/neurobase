"""F2 regression (spec §10): recall must honor the configured [inject].max_chars,
not the hardcoded default. build_context is shared, so both the Claude and Codex
adapters inherit the fix."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from neurobase.adapters import recall_common
from neurobase.adapters.claude import recall as claude_recall
from neurobase.adapters.codex import recall as codex_recall
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


def _set_cap(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    monkeypatch.setattr(
        recall_common, "load_config", lambda: SimpleNamespace(inject=SimpleNamespace(max_chars=cap))
    )


def test_small_configured_cap_is_honored(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    # Two sizable nodes; a 300-char cap must drop the trailing one.
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    store.write_node(root, "myrepo", "b-node", "B" * 250)
    _set_cap(monkeypatch, 300)

    ctx = claude_recall.build_context(root, repo)
    assert ctx is not None
    assert len(ctx) <= 300
    assert "B" * 250 not in ctx  # trailing node dropped by the small cap


def test_both_adapters_share_the_cap(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    store.write_node(root, "myrepo", "b-node", "B" * 250)
    _set_cap(monkeypatch, 300)
    # Both adapters re-export the same build_context, so both see the cap.
    assert claude_recall.build_context(root, repo) == codex_recall.build_context(root, repo)
    assert len(codex_recall.build_context(root, repo) or "") <= 300


def test_default_cap_is_6000_when_config_absent(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "small body")
    # No monkeypatch: load_config() returns the §8 default (6000), nothing dropped.
    ctx = claude_recall.build_context(root, repo)
    assert ctx is not None
    assert "small body" in ctx
