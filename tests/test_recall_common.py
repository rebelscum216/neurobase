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
        recall_common,
        "load_config",
        lambda: SimpleNamespace(
            inject=SimpleNamespace(max_chars=cap),
            # build_context also reads the auto-enable config; an empty section
            # leaves the enabled fixture's already-registered project untouched.
            enable=SimpleNamespace(auto_enable_roots=[], denylist=[]),
        ),
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


def test_recall_skips_unreadable_node(enabled: tuple[Path, Path]) -> None:
    """A directory named ``*.md`` in nodes/ (read_doc's read_text raises
    IsADirectoryError, an OSError) must be skipped, never crash recall —
    build_context runs in the SessionStart hook and has to fail safe. The healthy
    node still reaches the injected context."""
    root, repo = enabled
    store.write_node(root, "myrepo", "good-node", "the good body")
    (store.memory_dir("myrepo", root) / "nodes" / "bad.md").mkdir()
    ctx = claude_recall.build_context(root, repo)
    assert ctx is not None
    assert "the good body" in ctx


def test_read_recall_does_not_create_store_toml(tmp_path: Path) -> None:
    # ADR-0015: build_context now opens a READ handle, which never writes. A
    # project can be registered (registry.toml) before the store is initialized
    # (no store.toml); recall must read as empty and must NOT create store.toml as
    # a side effect the way the old ensure_store_metadata guard call did.
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")  # writes registry.toml only
    assert not (root / "store.toml").exists()

    assert recall_common.build_context(root, repo) is None  # no nodes → inject nothing
    assert not (root / "store.toml").exists()  # READ recall wrote nothing
