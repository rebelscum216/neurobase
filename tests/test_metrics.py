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
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from neurobase.core import projects as core_projects
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


_DROP = object()  # sentinel: remove the key entirely rather than setting it


def _rewrite_ledger_event(root: Path, slug: str, event: str, **changes: Any) -> None:
    """Rewrite one ledger event's fields in place. Used to reproduce ledgers
    this codebase no longer writes but must still read fail-soft: a legacy
    event predating a field, or one whose timestamp is unparseable. Pass
    ``_DROP`` as a value to remove that key rather than overwrite it."""
    path = ledger_path(root)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("slug") == slug and record.get("event") == event:
            for key, value in changes.items():
                if value is _DROP:
                    record.pop(key, None)
                else:
                    record[key] = value
        out.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _seed_project(root: Path, tmp_path: Path, project: str = "neurobase") -> None:
    core_store.ensure_tree(project, root)
    core_projects.register_project(root, tmp_path / project, slug=project)


def _seed_raw(
    root: Path,
    project: str,
    *,
    session_id: str,
    captured_at: datetime,
    body: str,
) -> None:
    core_store.write_raw(
        root,
        project,
        agent="claude",
        session_id=session_id,
        cwd="/repo",
        branch="main",
        captured_at=captured_at,
        body=body,
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


# --- survival: the remaining fallback branches (§12.9) -----------------------
#
# Each case below is a ledger/proposal shape that resolves to a documented
# fallback rather than a crash. The first two reproduce ledgers this codebase
# no longer writes but must still read: an unparseable timestamp, and a legacy
# accepted event predating `installed_hash` (D2).


def test_survival_insufficient_data_when_accepted_timestamp_unparseable(tmp_path: Path) -> None:
    """An `accepted` event whose `at` will not parse yields no resolvable
    acceptance time, so survival is "insufficient_data" — never
    "not_survived", which would blame an artifact for a bad clock stamp. The
    proposal still counts as decided: `compute_metrics` matches on the event's
    presence, which does not depend on the timestamp parsing."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    _rewrite_ledger_event(root, "prefer-uv-run", "accepted", at="not-a-timestamp")
    installed_path.unlink()  # gone, and still not reported as not_survived

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=365))

    assert result.decided == 1
    assert result.accepted == 1
    assert result.survival["prefer-uv-run"] == "insufficient_data"


def test_legacy_accepted_event_without_hash_falls_back_to_existence(tmp_path: Path) -> None:
    """A legacy `accepted` event with no stored `installed_hash` (D2) cannot
    detect modification, only presence: past the window an existing artifact
    reports "survived" even though its bytes have since changed."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    _rewrite_ledger_event(root, "prefer-uv-run", "accepted", installed_hash=_DROP)
    installed_path.write_text("hand-edited since acceptance", encoding="utf-8")

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=31))

    # Existence-only: the edit is invisible without a recorded hash to compare.
    assert result.survival["prefer-uv-run"] == "survived"


def test_survival_insufficient_data_when_installed_path_absent(tmp_path: Path) -> None:
    """A proposal accepted in the ledger but carrying no `installed_path`
    gives nothing to check, so survival is "insufficient_data" rather than a
    not_survived verdict drawn from a path that was never recorded.

    The key is dropped, not blanked: the proposal schema admits `installed_path`
    only as null or an **absolute** path string, so an empty string would fail
    validation and drop the whole proposal from `load_all_proposals` — never
    reaching the survival check at all."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    _accept(root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW)

    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None
    frontmatter = dict(doc.frontmatter)
    del frontmatter["installed_path"]
    core_store.write_doc(doc.file_path, frontmatter, doc.body)

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=31))

    assert result.decided == 1  # still decided — only the artifact is unknown
    assert result.survival["prefer-uv-run"] == "insufficient_data"


def test_survival_not_survived_when_artifact_cannot_be_read(tmp_path: Path) -> None:
    """The artifact path exists but reading its bytes raises OSError (here: it
    is a directory, not a file). A hash comparison is impossible, so the
    verdict is "not_survived" — the artifact is provably not intact."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW)
    installed_path = tmp_path / "AGENTS.md"
    _accept(root, "prefer-uv-run", installed_path=installed_path, now=NOW)
    installed_path.unlink()
    installed_path.mkdir()  # same path, now unreadable as bytes

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=31))

    assert result.survival["prefer-uv-run"] == "not_survived"


# --- recurrence reduction (advisory, §12.9 / D19) ----------------------------
#
# The aggregate `after / before` ratio over near-duplicate raw captures. Note
# `_recurrence_reduction` loads the corpus with the DEFAULT config rather than
# the injected one, so raw captures here stay inside the default 30-day
# lookback relative to `now`; widening `raw_lookback_days` via `config=` would
# not reach it.


def _duplicate_capture_body(root: Path, slug: str) -> str:
    """The proposal's own rendered body — reused verbatim as a raw capture so
    the Jaccard near-duplicate test is unambiguously satisfied, isolating what
    these tests actually exercise: the before/after partition and the ratio."""
    doc = proposals.load_proposal(root, slug)
    assert doc is not None
    return doc.body


def test_recurrence_reduction_ratio_partitions_captures_around_acceptance(
    tmp_path: Path,
) -> None:
    """Near-duplicate captures are split by the acceptance timestamp and
    reported as `after / before`: two before and one after gives 0.5. A
    capture that is not a near-duplicate is excluded from both sides."""
    root = tmp_path / "store"
    _seed_project(root, tmp_path)
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW - timedelta(days=5))
    _accept(root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW)
    recurring = _duplicate_capture_body(root, "prefer-uv-run")

    _seed_raw(
        root,
        "neurobase",
        session_id="before01",
        captured_at=NOW - timedelta(days=3),
        body=recurring,
    )
    _seed_raw(
        root,
        "neurobase",
        session_id="before02",
        captured_at=NOW - timedelta(days=2),
        body=recurring,
    )
    _seed_raw(
        root,
        "neurobase",
        session_id="after001",
        captured_at=NOW + timedelta(days=1),
        body=recurring,
    )
    _seed_raw(
        root,
        "neurobase",
        session_id="unrelate",
        captured_at=NOW - timedelta(days=1),
        body="Postgres index bloat and the query planner's cost estimates.",
    )

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=2))

    assert result.recurrence_reduction == 0.5


def test_recurrence_reduction_none_when_no_captures_precede_acceptance(tmp_path: Path) -> None:
    """With zero "before" occurrences there is nothing to compare against, so
    the ratio is None ("insufficient data") rather than a misleading 0 or a
    divide-by-zero — even though "after" captures exist."""
    root = tmp_path / "store"
    _seed_project(root, tmp_path)
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW - timedelta(days=5))
    _accept(root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW)
    recurring = _duplicate_capture_body(root, "prefer-uv-run")

    _seed_raw(
        root,
        "neurobase",
        session_id="after001",
        captured_at=NOW + timedelta(days=1),
        body=recurring,
    )

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=2))

    assert result.recurrence_reduction is None


def test_recurrence_reduction_counts_a_capture_at_the_acceptance_instant_as_after(
    tmp_path: Path,
) -> None:
    """The partition is `when < accepted_at`, so a capture stamped at exactly
    the acceptance instant counts as "after", not "before"."""
    root = tmp_path / "store"
    _seed_project(root, tmp_path)
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW - timedelta(days=5))
    _accept(root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW)
    recurring = _duplicate_capture_body(root, "prefer-uv-run")

    _seed_raw(
        root,
        "neurobase",
        session_id="before01",
        captured_at=NOW - timedelta(days=1),
        body=recurring,
    )
    _seed_raw(root, "neurobase", session_id="exactly1", captured_at=NOW, body=recurring)

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=2))

    # 1 after / 1 before — the boundary capture landed on the "after" side.
    assert result.recurrence_reduction == 1.0


def test_recurrence_reduction_skips_proposal_with_unparseable_acceptance(tmp_path: Path) -> None:
    """A proposal whose acceptance time will not parse is skipped entirely:
    its captures join neither side, so with no other accepted proposal the
    ratio is None rather than being computed against an unknown boundary."""
    root = tmp_path / "store"
    _seed_project(root, tmp_path)
    proposals.write_ranked(root, [_ranked("prefer-uv-run")], now=NOW - timedelta(days=5))
    _accept(root, "prefer-uv-run", installed_path=tmp_path / "AGENTS.md", now=NOW)
    recurring = _duplicate_capture_body(root, "prefer-uv-run")
    _rewrite_ledger_event(root, "prefer-uv-run", "accepted", at="not-a-timestamp")

    _seed_raw(
        root,
        "neurobase",
        session_id="before01",
        captured_at=NOW - timedelta(days=2),
        body=recurring,
    )
    _seed_raw(
        root,
        "neurobase",
        session_id="after001",
        captured_at=NOW + timedelta(days=1),
        body=recurring,
    )

    result = metrics.compute_metrics(root, now=NOW + timedelta(days=2))

    assert result.decided == 1  # still decided; only the timestamp is unusable
    assert result.recurrence_reduction is None
