"""Tests for the corpus loader + evidence model (spec §12.4/§12.1, ADR-0007
D17/D18/D21, execution plan workstream C) — ``recommender/corpus.py``.

Covers the four named workstream-C tests (all-project registry traversal;
missing/bad project tree skips; raw cap enforced; evidence references serialize
into proposal frontmatter) plus the contract behaviors this slice implements:
fail-soft evidence resolution (D21), deterministic near-duplicate detection
(D18), and the fail-soft/empty ledger summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neurobase.core import store
from neurobase.core.config import RecommendConfig
from neurobase.recommender import corpus

# --- helpers -----------------------------------------------------------------


def _write_registry(root: Path, slugs: list[str]) -> None:
    """Write ``registry.toml`` naming each slug (the loader only reads the
    registry's *keys*; a project's data lives in its store tree)."""
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for slug in slugs:
        lines.append(f"[projects.{slug}]")
        lines.append(f'roots = ["/repos/{slug}"]')
    (root / "registry.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_curated(root: Path, project: str, slug: str, body: str = "A durable fact.") -> None:
    store.ensure_tree(project, root)
    store.upsert_curated(root, project, slug, body, provenance=[f"raw/{slug}.md"])


def _seed_raw(
    root: Path,
    project: str,
    *,
    agent: str = "claude",
    session_id: str = "sess0001",
    captured_at: datetime,
    body: str = "raw body",
) -> Path:
    store.ensure_tree(project, root)
    return store.write_raw(
        root,
        project,
        agent=agent,
        session_id=session_id,
        cwd="/repo",
        branch="main",
        captured_at=captured_at,
        body=body,
    )


# --- named test 1: all-project registry traversal ----------------------------


def test_all_project_registry_traversal(tmp_path: Path) -> None:
    """Workstream C: 'all-project registry traversal' — curated facts and raw
    captures from *every* registered project land in the corpus, not just one."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha", "beta"])
    _seed_curated(root, "alpha", "alpha-fact")
    _seed_curated(root, "beta", "beta-fact")
    now = datetime(2026, 7, 10, tzinfo=UTC)
    _seed_raw(root, "alpha", captured_at=now - timedelta(days=1), body="alpha raw")
    _seed_raw(root, "beta", captured_at=now - timedelta(days=1), body="beta raw")

    result = corpus.load_corpus(root, now=now)

    assert {f.project for f in result.curated} == {"alpha", "beta"}
    assert {f.slug for f in result.curated} == {"alpha-fact", "beta-fact"}
    assert {r.project for r in result.raw} == {"alpha", "beta"}
    assert result.skipped_projects == []
    # Curated facts carry the metadata the miner/ranker need to cite them.
    alpha = next(f for f in result.curated if f.project == "alpha")
    assert alpha.as_evidence() == corpus.EvidenceRef.curated("alpha", "alpha-fact")
    assert alpha.provenance == ["raw/alpha-fact.md"]


def test_empty_registry_yields_empty_corpus(tmp_path: Path) -> None:
    """No registry at all ⇒ an empty (never-raising) corpus."""
    root = tmp_path / "store"
    result = corpus.load_corpus(root)
    assert result.curated == []
    assert result.raw == []
    assert result.skipped_projects == []
    assert result.ledger.reject_counts == {}


def test_corpus_reads_self_guard_against_a_too_new_store(tmp_path: Path) -> None:
    """ADR-0015: the corpus readers now obtain a validated store handle before
    any store access, so a store whose schema is newer than this binary supports
    degrades to an empty corpus / unresolved evidence (fail-soft) instead of
    reading facts without the schema guard — defense in depth even when the
    library is called directly, not just behind the CLI guard."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha"])
    _seed_curated(root, "alpha", "alpha-fact")  # creates store.toml at schema 1
    # Ledger + a rejected proposal on disk: the fail-soft ledger reader would
    # happily surface these off an unsupported-schema store unless it, too, is
    # gated by the handle (Codex F1 — an unguarded corpus input, not just a path
    # construction detail).
    store.write_doc(
        corpus.proposal_path(root, "rejected-one"),
        {"name": "rejected-one", "status": "rejected", "candidate_type": "repeated-workflow"},
        "Never do the rejected thing.",
    )
    ledger = corpus.ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"at":"2026-07-09T12:00:00Z","slug":"rejected-one","event":"rejected",'
        '"candidate_type":"repeated-workflow"}\n',
        encoding="utf-8",
    )
    (root / "store.toml").write_text(
        f'schema = {store.STORE_SCHEMA_VERSION + 1}\ncreated_at = "2020-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )

    result = corpus.load_corpus(root)
    assert result.curated == []
    assert result.raw == []
    # The ledger/proposal readers are corpus inputs too: none of that state may
    # leak off a store whose schema this binary refuses (D11/spec §10).
    assert result.ledger.reject_counts == {}
    assert result.ledger.rejected_proposals == []

    ref = corpus.EvidenceRef.curated("alpha", "alpha-fact")
    assert not corpus.resolve_evidence(root, ref).resolved
    # The reader is guarded even when called directly, not only via load_corpus.
    summary = corpus.load_ledger_summary(root)
    assert summary.reject_counts == {}
    assert summary.rejected_proposals == []


# --- named test 2: missing/bad project tree skips ----------------------------


def test_missing_and_bad_project_tree_skips(tmp_path: Path) -> None:
    """Workstream C: 'missing/bad project tree skips' — a registered project
    with no store tree (missing) and one whose registry slug is invalid (bad,
    raises deep in the store) must neither crash the pass nor blind the loader
    to the healthy project's facts."""
    root = tmp_path / "store"
    # "good" has a real tree; "ghost" is registered but never had a tree
    # created; "BadSlug" is an invalid neurobase slug that makes the store's
    # slug-validating memory_dir raise.
    _write_registry(root, ["good", "ghost", "BadSlug"])
    _seed_curated(root, "good", "good-fact")

    result = corpus.load_corpus(root)

    assert [f.slug for f in result.curated] == ["good-fact"]
    # The invalid slug raises and is therefore skipped-and-named; the merely
    # missing tree yields nothing but isn't an error, so it is not a "skip".
    assert result.skipped_projects == ["BadSlug"]


# --- named test 3: raw cap enforced ------------------------------------------


def test_raw_cap_by_count_enforced(tmp_path: Path) -> None:
    """Workstream C: 'raw cap enforced' — the count cap keeps at most
    ``raw_cap_per_project`` of the *most recent* captures per project."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha"])
    now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    # 5 captures, all within the lookback window, one minute apart.
    for i in range(5):
        _seed_raw(
            root,
            "alpha",
            session_id=f"sess{i:04d}",
            captured_at=now - timedelta(minutes=i),
            body=f"capture {i}",
        )
    cfg = RecommendConfig(raw_cap_per_project=3, raw_lookback_days=30)

    result = corpus.load_corpus(root, config=cfg, now=now)

    assert len(result.raw) == 3
    # The three most recent (i=0,1,2) survive; the two oldest are dropped.
    bodies = {r.body for r in result.raw}
    assert bodies == {"capture 0", "capture 1", "capture 2"}


def test_raw_cap_by_age_enforced(tmp_path: Path) -> None:
    """The lookback window is the *other* half of the cap (§12.4, D17): a
    capture older than ``raw_lookback_days`` is excluded even when the count cap
    is nowhere near hit — 'whichever yields fewer'."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha"])
    now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    _seed_raw(
        root, "alpha", session_id="recent01", captured_at=now - timedelta(days=5), body="recent"
    )
    _seed_raw(
        root, "alpha", session_id="ancient1", captured_at=now - timedelta(days=90), body="ancient"
    )
    cfg = RecommendConfig(raw_cap_per_project=200, raw_lookback_days=30)

    result = corpus.load_corpus(root, config=cfg, now=now)

    assert [r.body for r in result.raw] == ["recent"]
    # And the survivor carries the per-file metadata the ranker recomputes
    # breadth from (§12.6).
    only = result.raw[0]
    assert only.agent == "claude"
    assert only.session_id == "recent01"
    assert only.as_evidence() == corpus.EvidenceRef.raw("alpha", only.file)


# --- named test 4: evidence references serialize into proposal frontmatter ---


def test_evidence_references_serialize_into_proposal_frontmatter(tmp_path: Path) -> None:
    """Workstream C: 'evidence references serialize into proposal frontmatter'
    — the structured §12.1 shapes round-trip through ``store.write_doc`` /
    ``read_doc`` byte-clean, matching the spec's example exactly."""
    root = tmp_path / "store"
    refs = [
        corpus.EvidenceRef.curated("neurobase", "use-uv-not-pip"),
        corpus.EvidenceRef.raw("neurobase", "2026-07-03T10-00-00Z_claude_ab12cd34.md"),
        corpus.EvidenceRef.proposal("prior-proposal"),
    ]
    frontmatter = {
        "name": "prefer-uv-run-over-pip",
        "status": "proposed",
        "evidence": corpus.evidence_to_frontmatter(refs),
    }
    path = corpus.proposal_path(root, "prefer-uv-run-over-pip")
    store.write_doc(path, frontmatter, "# Prefer uv run\n")

    doc = store.read_doc(path)
    assert doc["evidence"] == [
        {"kind": "curated", "project": "neurobase", "slug": "use-uv-not-pip"},
        {"kind": "raw", "project": "neurobase", "file": "2026-07-03T10-00-00Z_claude_ab12cd34.md"},
        {"kind": "proposal", "slug": "prior-proposal"},
    ]
    # A proposal ref carries no project; a curated ref carries no file — only
    # each kind's own keys are ever emitted.
    assert "project" not in doc["evidence"][2]
    assert "file" not in doc["evidence"][0]
    # Rebuilding the ref from stored frontmatter is the inverse of serializing.
    assert corpus.EvidenceRef.from_frontmatter(doc["evidence"][0]) == refs[0]
    # And it's genuinely block-style YAML in the file (§12.1's dumped shape),
    # not an inline `{...}` mapping.
    text = path.read_text(encoding="utf-8")
    assert "evidence:\n- kind: curated\n" in text


# --- fail-soft evidence resolution (ADR-0007 D21) ----------------------------


def test_resolve_evidence_hits_and_misses(tmp_path: Path) -> None:
    """A present curated fact / raw file resolves; a missing target comes back
    unresolved rather than raising (D21)."""
    root = tmp_path / "store"
    _seed_curated(root, "alpha", "live-fact")
    raw_path = _seed_raw(root, "alpha", captured_at=datetime(2026, 7, 10, tzinfo=UTC))

    live = corpus.resolve_evidence(root, corpus.EvidenceRef.curated("alpha", "live-fact"))
    assert live.resolved and live.path is not None and live.path.exists()

    raw_ref = corpus.EvidenceRef.raw("alpha", raw_path.name)
    assert corpus.resolve_evidence(root, raw_ref).resolved

    gone = corpus.resolve_evidence(root, corpus.EvidenceRef.raw("alpha", "deleted.md"))
    assert not gone.resolved and gone.path is None
    # A bad project slug is a miss, not a raised InvalidSlugError.
    bad = corpus.resolve_evidence(root, corpus.EvidenceRef.curated("BadSlug", "x"))
    assert not bad.resolved


def test_path_helpers_and_resolution_reject_traversal(tmp_path: Path) -> None:
    """Codex review F1: proposal/evidence identifiers must not escape their
    store directories. Invalid proposal slugs raise at the helper boundary
    (same discipline as ``store.memory_dir``); traversal-shaped evidence refs
    resolve to ``unresolved`` rather than returning an escaped path."""
    root = tmp_path / "store"

    # proposal_path validates the slug — a traversal slug can never build a path
    # that escapes proposals/.
    for bad in ["../escape", "a/b", "..", "Bad", "with space"]:
        with pytest.raises(store.InvalidSlugError):
            corpus.proposal_path(root, bad)
    assert corpus.proposal_path(root, "ok-slug") == root / "proposals" / "ok-slug.md"

    # An absolute raw `file` would otherwise discard the raw/ prefix and resolve
    # to the absolute path itself (e.g. /etc/passwd); it must be unresolved.
    escapes = [
        corpus.EvidenceRef.raw("alpha", "/etc/passwd"),
        corpus.EvidenceRef.raw("alpha", "../../../etc/passwd"),
        corpus.EvidenceRef.raw("alpha", "sub/nested.md"),
        corpus.EvidenceRef.raw("alpha", "..\\win.md"),
        corpus.EvidenceRef.curated("alpha", "../../secret"),
        corpus.EvidenceRef.proposal("../../secret"),
    ]
    for ref in escapes:
        resolved = corpus.resolve_evidence(root, ref)
        assert not resolved.resolved, ref
        assert resolved.path is None, ref

    # Sanity: a real absolute file that exists is still never reached through a
    # raw evidence ref, even when it's genuinely on disk.
    victim = tmp_path / "victim.md"
    victim.write_text("secret", encoding="utf-8")
    ref = corpus.EvidenceRef.raw("alpha", str(victim))
    assert not corpus.resolve_evidence(root, ref).resolved


def test_ledger_reader_skips_traversal_slug(tmp_path: Path) -> None:
    """A malformed/hostile ledger slug must not make the fail-soft ledger reader
    raise through ``proposal_path`` (Codex review F1 + §12.2 fail-soft)."""
    root = tmp_path / "store"
    ledger = corpus.ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"at":"2026-07-09T12:00:00Z","slug":"../../etc/passwd","event":"rejected",'
        '"candidate_type":"repeated-instruction"}\n',
        encoding="utf-8",
    )

    summary = corpus.load_ledger_summary(root)  # must not raise

    assert summary.rejected_proposals == []
    # The reject *count* still tallies by candidate_type (no path built from it).
    assert summary.reject_counts == {"repeated-instruction": 1}


def test_resolve_tombstoned_curated_fact_still_resolves(tmp_path: Path) -> None:
    """A tombstoned/pruned curated fact resolves to its ``.tombstones/`` record
    while that survives — evidence is an append-only historical record (D21)."""
    root = tmp_path / "store"
    _seed_curated(root, "alpha", "doomed-fact")
    store.soft_delete_curated(root, "alpha", "doomed-fact")

    resolved = corpus.resolve_evidence(root, corpus.EvidenceRef.curated("alpha", "doomed-fact"))
    assert resolved.resolved
    assert resolved.tombstoned
    assert resolved.path is not None and ".tombstones" in str(resolved.path)


# --- near-duplicate detection (ADR-0007 D18) ---------------------------------


def test_jaccard_and_near_duplicate() -> None:
    """Deterministic token-overlap similarity (D18): identical bodies score 1.0,
    disjoint bodies 0.0, and the threshold gates ``is_near_duplicate``."""
    a = "always use uv run never bare pip"
    b = "use uv run and never bare pip please"
    assert corpus.jaccard_similarity(a, a) == 1.0
    assert corpus.jaccard_similarity("alpha beta", "gamma delta") == 0.0
    assert corpus.jaccard_similarity("", "") == 0.0  # degenerate, never a match
    # a∩b = {use,uv,run,never,bare,pip}=6; a∪b = {always,use,uv,run,never,bare,
    # pip,and,please}=9 ⇒ 0.667, over the 0.6 threshold.
    assert corpus.is_near_duplicate(a, b, threshold=0.6)
    assert not corpus.is_near_duplicate(a, b, threshold=0.7)


# --- ledger summary (fail-soft, §12.2/§12.4) ---------------------------------


def test_ledger_summary_absent_is_empty(tmp_path: Path) -> None:
    """No ledger file (the norm until workstream F) ⇒ an empty summary."""
    root = tmp_path / "store"
    summary = corpus.load_ledger_summary(root)
    assert summary.reject_counts == {}
    assert summary.rejected_proposals == []


def test_ledger_summary_counts_rejects_and_skips_malformed(tmp_path: Path) -> None:
    """Rejected events feed per-type counts and surface the rejected proposal's
    body for near-dup suppression; a malformed line is skipped, not fatal
    (§12.2)."""
    root = tmp_path / "store"
    # A rejected proposal on disk, plus a ledger with one good reject line, one
    # corrupt line, and an unrelated event.
    store.write_doc(
        corpus.proposal_path(root, "rejected-one"),
        {"name": "rejected-one", "status": "rejected", "candidate_type": "repeated-instruction"},
        "Never do the rejected thing.",
    )
    ledger = corpus.ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"at":"2026-07-09T12:00:00Z","slug":"rejected-one","event":"rejected",'
        '"candidate_type":"repeated-instruction"}\n'
        "{ this is not valid json\n"
        '{"at":"2026-07-09T12:01:00Z","slug":"other","event":"proposed"}\n',
        encoding="utf-8",
    )

    summary = corpus.load_ledger_summary(root)

    assert summary.reject_counts == {"repeated-instruction": 1}
    assert [p.slug for p in summary.rejected_proposals] == ["rejected-one"]
    assert summary.rejected_proposals[0].body == "Never do the rejected thing."
    # The rejected body is what a fresh near-duplicate candidate is checked
    # against (§12.4 → §12.6).
    assert corpus.is_near_duplicate(
        "Never do the rejected thing.", summary.rejected_proposals[0].body, threshold=0.6
    )


def test_load_corpus_includes_ledger_summary(tmp_path: Path) -> None:
    """The top-level loader wires the ledger summary into the returned corpus."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha"])
    _seed_curated(root, "alpha", "a-fact")
    result = corpus.load_corpus(root)
    assert isinstance(result.ledger, corpus.LedgerSummary)


# --- evidence ref guards -----------------------------------------------------


def test_evidence_ref_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        corpus.EvidenceRef(kind="bogus").to_frontmatter()
    with pytest.raises(ValueError):
        corpus.EvidenceRef.from_frontmatter({"kind": "bogus"})
