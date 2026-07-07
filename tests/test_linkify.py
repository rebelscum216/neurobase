"""Tests for linkify (spec §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from neurobase.core import linkify, store


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store"


def test_curated_lineage_block_from_provenance_and_supersedes(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(
        root,
        "proj",
        "fact-a",
        "body",
        provenance=["raw/2026-07-07T12-00-00Z_claude_abc.md"],
        supersedes=["old-fact"],
    )
    linkify.linkify(root, "proj")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert "## Lineage" in doc.body
    assert "**Sources:** [[2026-07-07T12-00-00Z_claude_abc]]" in doc.body
    assert "**Supersedes:** [[old-fact]]" in doc.body
    assert linkify.LINEAGE_START in doc.body and linkify.LINEAGE_END in doc.body


def test_curated_no_block_when_no_lineage(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "body")  # no provenance/supersedes
    linkify.linkify(root, "proj")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert linkify.LINEAGE_START not in doc.body


def test_node_synthesized_from_links_active_facts(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "a")
    store.upsert_curated(root, "proj", "fact-b", "b")
    store.write_node(root, "proj", "proj-status", "# Status\n\nbody")
    linkify.linkify(root, "proj")
    node = store.read_doc(store.memory_dir("proj", root) / "nodes" / "proj-status.md")
    assert "## Synthesized from" in node.body
    assert "[[fact-a]]" in node.body and "[[fact-b]]" in node.body


def test_idempotent_no_stacking(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "body", provenance=["raw/r1.md"])
    linkify.linkify(root, "proj")
    linkify.linkify(root, "proj")
    linkify.linkify(root, "proj")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc.body.count(linkify.LINEAGE_START) == 1


def test_block_removed_when_lineage_clears(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1", provenance=["raw/r1.md"])
    linkify.linkify(root, "proj")
    # Re-upsert without provenance/supersedes (supersedes=[] clears it).
    store.upsert_curated(root, "proj", "fact-a", "v2", provenance=[], supersedes=[])
    # Provenance merges (still has raw/r1.md), so block stays — verify that first.
    linkify.linkify(root, "proj")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert "[[r1]]" in doc.body

    # Now a genuinely lineage-free fact loses the block on relinkify.
    store.write_doc(
        store.memory_dir("proj", root) / "curated" / "fact-a.md",
        {"name": "fact-a", "status": "active", "supersedes": [], "provenance": []},
        f"# fact\n\n{linkify.LINEAGE_START}\n## Lineage\nstale\n{linkify.LINEAGE_END}",
    )
    linkify.linkify(root, "proj")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert linkify.LINEAGE_START not in doc.body


def test_frontmatter_preserved_byte_for_byte(root: Path) -> None:
    """Linkify must touch only the body — frontmatter bytes unchanged."""
    store.ensure_tree("proj", root)
    path = store.memory_dir("proj", root) / "curated" / "fact-a.md"
    # Write a doc with frontmatter whose exact serialization we control.
    original = "---\nname: fact-a\nstatus: active\nprovenance:\n- raw/r1.md\n---\n\n# fact body\n"
    path.write_text(original, encoding="utf-8")
    linkify.linkify(root, "proj")
    new_text = path.read_text(encoding="utf-8")
    # The frontmatter section (between the --- fences) is byte-identical.
    original_fm = original.split("---\n", 2)[1]
    new_fm = new_text.split("---\n", 2)[1]
    assert new_fm == original_fm
    assert "## Lineage" in new_text  # body did change


def test_raw_and_tombstones_never_modified(root: Path) -> None:
    store.ensure_tree("proj", root)
    mem = store.memory_dir("proj", root)
    raw_path = mem / "raw" / "r1.md"
    raw_path.write_text("---\nagent: claude\n---\n\nraw body\n", encoding="utf-8")
    tomb_path = mem / ".tombstones" / "old.md"
    tomb_path.write_text("---\nname: old\n---\n\ntomb body\n", encoding="utf-8")
    raw_before = raw_path.read_text()
    tomb_before = tomb_path.read_text()
    store.upsert_curated(root, "proj", "fact-a", "body", provenance=["raw/r1.md"])
    linkify.linkify(root, "proj")
    assert raw_path.read_text() == raw_before
    assert tomb_path.read_text() == tomb_before
