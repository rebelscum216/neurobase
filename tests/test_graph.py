"""Slice A of the provenance plan: the read-only session→fact graph.

Every test here pins one rung of the degradation ladder or one thing the reader
must refuse to invent. The load-bearing property under test throughout is that a
thinning record degrades to *coarse* — it never fabricates an edge and never
silently drops one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neurobase.core import graph, projects, store
from neurobase.core.store_handle import StoreHandle, StoreMode, open_store

PROJECT = "proj"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


@pytest.fixture
def handle(root: Path) -> StoreHandle:
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, Path("/tmp/proj"), PROJECT)
    return open_store(root, StoreMode.READ)


def write_raw(root: Path, *, session_id: str, agent: str = "claude", when: str) -> str:
    """A raw capture whose filename is the real ``store.raw_filename`` grammar."""
    captured = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(UTC)
    name = store.raw_filename(captured, agent, session_id)
    path = store.memory_dir(PROJECT, root) / "raw" / name
    store.write_doc(
        path,
        {"session_id": session_id, "agent": agent, "captured_at": when, "consumed": True},
        "transcript body",
    )
    return name


def write_fact(root: Path, slug: str, **frontmatter: object) -> None:
    store.upsert_curated(root, PROJECT, slug, f"body of {slug}", **frontmatter)  # type: ignore[arg-type]


def write_fold(root: Path, fold: dict[str, object], *, status: str = "ok") -> None:
    line = json.dumps({"status": status, "upserts": 1, "at": "2026-07-30T00:00:00Z", "fold": fold})
    path = store.memory_dir(PROJECT, root) / store.CURATOR_LOG
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def session_edge_pairs(g: graph.MemoryGraph) -> set[tuple[str, str]]:
    return {(e.session, e.fact) for e in g.session_edges}


# --- rung 1: the resolved join ---------------------------------------------


def test_provenance_plus_raw_frontmatter_is_a_resolved_edge(
    root: Path, handle: StoreHandle
) -> None:
    name = write_raw(root, session_id="sess-alpha", when="2026-07-01T10:00:00Z")
    write_fact(root, "a-fact", provenance=[f"raw/{name}"])

    g = graph.memory_graph(handle, PROJECT)

    assert session_edge_pairs(g) == {("sess-alpha", "a-fact")}
    edge = g.session_edges[0]
    assert edge.resolved and edge.source == "frontmatter"
    node = g.sessions[0]
    assert (node.session_id, node.agent, node.resolved) == ("sess-alpha", "claude", True)
    assert node.files == (name,)


def test_several_captures_collapse_into_one_session_node(root: Path, handle: StoreHandle) -> None:
    """A conversation is one node, not one node per capture — the whole question
    the graph answers is 'which conversation fed this'."""
    first = write_raw(root, session_id="sess-alpha", when="2026-07-01T10:00:00Z")
    second = write_raw(root, session_id="sess-alpha", when="2026-07-01T11:00:00Z")
    write_fact(root, "a-fact", provenance=[f"raw/{first}", f"raw/{second}"])

    g = graph.memory_graph(handle, PROJECT)

    assert len(g.sessions) == 1
    assert g.sessions[0].files == tuple(sorted((first, second)))
    assert g.sessions[0].captured_at == "2026-07-01T10:00:00Z"  # earliest capture
    assert session_edge_pairs(g) == {("sess-alpha", "a-fact")}  # one edge, not two


def test_sessions_with_no_fact_edges_still_appear(root: Path, handle: StoreHandle) -> None:
    write_raw(root, session_id="sess-orphan", when="2026-07-01T10:00:00Z")
    g = graph.memory_graph(handle, PROJECT)
    assert [s.id for s in g.sessions] == ["sess-orphan"]
    assert g.session_edges == ()


# --- rung 3: degradation, never fabrication --------------------------------


def test_missing_raw_degrades_to_a_skeleton_and_keeps_the_edge(
    root: Path, handle: StoreHandle
) -> None:
    """The raw is gone (pruned, or a fresh clone with a gitignored raw/). The
    edge must survive, marked unresolved, with identity recovered only as far as
    the filename honestly carries it."""
    captured = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    name = store.raw_filename(captured, "codex", "sess-gone")
    write_fact(root, "a-fact", provenance=[f"raw/{name}"])

    g = graph.memory_graph(handle, PROJECT)

    assert session_edge_pairs(g) == {(name, "a-fact")}
    assert not g.session_edges[0].resolved
    node = g.sessions[0]
    assert node.resolved is False
    assert node.agent == "codex"
    assert node.captured_at is not None and node.captured_at.startswith("2026-07-01T10:00:00")
    # sid8 is a truncated, stripped digest of the session id — not the id. The
    # reader must not widen it back into one.
    assert node.session_id is None


def test_unparseable_raw_filename_still_keeps_the_edge(root: Path, handle: StoreHandle) -> None:
    write_fact(root, "a-fact", provenance=["raw/not-the-grammar.md"])
    g = graph.memory_graph(handle, PROJECT)
    assert session_edge_pairs(g) == {("not-the-grammar.md", "a-fact")}
    node = g.sessions[0]
    assert (node.resolved, node.agent, node.captured_at) == (False, None, None)


@pytest.mark.parametrize("sentinel", ["user-directed", "seed:claude-export/notes.md"])
def test_sentinel_provenance_produces_no_session_edge(
    root: Path, handle: StoreHandle, sentinel: str
) -> None:
    write_fact(root, "a-fact", provenance=[sentinel])
    g = graph.memory_graph(handle, PROJECT)
    assert g.session_edges == ()
    assert g.sessions == ()


def test_user_directed_marks_the_fact_pinned(root: Path, handle: StoreHandle) -> None:
    write_fact(root, "pinned-fact", provenance=["user-directed"])
    write_fact(root, "plain-fact", provenance=[])
    g = graph.memory_graph(handle, PROJECT)
    assert {f.slug: f.pinned for f in g.facts} == {"pinned-fact": True, "plain-fact": False}


# --- fact→fact: supersedes and ghosts ---------------------------------------


def test_supersedes_frontmatter_becomes_a_fact_edge(root: Path, handle: StoreHandle) -> None:
    write_fact(root, "old-fact", provenance=[])
    store.soft_delete_curated(root, PROJECT, "old-fact")
    write_fact(root, "new-fact", provenance=[], supersedes=["old-fact"])

    g = graph.memory_graph(handle, PROJECT, include_tombstoned=True)

    assert [(e.supersedes, e.superseded_by, e.resolved) for e in g.fact_edges] == [
        ("old-fact", "new-fact", True)
    ]


def test_supersedes_target_pruned_renders_a_ghost_node(root: Path, handle: StoreHandle) -> None:
    """``supersedes`` is overwritten on re-upsert and tombstones are pruned at 14
    days, so a target that exists nowhere is normal, not corrupt. It must render
    as an unresolved ghost rather than vanishing with its edge."""
    write_fact(root, "new-fact", provenance=[], supersedes=["long-gone"])

    g = graph.memory_graph(handle, PROJECT)

    ghost = next(f for f in g.facts if f.slug == "long-gone")
    assert (ghost.status, ghost.resolved, ghost.tombstoned) == ("ghost", False, False)
    assert [(e.supersedes, e.resolved) for e in g.fact_edges] == [("long-gone", False)]


def test_tombstoned_facts_are_excluded_by_default_and_are_not_ghosts(
    root: Path, handle: StoreHandle
) -> None:
    """The toggle must not silently promote a real tombstone into a ghost — a
    known-but-excluded slug yields no node and no edge."""
    write_fact(root, "old-fact", provenance=[])
    store.soft_delete_curated(root, PROJECT, "old-fact")
    write_fact(root, "new-fact", provenance=[], supersedes=["old-fact"])

    g = graph.memory_graph(handle, PROJECT)

    assert [f.slug for f in g.facts] == ["new-fact"]
    assert g.fact_edges == ()

    with_tombs = graph.memory_graph(handle, PROJECT, include_tombstoned=True)
    tombstoned = next(f for f in with_tombs.facts if f.slug == "old-fact")
    assert (tombstoned.status, tombstoned.tombstoned, tombstoned.resolved) == (
        "tombstoned",
        True,
        True,
    )


def test_active_slug_wins_over_its_tombstone_residue(root: Path, handle: StoreHandle) -> None:
    """Spec §1: an active+tombstoned pair resolves to *active*, and the tombstone
    is residue ``upsert_curated`` deliberately cannot delete. A reader of
    ``.tombstones/`` MUST exclude it — otherwise one fact renders twice."""
    write_fact(root, "a-fact", provenance=[])
    store.soft_delete_curated(root, PROJECT, "a-fact")
    write_fact(root, "a-fact", provenance=[])  # resurrected; tombstone stays on disk

    assert (store.memory_dir(PROJECT, root) / ".tombstones" / "a-fact.md").exists()
    assert store.list_tombstoned(root, PROJECT) == []

    g = graph.memory_graph(handle, PROJECT, include_tombstoned=True)
    assert [(f.slug, f.status) for f in g.facts] == [("a-fact", "active")]


# --- the fold journal --------------------------------------------------------


def test_journal_edges_survive_the_raw_file_being_gone(root: Path, handle: StoreHandle) -> None:
    """The journal's whole point: identity captured at consumption time outlives
    raw retention, so the edge stays *resolved* with no raw file on disk."""
    write_fact(root, "a-fact", provenance=[])
    write_fold(
        root,
        {
            "v": 1,
            "consumed": [
                {
                    "file": "2026-07-01T10-00-00.000000Z_claude_sessalph.md",
                    "session_id": "sess-alpha",
                    "agent": "claude",
                    "captured_at": "2026-07-01T10:00:00Z",
                }
            ],
            "edges": {"a-fact": ["2026-07-01T10-00-00.000000Z_claude_sessalph.md"]},
        },
    )

    g = graph.memory_graph(handle, PROJECT)

    assert session_edge_pairs(g) == {("sess-alpha", "a-fact")}
    edge = g.session_edges[0]
    assert edge.resolved and edge.source == "journal"
    assert g.sessions[0].session_id == "sess-alpha"


def test_journal_attribution_wins_over_frontmatter_for_the_same_edge(
    root: Path, handle: StoreHandle
) -> None:
    name = write_raw(root, session_id="sess-alpha", when="2026-07-01T10:00:00Z")
    write_fact(root, "a-fact", provenance=[f"raw/{name}"])
    write_fold(
        root,
        {
            "v": 1,
            "consumed": [{"file": name, "session_id": "sess-alpha", "agent": "claude"}],
            "edges": {"a-fact": [name]},
        },
    )

    g = graph.memory_graph(handle, PROJECT)

    assert len(g.session_edges) == 1  # not duplicated
    assert g.session_edges[0].source == "journal"


def test_journal_superseded_pairs_outlive_pruning(root: Path, handle: StoreHandle) -> None:
    """Multi-generation supersession overwrites the ``supersedes`` key, so the
    journal is the only surviving record of the older hop."""
    write_fact(root, "new-fact", provenance=[])
    write_fold(root, {"v": 1, "superseded": [{"slug": "gen-one", "by": "new-fact"}]})

    g = graph.memory_graph(handle, PROJECT)

    assert [(e.supersedes, e.superseded_by, e.source) for e in g.fact_edges] == [
        ("gen-one", "new-fact", "journal")
    ]
    assert next(f for f in g.facts if f.slug == "gen-one").status == "ghost"


def test_no_run_granular_edges(root: Path, handle: StoreHandle) -> None:
    """A pass consumed three sessions and touched a fact it could not attribute.
    Joining ``consumed`` × the touched fact would sprout three false edges; the
    unattributed fact must render as an honest orphan instead."""
    consumed = [
        {"file": f"2026-07-0{i}T10-00-00.000000Z_claude_sess000{i}.md", "session_id": f"sess-{i}"}
        for i in (1, 2, 3)
    ]
    write_fact(root, "unattributed", provenance=[])
    write_fold(root, {"v": 1, "consumed": consumed, "edges": {"unattributed": []}})

    g = graph.memory_graph(handle, PROJECT)

    assert len(g.sessions) == 3  # the sessions are real and are shown
    assert g.session_edges == ()  # but nothing attributes them to the fact


def test_journal_edges_to_an_excluded_or_unknown_slug(root: Path, handle: StoreHandle) -> None:
    """A journal edge naming a fact that no longer exists must not resurrect it
    as a session-bearing node; only facts in the included set take edges."""
    write_fold(
        root,
        {
            "v": 1,
            "consumed": [{"file": "x.md", "session_id": "sess-alpha"}],
            "edges": {"never-existed": ["x.md"]},
        },
    )

    g = graph.memory_graph(handle, PROJECT)

    assert g.session_edges == ()
    assert g.facts == ()
    assert [s.id for s in g.sessions] == ["sess-alpha"]  # the session itself is real


def test_journal_superseded_by_a_fact_that_no_longer_exists_is_dropped(
    root: Path, handle: StoreHandle
) -> None:
    """The ``by`` side of a supersession is the *surviving* fact. If it is itself
    gone, the pair describes history between two absent facts — rendering it would
    resurrect both as ghosts and imply a hop into nothing."""
    write_fold(root, {"v": 1, "superseded": [{"slug": "gen-one", "by": "also-gone"}]})

    g = graph.memory_graph(handle, PROJECT)

    assert g.fact_edges == ()
    assert g.facts == ()


def test_a_supersession_recorded_twice_yields_one_edge_journal_sourced(
    root: Path, handle: StoreHandle
) -> None:
    write_fact(root, "old-fact", provenance=[])
    store.soft_delete_curated(root, PROJECT, "old-fact")
    write_fact(root, "new-fact", provenance=[], supersedes=["old-fact"])
    write_fold(root, {"v": 1, "superseded": [{"slug": "old-fact", "by": "new-fact"}]})

    g = graph.memory_graph(handle, PROJECT, include_tombstoned=True)

    assert len(g.fact_edges) == 1
    assert g.fact_edges[0].source == "journal"


@pytest.mark.parametrize(
    "name",
    [
        "2026-07-01T10-00-00.000000Z_claude_sessalph",  # no .md
        "nounderscores.md",
        "2026-07-01T10-00-00.000000Z_claude.md",  # no sid8 field
        "not-a-timestamp_claude_sessalph.md",
        "_claude_sessalph.md",  # empty timestamp
    ],
)
def test_filename_grammar_refuses_to_guess(name: str) -> None:
    assert graph.parse_raw_filename(name) == (None, None)


def test_filename_grammar_round_trips_a_real_raw_filename() -> None:
    """The recovered stamp keeps its ``Z``, so a skeleton session's
    ``captured_at`` is directly comparable to one read from raw frontmatter —
    the two rungs of the ladder must not speak different timestamp dialects."""
    captured = datetime(2026, 7, 1, 10, 30, 45, 123456, tzinfo=UTC)
    name = store.raw_filename(captured, "codex", "sess-alpha")
    captured_at, agent = graph.parse_raw_filename(name)
    assert agent == "codex"
    assert captured_at == "2026-07-01T10:30:45.123456Z"
    assert store.parse_captured_at(captured_at) == captured


def test_contention_suffix_does_not_defeat_the_grammar() -> None:
    captured = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    base = store.raw_filename(captured, "claude", "sess-alpha")
    contended = base.replace(".md", "__g0002.md")
    assert graph.parse_raw_filename(contended) == graph.parse_raw_filename(base)


# --- fail-soft ---------------------------------------------------------------


def test_malformed_journal_lines_are_skipped_not_fatal(root: Path, handle: StoreHandle) -> None:
    """Spec §2: the journal may have gaps and is written outside any transaction.
    A torn last line must not cost the reader every good line before it."""
    write_fact(root, "a-fact", provenance=[])
    write_fold(root, {"v": 1, "superseded": [{"slug": "gen-one", "by": "a-fact"}]})
    path = store.memory_dir(PROJECT, root) / store.CURATOR_LOG
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write('{"status": "ok", "fold": "not-an-object"}\n')
        fh.write('{"status": "ok", "fold": {"v": 1, "truncat')  # torn write, no newline

    g = graph.memory_graph(handle, PROJECT)

    assert [e.supersedes for e in g.fact_edges] == ["gen-one"]


def test_malformed_store_files_are_skipped_not_fatal(root: Path, handle: StoreHandle) -> None:
    name = write_raw(root, session_id="sess-alpha", when="2026-07-01T10:00:00Z")
    write_fact(root, "a-fact", provenance=[f"raw/{name}"])
    (store.memory_dir(PROJECT, root) / "curated" / "broken.md").write_text("no frontmatter here")
    (store.memory_dir(PROJECT, root) / "raw" / "broken.md").write_text("also broken")

    g = graph.memory_graph(handle, PROJECT)

    assert [f.slug for f in g.facts] == ["a-fact"]
    assert session_edge_pairs(g) == {("sess-alpha", "a-fact")}


def test_missing_project_tree_yields_an_empty_graph(root: Path, handle: StoreHandle) -> None:
    g = graph.memory_graph(handle, "never-created")
    assert g == graph.MemoryGraph((), (), (), ())


def test_an_invalid_registry_slug_is_skipped_not_fatal(root: Path, handle: StoreHandle) -> None:
    """All-projects mode walks the registry, which is fail-soft by contract: a
    corrupt entry contributes nothing rather than taking down every other
    project's graph (the ``memory_list_projects`` posture)."""
    write_fact(root, "a-fact", provenance=[])
    registry = root / "registry.toml"
    registry.write_text(registry.read_text() + '\n[projects."Not A Slug!"]\nroots = ["/tmp/x"]\n')

    g = graph.memory_graph(handle)

    assert [f.slug for f in g.facts] == ["a-fact"]


def test_an_explicitly_named_invalid_project_raises(handle: StoreHandle) -> None:
    """Fail-soft covers a hostile store, not a caller's typo: an empty graph and
    a bad slug must stay distinguishable."""
    with pytest.raises(store.InvalidSlugError):
        graph.memory_graph(handle, "Not A Slug!")


# --- all-projects mode + determinism ----------------------------------------


def test_all_projects_mode_keeps_same_slug_facts_distinct(root: Path, handle: StoreHandle) -> None:
    """Two projects may hold the same fact slug; a project-blind key would
    collapse them into one node and cross-wire their edges."""
    other = "otherproj"
    store.ensure_tree(other, root)
    projects.register_project(root, Path("/tmp/other"), other)
    write_fact(root, "shared-slug", provenance=[])
    store.upsert_curated(root, other, "shared-slug", "other body", provenance=[])

    g = graph.memory_graph(handle)

    assert [(f.project, f.slug) for f in g.facts] == [
        (other, "shared-slug"),  # ordering is (project, slug); "otherproj" < "proj"
        (PROJECT, "shared-slug"),
    ]


def test_graph_is_deterministic_across_calls(root: Path, handle: StoreHandle) -> None:
    for i in (3, 1, 2):
        name = write_raw(root, session_id=f"sess-{i}", when=f"2026-07-0{i}T10:00:00Z")
        write_fact(root, f"fact-{i}", provenance=[f"raw/{name}"])

    first = graph.memory_graph(handle, PROJECT)
    assert first == graph.memory_graph(handle, PROJECT)
    assert [s.id for s in first.sessions] == ["sess-1", "sess-2", "sess-3"]  # by captured_at
    assert [f.slug for f in first.facts] == ["fact-1", "fact-2", "fact-3"]


def test_the_graph_writes_nothing(root: Path, handle: StoreHandle) -> None:
    name = write_raw(root, session_id="sess-alpha", when="2026-07-01T10:00:00Z")
    write_fact(root, "a-fact", provenance=[f"raw/{name}"])
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}

    graph.memory_graph(handle, PROJECT, include_tombstoned=True)

    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == before
