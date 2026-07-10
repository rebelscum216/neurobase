"""Round-trip tests enforcing spec §1's store invariants."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neurobase.core import store


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


# --- tree -------------------------------------------------------------


def test_ensure_tree_creates_all_four_subdirs(root: Path) -> None:
    mem = store.ensure_tree("proj", root)
    for sub in ("raw", "curated", "nodes", ".tombstones"):
        assert (mem / sub).is_dir()


def test_ensure_tree_is_idempotent(root: Path) -> None:
    store.ensure_tree("proj", root)
    (root / "projects" / "proj" / "memory" / "raw" / "marker.md").write_text("x")
    store.ensure_tree("proj", root)  # must not wipe existing content
    assert (root / "projects" / "proj" / "memory" / "raw" / "marker.md").exists()


@pytest.mark.parametrize("bad_project", ["", "Not Valid!", "has spaces", "UPPER"])
def test_memory_dir_rejects_invalid_project_slug(root: Path, bad_project: str) -> None:
    """An invalid/empty project must never silently collapse into a bad path
    (e.g. an empty slug joining away to <root>/projects/memory)."""
    with pytest.raises(store.InvalidSlugError):
        store.memory_dir(bad_project, root)
    with pytest.raises(store.InvalidSlugError):
        store.ensure_tree(bad_project, root)


def test_ensure_tree_creates_store_toml(root: Path) -> None:
    """spec §10/D11: <root>/store.toml with schema + created_at."""
    store.ensure_tree("proj", root)
    path = store.store_toml_path(root)
    assert path.exists()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == store.STORE_SCHEMA_VERSION
    assert "created_at" in data


def test_ensure_store_metadata_does_not_rewrite_existing_created_at(root: Path) -> None:
    store.ensure_store_metadata(root)
    first = store.store_toml_path(root).read_text(encoding="utf-8")
    store.ensure_store_metadata(root)
    second = store.store_toml_path(root).read_text(encoding="utf-8")
    assert first == second


def test_ensure_store_metadata_refuses_newer_schema(root: Path) -> None:
    path = store.store_toml_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(store.UnsupportedSchemaError):
        store.ensure_store_metadata(root)


def test_resolve_root_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEUROBASE_ROOT", raising=False)
    explicit = tmp_path / "explicit"
    assert store.resolve_root(explicit) == explicit.resolve()

    env_root = tmp_path / "from-env"
    monkeypatch.setenv("NEUROBASE_ROOT", str(env_root))
    assert store.resolve_root(None) == env_root.resolve()


# --- document format ----------------------------------------------------


def test_doc_round_trips_frontmatter_and_body(root: Path) -> None:
    path = root / "doc.md"
    fm = {"name": "abc", "supersedes": ["x", "y"], "count": 3}
    store.write_doc(path, fm, "hello\nworld\n")
    doc = store.read_doc(path)
    assert doc.frontmatter == fm
    assert doc.body == "hello\nworld\n"


def test_write_doc_is_atomic_no_tmp_left_behind(root: Path) -> None:
    path = root / "doc.md"
    store.write_doc(path, {"a": 1}, "body")
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_read_doc_rejects_missing_frontmatter(root: Path) -> None:
    path = root / "bad.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no frontmatter here")
    with pytest.raises(ValueError, match="frontmatter"):
        store.read_doc(path)


# --- raw/ ---------------------------------------------------------------


def _captured_at() -> datetime:
    return datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)


def test_write_raw_filename_shape(root: Path) -> None:
    store.ensure_tree("proj", root)
    path = store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="AbC-123_def",
        cwd="/tmp/x",
        branch="main",
        captured_at=_captured_at(),
        body="hi",
    )
    assert path.name == "2026-07-07T12-00-00Z_claude_abc123de.md"


def test_write_raw_sid8_fallback_nosid(root: Path) -> None:
    store.ensure_tree("proj", root)
    path = store.write_raw(
        root,
        "proj",
        agent="codex",
        session_id="",
        cwd="/tmp/x",
        branch="",
        captured_at=_captured_at(),
        body="hi",
    )
    assert "_nosid.md" in path.name


def test_list_raw_oldest_first_and_skips_unparseable(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="s2",
        cwd="/x",
        branch="",
        captured_at=datetime(2026, 7, 7, 13, 0, 0, tzinfo=UTC),
        body="second",
    )
    store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="s1",
        cwd="/x",
        branch="",
        captured_at=datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC),
        body="first",
    )
    (store.memory_dir("proj", root) / "raw" / "0000-garbage.md").write_text("not a doc")

    docs = store.list_raw(root, "proj", unconsumed_only=False)
    assert [d.body for d in docs] == ["first", "second"]


def test_mark_consumed_preserves_other_fields_and_body(root: Path) -> None:
    store.ensure_tree("proj", root)
    path = store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="s1",
        cwd="/x",
        branch="main",
        captured_at=_captured_at(),
        body="body text",
    )
    store.mark_consumed(path)
    doc = store.read_doc(path)
    assert doc["consumed"] is True
    assert doc["agent"] == "claude"
    assert doc["cwd"] == "/x"
    assert doc.body == "body text"


def test_list_raw_unconsumed_only_excludes_consumed(root: Path) -> None:
    store.ensure_tree("proj", root)
    path = store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="s1",
        cwd="/x",
        branch="",
        captured_at=_captured_at(),
        body="a",
    )
    store.mark_consumed(path)
    assert store.list_raw(root, "proj", unconsumed_only=True) == []
    assert len(store.list_raw(root, "proj", unconsumed_only=False)) == 1


def _write_codex_turn(root: Path, captured_at: datetime, body: str) -> Path:
    return store.write_raw(
        root,
        "proj",
        agent="codex",
        session_id="s1",
        cwd="/x",
        branch="",
        captured_at=captured_at,
        body=body,
    )


def test_write_raw_overwrites_same_session_until_consumed(root: Path) -> None:
    """The Codex per-turn overwrite trick (spec §5): same captured_at + agent +
    session_id ⇒ same filename ⇒ later turns overwrite in place."""
    store.ensure_tree("proj", root)
    ts = _captured_at()
    path1 = _write_codex_turn(root, ts, "turn 1")
    path2 = _write_codex_turn(root, ts, "turn 1 + 2")
    assert path1 == path2
    assert store.read_doc(path1).body == "turn 1 + 2"
    assert len(list((store.memory_dir("proj", root) / "raw").glob("*.md"))) == 1


def test_write_raw_refuses_to_overwrite_consumed(root: Path) -> None:
    """Mutability rule: once consumed, the owning scribe MUST NOT overwrite —
    it must retry with a fresh captured_at (new filename)."""
    store.ensure_tree("proj", root)
    ts = _captured_at()
    path = _write_codex_turn(root, ts, "turn 1")
    store.mark_consumed(path)

    with pytest.raises(store.RawConsumedError):
        _write_codex_turn(root, ts, "turn 2")

    # Retrying with a fresh captured_at (new turn) succeeds as a new file.
    fresh_path = store.write_raw(
        root,
        "proj",
        agent="codex",
        session_id="s1",
        cwd="/x",
        branch="",
        captured_at=ts + timedelta(minutes=5),
        body="turn 2, new pass",
    )
    assert fresh_path != path
    assert len(list((store.memory_dir("proj", root) / "raw").glob("*.md"))) == 2


# --- curated/ ------------------------------------------------------------


def test_upsert_curated_rejects_invalid_slug(root: Path) -> None:
    store.ensure_tree("proj", root)
    with pytest.raises(store.InvalidSlugError):
        store.upsert_curated(root, "proj", "Not Valid!", "body")


def test_upsert_curated_merges_provenance_order_preserving_dedupe(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1", provenance=["raw/a.md", "raw/b.md"])
    store.upsert_curated(root, "proj", "fact-a", "v2", provenance=["raw/b.md", "raw/c.md"])
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["provenance"] == ["raw/a.md", "raw/b.md", "raw/c.md"]
    assert doc.body == "v2"


def test_upsert_curated_supersedes_new_value_overrides(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1", supersedes=["old-1"])
    store.upsert_curated(root, "proj", "fact-a", "v2")  # no supersedes given -> keep prior
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["supersedes"] == ["old-1"]

    store.upsert_curated(root, "proj", "fact-a", "v3", supersedes=["old-2"])
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["supersedes"] == ["old-2"]


def test_upsert_curated_default_agent_last_is_curator(root: Path) -> None:
    """Unchanged behavior for every existing caller that doesn't pass
    ``agent_last`` (spec §12.3's additive-extension requirement)."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["agent_last"] == "curator"


def test_upsert_curated_agent_last_override(root: Path) -> None:
    """A seed-imported fact was never touched by the curator — the importer
    passes an override so ``agent_last`` never silently reads ``curator``
    (spec §12.3)."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1", agent_last="seed")
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["agent_last"] == "seed"


def test_upsert_curated_extra_frontmatter_persists(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(
        root,
        "proj",
        "fact-a",
        "v1",
        extra_frontmatter={"source_digest": "abc123", "source_path": "seed:notes/a.md"},
    )
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["source_digest"] == "abc123"
    assert doc["source_path"] == "seed:notes/a.md"


def test_upsert_curated_extra_frontmatter_cannot_clobber_core_keys(root: Path) -> None:
    """Core fields always win on collision — a caller can't use
    ``extra_frontmatter`` to smuggle a fake ``status``/``agent_last``/etc."""
    store.ensure_tree("proj", root)
    store.upsert_curated(
        root,
        "proj",
        "fact-a",
        "v1",
        agent_last="seed",
        extra_frontmatter={"status": "tombstoned", "agent_last": "curator", "name": "not-the-slug"},
    )
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["status"] == "active"
    assert doc["agent_last"] == "seed"
    assert doc["name"] == "fact-a"


def test_soft_delete_curated_rejects_invalid_slug(root: Path) -> None:
    store.ensure_tree("proj", root)
    with pytest.raises(store.InvalidSlugError):
        store.soft_delete_curated(root, "proj", "Not Valid!")


def test_soft_delete_moves_to_tombstones(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "body")
    dest = store.soft_delete_curated(root, "proj", "fact-a")
    assert dest == store.memory_dir("proj", root) / ".tombstones" / "fact-a.md"
    assert not (store.memory_dir("proj", root) / "curated" / "fact-a.md").exists()
    doc = store.read_doc(dest)
    assert doc["status"] == "tombstoned"
    assert "tombstoned_at" in doc.frontmatter


def test_prune_tombstones_respects_grace_period(root: Path) -> None:
    store.ensure_tree("proj", root)
    mem = store.memory_dir("proj", root)
    old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat().replace("+00:00", "Z")
    recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    store.write_doc(
        mem / ".tombstones" / "old-fact.md",
        {"name": "old-fact", "status": "tombstoned", "tombstoned_at": old_ts},
        "body",
    )
    store.write_doc(
        mem / ".tombstones" / "recent-fact.md",
        {"name": "recent-fact", "status": "tombstoned", "tombstoned_at": recent_ts},
        "body",
    )
    pruned = store.prune_tombstones(root, "proj", older_than_days=14)
    assert pruned == ["old-fact"]
    assert not (mem / ".tombstones" / "old-fact.md").exists()
    assert (mem / ".tombstones" / "recent-fact.md").exists()


# --- nodes/ + index.md ---------------------------------------------------


def test_write_node_overwrites_wholesale_never_appends(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.write_node(root, "proj", "proj-status", "first version")
    store.write_node(root, "proj", "proj-status", "second version")
    doc = store.read_doc(store.memory_dir("proj", root) / "nodes" / "proj-status.md")
    assert doc.body == "second version"


def test_rebuild_index_lists_nodes_and_active_fact_count(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.write_node(root, "proj", "proj-status", "# Proj Status\n\nsome body")
    store.upsert_curated(root, "proj", "fact-a", "a")
    store.upsert_curated(root, "proj", "fact-b", "b")
    store.soft_delete_curated(root, "proj", "fact-b")

    index_path = store.rebuild_index(root, "proj")
    content = index_path.read_text(encoding="utf-8")  # em-dash: cp1252 mangles it on Windows
    assert "# Memory index — proj" in content
    assert "[proj-status](nodes/proj-status.md) — Proj Status" in content
    assert "_1 active curated facts._" in content
