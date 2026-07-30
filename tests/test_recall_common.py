"""Recall must honor the configured [inject].max_chars, not the hardcoded default
(spec §10), and must never emit a partial header (spec §3, review F2).
build_context is shared, so both the Claude and Codex adapters inherit both.

`test_cap_too_small_for_the_header_injects_nothing` is the F2 regression pin —
stash-verified to fail against the pre-F2 `_assemble`, which returned a
header-only fragment there. The surrounding cap tests characterize the contract
at its other boundaries; see the mutation note on the truncation test."""

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


def _header_for(root: Path, project: str = "myrepo") -> str:
    """The exact header ``build_context`` emits for this store. Its length varies
    with the ``memory_dir`` path, so every cap below is *derived* from it rather
    than hardcoded — review F2 called out that a fixed cap silently means
    different things under a short tmp path and a real one."""
    return recall_common.HEADER.format(memory_dir=store.memory_dir(project, root))


def test_cap_too_small_for_the_header_injects_nothing(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header is indivisible (spec §3): when the cap cannot fit the whole
    header plus at least one character of node body, recall injects NOTHING
    rather than a header-only or mid-sentence fragment. Review F2 — the previous
    behavior emitted the first `cap` characters of an incomplete header, cutting
    through the trust framing that makes the payload safe to read, and carrying
    no node content to justify it."""
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    store.write_node(root, "myrepo", "b-node", "B" * 250)
    header = _header_for(root)
    # Exactly one char short of viable: header + joiner fits, no body room left.
    _set_cap(monkeypatch, len(header) + len(recall_common._JOINER))

    assert claude_recall.build_context(root, repo) is None


def test_smallest_viable_cap_still_carries_the_complete_header(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One char of body room is the boundary: at exactly that cap we inject, and
    what we inject still opens with the entire header."""
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    header = _header_for(root)
    cap = len(header) + len(recall_common._JOINER) + 1
    _set_cap(monkeypatch, cap)

    ctx = claude_recall.build_context(root, repo)
    assert ctx == header + recall_common._JOINER + "A"
    assert len(ctx) <= cap


def test_configured_cap_keeps_whole_header_and_real_node_content(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Framing integrity AND node presence together (review F2): a cap sized for
    the header plus one node must yield the complete header, that node's body in
    full, and no trailing node — not merely a length under the cap."""
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    store.write_node(root, "myrepo", "b-node", "B" * 250)
    header = _header_for(root)
    cap = len(header) + len(recall_common._JOINER) + 250
    _set_cap(monkeypatch, cap)

    ctx = claude_recall.build_context(root, repo)
    assert ctx is not None
    assert ctx.startswith(header)  # complete framing, not a prefix of it
    assert "A" * 250 in ctx  # the node that fits survives intact
    assert "B" * 250 not in ctx  # trailing node dropped, never truncated
    assert len(ctx) <= cap


def test_oversized_first_node_truncates_the_body_never_the_header(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single node larger than the cap is the one truncating case, and the cut
    falls on the BODY, sized by `room`.

    Mutation note: this does NOT discriminate pre- from post-F2. Whenever the
    header itself fits, `(header + _JOINER + body)[:cap]` and
    `header + _JOINER + body[:room]` are the same string — the two forms only
    diverge once the cap is too small for the header, which is
    `test_cap_too_small_for_the_header_injects_nothing`'s case (verified: that
    test is the one of these five that fails against the pre-F2 `_assemble`).
    What this pins is the going-forward contract: a refactor that reintroduced
    slicing over the assembled string, or prepended anything ahead of the
    header, would overflow the cap or cut the framing and fail here."""
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 500)
    header = _header_for(root)
    room = 100
    cap = len(header) + len(recall_common._JOINER) + room
    _set_cap(monkeypatch, cap)

    ctx = claude_recall.build_context(root, repo)
    assert ctx == header + recall_common._JOINER + "A" * room


def test_both_adapters_share_the_cap(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    store.write_node(root, "myrepo", "a-node", "A" * 250)
    store.write_node(root, "myrepo", "b-node", "B" * 250)
    header = _header_for(root)
    cap = len(header) + len(recall_common._JOINER) + 250
    _set_cap(monkeypatch, cap)
    # Both adapters re-export the same build_context, so both see the cap. Assert
    # on real content: at a viable cap an `is None == is None` parity check would
    # pass even if both adapters injected nothing.
    ctx = claude_recall.build_context(root, repo)
    assert ctx == codex_recall.build_context(root, repo)
    assert ctx is not None
    assert ctx.startswith(header)
    assert len(ctx) <= cap


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
