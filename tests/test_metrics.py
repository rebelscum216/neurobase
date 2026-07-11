"""Tests for recommender metrics (spec §12.9, ADR-0007 D19, workstream H) —
``recommender/metrics.py``.

Covers the named workstream-H tests:

1. metrics on empty ledger
2. accepted/rejected/edited counts
3. missing artifact marks survival false only after the configured window
4. a malformed line in recommender/ledger.jsonl is skipped, not fatal, by
   metrics computation
5. a proposal edited three times before acceptance contributes exactly 1 to
   decided and 4 to reviewed_events (D19's disclosed test-coverage gap)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from neurobase.core import store as core_store
from neurobase.core.config import RecommendConfig
from neurobase.recommender import metrics, proposals
from neurobase.recommender.corpus import EvidenceRef, ledger_path
from neurobase.recommender.ranker import RankedCandidate, Scores

NOW = datetime(2026, 7, 10, tzinfo=UTC)


# --- helpers -----------------------------------------------------------------


def _ranked(slug: str, **overrides: Any) -> RankedCandidate:
    base: dict[str, Any] = {
        "slug": slug,
        "type": "rule",
        "candidate_type": "repeated-instruction",
        "title": "Prefer uv run",
        "rationale": "corrected repeatedly across sessions",
        "draft": "Always invoke Python via `uv run`.",
        "target": "AGENTS.md",
        "project": "neurobase",
        "supersedes": [],
        "evidence": [EvidenceRef.raw("neurobase", "2026-07-01T00-00-00Z_claude_ab12cd34.md")],
        "scores": Scores(recurrence=5, breadth=6, recency=0.86, total=25.8),
        "sessions": 3,
        "agents": 2,
        "projects": 1,
    }
    base.update(overrides)
    return RankedCandidate(**base)


def _accept(
    root: Path,
    slug: str,
    *,
    installed_path: Path,
    content: str = "installed content",
    now: datetime = NOW,
) -> None:
    installed_path.write_text(content, encoding="utf-8")
    installed_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    proposals.accept_proposal(
        root,
        slug,
        target="AGENTS.md",
        installed_path=installed_path,
        now=now,
        installed_hash=installed_hash,
    )


# --- named test 1: metrics on empty ledger -----------------------------------


def test_metrics_on_empty_ledger(tmp_path: Path) -> None:
    """No proposals/ledger at all: every metric reports "insufficient data" (or
    a zero count), never a crash or divide-by-zero."""
    root = tmp_path / "store"
    result = metrics.compute_metrics(root, now=NOW)

    assert result.decided == 0
    assert result.accepted == 0
    assert result.rejected == 0
    assert result.precision is None
    assert result.edited_rate is None
    assert result.reviewed_events == 0
    assert result.survival == {}
    assert result.recurrence_reduction is None


def test_proposal_status_without_matching_ledger_event_is_insufficient_data(
    tmp_path: Path,
) -> None:
    """Codex round-1 finding: a proposal FILE claiming accepted/rejected status
    with no MATCHING ledger event (e.g. its status was hand-set outside
    accept_proposal/reject_proposal, bypassing the ledger write — the ledger
    here still has each proposal's `proposed` line from `write_ranked`, just
    never an `accepted`/`rejected` one) must not manufacture a decided count —
    the ledger, not the file's own `status` field, is the authoritative record
    of a decision. Every metric reports "insufficient data", exactly as if the
    store were fully empty."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("orphan-accepted"), _ranked("orphan-rejected")], now=NOW)
    for slug, status in [("orphan-accepted", "accepted"), ("orphan-rejected", "rejected")]:
        doc = proposals.load_proposal(root, slug)
        assert doc is not None
        frontmatter = dict(doc.frontmatter)
        frontmatter["status"] = status
        core_store.write_doc(doc.file_path, frontmatter, doc.body)
        # No accept_proposal/reject_proposal was ever called for this slug —
        # its ledger history holds only the `proposed` line write_ranked wrote.
        history = proposals.ledger_history(root, slug)
        assert [event["event"] for event in history] == ["proposed"]

    result = metrics.compute_metrics(root, now=NOW)

    assert result.decided == 0
    assert result.accepted == 0
    assert result.rejected == 0
    assert result.precision is None
    assert result.edited_rate is None
    assert result.reviewed_events == 0
    assert result.survival == {}
    assert result.recurrence_reduction is None


# --- named test 2: accepted/rejected/edited counts ---------------------------


def test_accepted_rejected_edited_counts(tmp_path: Path) -> None:
    """Three decided proposals (accepted, rejected, edited-then-accepted) plus
    one still-``proposed`` proposal that must NOT contribute to `decided`."""
    root = tmp_path / "store"
    proposals.write_ranked(
        root,
        [_ranked("plain-accept"), _ranked("plain-reject"), _ranked("edited-accept")],
        now=NOW,
    )
    proposals.write_ranked(root, [_ranked("still-proposed")], now=NOW)

    _accept(root, "plain-accept", installed_path=tmp_path / "a.md")
    proposals.reject_proposal(root, "plain-reject", now=NOW)
    proposals.save_edited_draft(root, "edited-accept", "A revised draft.", now=NOW)
    _accept(root, "edited-accept", installed_path=tmp_path / "b.md")

    result = metrics.compute_metrics(root, now=NOW)

    assert result.decided == 3
    assert result.accepted == 2
    assert result.rejected == 1
    assert result.precision == 2 / 3
    # Exactly one of the two decided-accepted proposals carries an `edited`
    # event before its decision (D19's proposal-counted denominator).
    assert result.edited_rate == 1 / 3
    # reviewed_events is the raw ledger-line count: 2 accepted + 1 rejected +
    # 1 edited = 4 (never used as precision/edited_rate's denominator).
    assert result.reviewed_events == 4


# --- named test 3: survival false only after the configured window ----------


def test_missing_artifact_survival_false_only_after_window(tmp_path: Path) -> None:
    """Before ``survival_window_days`` have elapsed since acceptance, a missing
    artifact reports "insufficient data", never `False`/"not_survived"; only
    past the window does it flip."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    installed_path.unlink()  # simulate the artifact having gone missing

    cfg = RecommendConfig(survival_window_days=30)

    just_inside_window = metrics.compute_metrics(root, config=cfg, now=NOW + timedelta(days=29))
    assert just_inside_window.survival["prefer-uv-run"] == "insufficient_data"

    just_past_window = metrics.compute_metrics(root, config=cfg, now=NOW + timedelta(days=31))
    assert just_past_window.survival["prefer-uv-run"] == "not_survived"


def test_missing_artifact_survival_at_exact_window_boundary_is_checkable(tmp_path: Path) -> None:
    """At exactly ``elapsed_days == survival_window_days``, the window has
    already elapsed (``_survival_one``'s ``<`` is strict): a missing artifact
    is checkable and reports "not_survived", not "insufficient_data"."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    installed_path.unlink()  # simulate the artifact having gone missing

    cfg = RecommendConfig(survival_window_days=30)

    exactly_at_window = metrics.compute_metrics(root, config=cfg, now=NOW + timedelta(days=30))
    assert exactly_at_window.survival["prefer-uv-run"] == "not_survived"


def test_survived_artifact_matches_installed_hash(tmp_path: Path) -> None:
    """Past the window, an untouched artifact (bytes match the recorded
    ``installed_hash``) reports "survived", not merely "present"."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=31))
    assert result.survival["prefer-uv-run"] == "survived"


def test_modified_artifact_survival_false_past_window(tmp_path: Path) -> None:
    """Past the window, a modified artifact (bytes no longer match the
    recorded ``installed_hash``) reports "not_survived"."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    installed_path.write_text("someone hand-edited this artifact", encoding="utf-8")

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=31))
    assert result.survival["prefer-uv-run"] == "not_survived"


# --- named test 4: malformed ledger line skipped, not fatal ------------------


def test_malformed_ledger_line_skipped_not_fatal(tmp_path: Path) -> None:
    """A malformed line in ``recommender/ledger.jsonl`` (bad JSON, a JSON
    scalar rather than an object) is skipped, never fatal — reuses the
    existing fail-soft ``proposals.read_ledger`` reader."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("plain-accept")], now=NOW)
    _accept(root, "plain-accept", installed_path=tmp_path / "a.md")

    path = ledger_path(root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write("42\n")  # valid JSON, but not an object

    result = metrics.compute_metrics(root, now=NOW)

    assert result.decided == 1
    assert result.accepted == 1
    # Only the genuine `proposed`/`accepted` lines count; the two malformed
    # lines never raise and never inflate reviewed_events.
    assert result.reviewed_events == 1


# --- named test 5 (D19 disclosed gap): edited-3x-then-accepted ---------------


def test_edited_three_times_then_accepted_counts_once_in_decided(tmp_path: Path) -> None:
    """ADR-0007 D19's disclosed test-coverage gap: a proposal edited three
    times before acceptance contributes exactly 1 to `decided` and 4 to
    `reviewed_events` (3 `edited` + 1 `accepted`) — never diluting precision
    by the number of intermediate edits."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)

    for i in range(3):
        proposals.save_edited_draft(
            root, "prefer-uv-run", f"Revision {i}.", now=NOW + timedelta(hours=i)
        )
    _accept(
        root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW + timedelta(hours=4)
    )

    result = metrics.compute_metrics(root, now=NOW + timedelta(hours=4))

    assert result.decided == 1
    assert result.accepted == 1
    assert result.precision == 1.0
    assert result.edited_rate == 1.0
    assert result.reviewed_events == 4
