"""Tests for the ranker (spec §12.6, execution plan workstream E) —
``recommender/ranker.py``.

Covers the two named workstream-E tests that live on the ranker side:

- **threshold enforcement** — a candidate failing either half of the gate
  (``len(evidence) >= min_occurrences`` and ``sessions >= min_breadth_sessions``)
  is silently dropped, not an error;
- **ranker recomputes occurrences/breadth/sessions from evidence, ignoring a
  miner's inflated self-reported counts** — the ADR-0007 determinism guarantee.

Plus the recency-weight formula, curated→raw provenance chasing, and the
fail-soft handling of an unresolved evidence ref (D21)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from neurobase.core import store
from neurobase.core.config import RecommendConfig
from neurobase.recommender import corpus, ranker

NOW = datetime(2026, 7, 10, tzinfo=UTC)


# --- helpers -----------------------------------------------------------------


def _write_registry(root: Path, slugs: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for slug in slugs:
        lines.append(f"[projects.{slug}]")
        lines.append(f'roots = ["/repos/{slug}"]')
    (root / "registry.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_raw(
    root: Path,
    project: str,
    *,
    agent: str,
    session_id: str,
    captured_at: datetime,
    body: str = "raw body",
) -> str:
    """Write one raw capture; return its basename (for evidence refs)."""
    store.ensure_tree(project, root)
    path = store.write_raw(
        root,
        project,
        agent=agent,
        session_id=session_id,
        cwd="/repo",
        branch="main",
        captured_at=captured_at,
        body=body,
    )
    return path.name


def _seed_curated_over_raw(
    root: Path, project: str, slug: str, raw_file: str, body: str = "durable fact"
) -> None:
    store.ensure_tree(project, root)
    store.upsert_curated(root, project, slug, body, provenance=[f"raw/{raw_file}"])


def _candidate(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slug": "prefer-uv-run",
        "type": "rule",
        "candidate_type": "repeated-instruction",
        "title": "Prefer uv run",
        "rationale": "corrected repeatedly",
        "draft": "Always invoke Python via `uv run`.",
        "target": "AGENTS.md",
        "evidence": [],
        "occurrences": 999,
        "projects": ["a", "b", "c", "d"],
        "agents": ["x", "y", "z"],
        "supersedes": [],
    }
    base.update(overrides)
    return base


# --- named test: ranker recomputes from evidence, ignoring inflated counts ----


def test_ranker_recomputes_counts_from_evidence_not_self_report(tmp_path: Path) -> None:
    """Workstream E: 'ranker recomputes occurrences/breadth/sessions from
    evidence, ignoring a miner's inflated self-reported counts'. Two raw refs
    (two sessions, two agents) plus a curated ref that chases one hop to a third
    raw session — the recomputed numbers, never the miner's 999/4-projects/
    3-agents fantasy, drive the score."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    r2 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0002", captured_at=NOW)
    r3 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0003", captured_at=NOW)
    _seed_curated_over_raw(root, "neurobase", "use-uv-not-pip", r3)

    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
            {"kind": "curated", "project": "neurobase", "slug": "use-uv-not-pip"},
        ],
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)

    assert len(ranked) == 1
    rc = ranked[0]
    # Recurrence is len(evidence) = 3, NOT the self-reported 999.
    assert rc.scores.recurrence == 3
    # Three distinct sessions reachable (two direct raws + one via the curated
    # fact's provenance), two distinct agents, one project — NOT 4 projects / 3
    # agents as the miner claimed.
    assert rc.sessions == 3
    assert rc.agents == 2
    assert rc.projects == 1
    # breadth = sessions × max(agents,1) × max(projects,1) = 3 × 2 × 1 = 6.
    assert rc.scores.breadth == 6
    # All captures are "now", so recency is at its ceiling and total = 3×6×1.
    assert rc.scores.recency == 1.0
    assert rc.scores.total == 18.0
    assert rc.project == "neurobase"


# --- named test: threshold enforcement ---------------------------------------


def test_threshold_drops_below_min_occurrences(tmp_path: Path) -> None:
    """Two evidence refs < min_occurrences (3) ⇒ silently dropped."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    r2 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0002", captured_at=NOW)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    assert ranker.rank(root, [candidate], loaded, now=NOW) == []


def test_threshold_drops_below_min_breadth_sessions(tmp_path: Path) -> None:
    """Three evidence refs but all in ONE session ⇒ sessions (1) <
    min_breadth_sessions (2) ⇒ silently dropped, even though occurrences pass."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    # Three refs, one to the same single raw (a session), two curated facts whose
    # provenance chases back to that same single session — so recurrence is 3 but
    # breadth is only one session.
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    _seed_curated_over_raw(root, "neurobase", "fact-a", r1)
    _seed_curated_over_raw(root, "neurobase", "fact-b", r1)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "curated", "project": "neurobase", "slug": "fact-a"},
            {"kind": "curated", "project": "neurobase", "slug": "fact-b"},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)
    assert ranked == []


def test_threshold_passes_at_exact_minimums(tmp_path: Path) -> None:
    """Exactly min_occurrences refs across exactly min_breadth_sessions sessions
    clears the gate (the boundary is inclusive: ``>=``)."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    r2 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0002", captured_at=NOW)
    r3 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0002", captured_at=NOW)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
            {"kind": "raw", "project": "neurobase", "file": r3},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)
    assert len(ranked) == 1
    assert ranked[0].sessions == 2  # sess0001 + sess0002 (r2/r3 share sess0002)


# --- recency weight ----------------------------------------------------------


def test_recency_halves_at_one_halflife(tmp_path: Path) -> None:
    """A last occurrence exactly one half-life ago weights ~0.5 (§12.6)."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    cfg = RecommendConfig()  # recency_halflife_days = 30
    old = NOW - timedelta(days=cfg.recency_halflife_days)
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=old)
    r2 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0002", captured_at=old)
    r3 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0003", captured_at=old)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
            {"kind": "raw", "project": "neurobase", "file": r3},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, config=cfg, now=NOW)
    assert len(ranked) == 1
    assert ranked[0].scores.recency == 0.5


# --- fail-soft: an unresolved evidence ref under-counts, never crashes (D21) ---


def test_unresolved_raw_ref_under_counts_never_crashes(tmp_path: Path) -> None:
    """A raw ref to a hand-deleted file contributes no session/agent (D21) but
    still asserts its project, and the ranker never raises. Here two real
    sessions clear the gate; the phantom third ref just doesn't add a session."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    r2 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0002", captured_at=NOW)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
            {"kind": "raw", "project": "neurobase", "file": "2099-01-01T00-00-00Z_ghost_dead.md"},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)
    assert len(ranked) == 1
    rc = ranked[0]
    assert rc.scores.recurrence == 3  # every ref counts toward recurrence
    assert rc.sessions == 2  # the ghost ref resolves to nothing → no 3rd session
    assert rc.projects == 1


def test_unsafe_evidence_ref_is_dropped_before_scoring(tmp_path: Path) -> None:
    """R4 (§12.1): a canonical-shaped but traversal-valued evidence ref
    (``slug: "../bad"``) is dropped by the ranker, so it neither counts toward
    recurrence nor ever reaches ``write_ranked``'s persisted evidence list."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    r1 = _seed_raw(root, "neurobase", agent="claude", session_id="sess0001", captured_at=NOW)
    r2 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0002", captured_at=NOW)
    r3 = _seed_raw(root, "neurobase", agent="codex", session_id="sess0003", captured_at=NOW)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "neurobase", "file": r1},
            {"kind": "raw", "project": "neurobase", "file": r2},
            {"kind": "raw", "project": "neurobase", "file": r3},
            {"kind": "proposal", "slug": "../bad"},  # canonical shape, unsafe slug
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)
    assert len(ranked) == 1
    rc = ranked[0]
    assert len(rc.evidence) == 3  # the unsafe ref was dropped
    assert all(ref.is_safe() for ref in rc.evidence)
    assert rc.scores.recurrence == 3


def test_cross_project_evidence_yields_null_project(tmp_path: Path) -> None:
    """Evidence spanning two projects ⇒ ``project`` is None (a cross-project
    candidate), and both projects count toward breadth."""
    root = tmp_path / "store"
    _write_registry(root, ["alpha", "beta"])
    a1 = _seed_raw(root, "alpha", agent="claude", session_id="sess000a", captured_at=NOW)
    b1 = _seed_raw(root, "beta", agent="codex", session_id="sess000b", captured_at=NOW)
    b2 = _seed_raw(root, "beta", agent="codex", session_id="sess000c", captured_at=NOW)
    candidate = _candidate(
        evidence=[
            {"kind": "raw", "project": "alpha", "file": a1},
            {"kind": "raw", "project": "beta", "file": b1},
            {"kind": "raw", "project": "beta", "file": b2},
        ]
    )
    loaded = corpus.load_corpus(root, now=NOW)

    ranked = ranker.rank(root, [candidate], loaded, now=NOW)
    assert len(ranked) == 1
    rc = ranked[0]
    assert rc.project is None
    assert rc.projects == 2
    assert rc.sessions == 3
    assert rc.agents == 2  # claude (alpha) + codex (beta)
    assert rc.scores.breadth == 3 * 2 * 2  # sessions × agents × projects
