"""Tests for the miner (spec §12.5, execution plan workstream D) —
``recommender/miner.py`` — with a fake injected brain (no network).

Covers the three named workstream-D tests (unparseable miner JSON leaves
proposals unchanged; invalid candidates skipped with warnings; rejected
near-duplicate summary reaches prompt) plus the candidate normalization,
evidence handling, and ledger-dedup behaviors this slice implements."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.brain.base import BrainError
from neurobase.core import store
from neurobase.core.config import RecommendConfig
from neurobase.recommender import corpus, miner


class FakeBrain:
    """Fake brain: ``plan`` is the dict (or Exception) returned by plan_json;
    captures the last ``(system, user)`` it was called with so a test can
    assert what reached the prompt."""

    name = "fake"

    def __init__(self, plan: Any) -> None:
        self._plan = plan
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.plan_calls = 0

    def plan_json(self, system: str, user: str) -> dict:
        self.plan_calls += 1
        self.last_system = system
        self.last_user = user
        if isinstance(self._plan, Exception):
            raise self._plan
        return self._plan

    def text(self, system: str, user: str) -> str:  # pragma: no cover - miner never calls text
        raise AssertionError("miner must not call brain.text()")


def _write_registry(root: Path, slugs: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for slug in slugs:
        lines.append(f"[projects.{slug}]")
        lines.append(f'roots = ["/repos/{slug}"]')
    (root / "registry.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _valid_candidate(**overrides: Any) -> dict[str, Any]:
    base = {
        "slug": "prefer-uv-run",
        "type": "rule",
        "candidate_type": "repeated-instruction",
        "title": "Prefer uv run",
        "rationale": "corrected repeatedly",
        "draft": "Always invoke Python via `uv run`.",
        "target": "AGENTS.md",
        "evidence": [{"kind": "curated", "project": "neurobase", "slug": "use-uv-not-pip"}],
        "occurrences": 5,
        "projects": ["neurobase"],
        "agents": ["claude", "codex"],
        "supersedes": [],
    }
    base.update(overrides)
    return base


# --- named test 1: unparseable miner JSON leaves proposals unchanged ----------


def test_unparseable_miner_json_returns_empty(tmp_path: Path) -> None:
    """Workstream D: 'unparseable miner JSON leaves proposals unchanged'. An
    unparseable answer surfaces as a BrainError from plan_json's retry wrapper;
    mine() swallows it and returns [] so the caller writes nothing."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    brain = FakeBrain(BrainError("plan JSON did not parse"))

    result = miner.mine(root, brain)

    assert result == []
    assert brain.plan_calls == 1


def test_response_without_candidates_list_returns_empty(tmp_path: Path) -> None:
    """A cleanly-parsed response that isn't the {'candidates': [...]} envelope
    degrades to an empty proposal set, not a crash."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    assert miner.mine(root, FakeBrain({"not_candidates": 1})) == []
    assert miner.mine(root, FakeBrain({"candidates": "nope"})) == []


# --- named test 2: invalid candidates skipped with warnings -------------------


def test_invalid_candidates_skipped_with_warnings(tmp_path: Path, caplog: Any) -> None:
    """Workstream D: 'invalid candidates skipped with warnings'. Each structural
    defect is dropped (and logged), while the one valid candidate survives."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    plan = {
        "candidates": [
            _valid_candidate(),  # keeper
            _valid_candidate(slug="Bad Slug"),  # bad slug
            _valid_candidate(slug=""),  # missing slug
            _valid_candidate(draft="   "),  # blank draft
            _valid_candidate(type="doc"),  # disallowed type
            _valid_candidate(candidate_type="random"),  # disallowed candidate_type
            "not even an object",  # not a dict
        ]
    }
    brain = FakeBrain(plan)

    with caplog.at_level(logging.WARNING):
        result = miner.mine(root, brain)

    assert [c["slug"] for c in result] == ["prefer-uv-run"]
    # Every one of the six invalid entries logged a skip warning.
    skip_warnings = [r for r in caplog.records if "skipped" in r.getMessage()]
    assert len(skip_warnings) == 6


def test_valid_candidate_is_normalized(tmp_path: Path) -> None:
    """A surviving candidate carries exactly the normalized §12.5 field shape,
    with self-reported counts coerced but preserved (they're advisory-only)."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    result = miner.mine(root, FakeBrain({"candidates": [_valid_candidate(occurrences="7")]}))

    assert len(result) == 1
    cand = result[0]
    assert cand["slug"] == "prefer-uv-run"
    assert cand["type"] == "rule"
    assert cand["candidate_type"] == "repeated-instruction"
    assert cand["draft"] == "Always invoke Python via `uv run`."
    assert cand["evidence"] == [
        {"kind": "curated", "project": "neurobase", "slug": "use-uv-not-pip"}
    ]
    assert cand["occurrences"] == 7  # coerced from the string "7"


def test_malformed_evidence_entries_dropped_candidate_survives(tmp_path: Path) -> None:
    """Malformed evidence items are filtered out (§12.1), but never fail the
    candidate — the ranker just has fewer refs to count from."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    plan = {
        "candidates": [
            _valid_candidate(
                evidence=[
                    {"kind": "curated", "project": "p", "slug": "s"},  # valid
                    {"kind": "bogus"},  # bad kind
                    "not a dict",  # not an object
                    {"kind": "raw", "project": "p"},  # missing 'file'
                ]
            )
        ]
    }
    result = miner.mine(root, FakeBrain(plan))

    assert result[0]["evidence"] == [{"kind": "curated", "project": "p", "slug": "s"}]


# --- named test 3: rejected near-duplicate summary reaches prompt -------------


def _seed_rejected_proposal(root: Path, slug: str, body: str, candidate_type: str) -> None:
    store.write_doc(
        corpus.proposal_path(root, slug),
        {"name": slug, "status": "rejected", "candidate_type": candidate_type},
        body,
    )
    ledger = corpus.ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "at": "2026-07-09T12:00:00Z",
                    "slug": slug,
                    "event": "rejected",
                    "candidate_type": candidate_type,
                }
            )
            + "\n"
        )


def test_rejected_near_duplicate_summary_reaches_prompt(tmp_path: Path) -> None:
    """Workstream D: 'rejected near-duplicate summary reaches prompt'. The
    ledger's rejected proposal (body + per-type reject count) must appear in the
    user payload the brain receives, with an instruction to avoid re-proposing
    it in the system prompt."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    _seed_rejected_proposal(
        root, "no-global-pip", "Do not install packages globally with pip.", "repeated-correction"
    )
    brain = FakeBrain({"candidates": []})

    miner.mine(root, brain)

    assert brain.last_user is not None
    payload = json.loads(brain.last_user)
    summary = payload["ledger_summary"]
    assert summary["reject_counts_by_type"] == {"repeated-correction": 1}
    rejected = summary["rejected_proposals"]
    assert len(rejected) == 1
    assert rejected[0]["slug"] == "no-global-pip"
    assert "Do not install packages globally with pip." in rejected[0]["snippet"]
    # The system prompt instructs the model to honor rejections.
    assert brain.last_system is not None
    assert "REJECTED PROPOSALS" in brain.last_system


def test_near_duplicate_rejected_snippets_are_deduped(tmp_path: Path) -> None:
    """§12.4's near-duplicate function selects which rejected snippets reach the
    prompt: two near-identical rejections collapse to one representative, while
    a genuinely different rejection is kept."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    _seed_rejected_proposal(
        root,
        "no-pip-a",
        "Never install packages globally with bare pip install.",
        "repeated-correction",
    )
    _seed_rejected_proposal(
        root,
        "no-pip-b",
        "Never install packages globally with bare pip install please.",
        "repeated-correction",
    )
    _seed_rejected_proposal(
        root,
        "use-ruff",
        "Format all Python code with ruff before committing.",
        "repeated-instruction",
    )
    brain = FakeBrain({"candidates": []})

    miner.mine(root, brain, config=RecommendConfig(near_duplicate_threshold=0.6))

    payload = json.loads(brain.last_user or "{}")
    slugs = {rp["slug"] for rp in payload["ledger_summary"]["rejected_proposals"]}
    # The two near-identical pip rejections collapse to one; the ruff one stays.
    assert "use-ruff" in slugs
    assert len(slugs & {"no-pip-a", "no-pip-b"}) == 1
    assert len(slugs) == 2


# --- corpus content reaches the prompt ---------------------------------------


def test_corpus_facts_and_captures_reach_prompt(tmp_path: Path) -> None:
    """The miner reasons over the whole corpus — curated facts and recent raw
    captures across every project land in the user payload."""
    root = tmp_path / "store"
    _write_registry(root, ["neurobase"])
    store.ensure_tree("neurobase", root)
    store.upsert_curated(root, "neurobase", "use-uv-not-pip", "Use uv, not pip.")
    now = datetime(2026, 7, 10, tzinfo=UTC)
    store.write_raw(
        root,
        "neurobase",
        agent="claude",
        session_id="sess0001",
        cwd="/r",
        branch="main",
        captured_at=now,
        body="reminder: uv run not pip",
    )
    brain = FakeBrain({"candidates": []})

    miner.mine(root, brain, now=now)

    payload = json.loads(brain.last_user or "{}")
    assert payload["curated_facts"][0]["slug"] == "use-uv-not-pip"
    assert payload["raw_captures"][0]["agent"] == "claude"
    assert "uv run not pip" in payload["raw_captures"][0]["body"]
