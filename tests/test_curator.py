"""Tests for the curator engine (spec §2), with a fake injected brain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from neurobase.brain.base import BrainError
from neurobase.core import store
from neurobase.curator import engine


class FakeBrain:
    """Fake brain: `plan` is the dict (or exception) returned by plan_json;
    `node_text` is what text() returns."""

    name = "fake"

    def __init__(self, plan: Any = None, node_text: Any = "# Status\n\nsynth body") -> None:
        self._plan = plan if plan is not None else {"upserts": [], "tombstones": []}
        self._node_text = node_text
        self.plan_calls = 0
        self.text_calls = 0

    def plan_json(self, system: str, user: str) -> dict:
        self.plan_calls += 1
        if isinstance(self._plan, Exception):
            raise self._plan
        return self._plan

    def text(self, system: str, user: str) -> str:
        self.text_calls += 1
        if isinstance(self._node_text, Exception):
            raise self._node_text
        return self._node_text


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store"


def _write_raw(
    root: Path,
    project: str,
    name: str,
    body: str = "raw body",
    *,
    when: str = "2026-07-07T12:00:00Z",
) -> Path:
    store.ensure_tree(project, root)
    path = store.memory_dir(project, root) / "raw" / name
    store.write_doc(
        path,
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": when,
            "consumed": False,
        },
        body,
    )
    return path


# --- step 1: idempotence / noop ------------------------------------------


def test_no_raw_is_noop(root: Path) -> None:
    store.ensure_tree("proj", root)
    brain = FakeBrain()
    summary = engine.curate(root, "proj", brain)
    assert summary["status"] == "noop"
    assert brain.plan_calls == 0  # never called the model


def test_valid_but_empty_plan_consumes_raw(root: Path) -> None:
    """A valid-but-empty plan IS consumed (distinct from a parse failure)."""
    raw = _write_raw(root, "proj", "r1.md")
    summary = engine.curate(root, "proj", FakeBrain({"upserts": [], "tombstones": []}))
    assert summary["status"] == "ok"
    assert store.read_doc(raw)["consumed"] is True
    assert store.list_curated(root, "proj") == []


# --- step 3: parse failure aborts, leaves raw unconsumed ------------------


def test_plan_error_aborts_and_leaves_raw_unconsumed(root: Path) -> None:
    raw = _write_raw(root, "proj", "r1.md")
    brain = FakeBrain(BrainError("unparseable after retry"))
    summary = engine.curate(root, "proj", brain)
    assert summary["status"] == "error"
    assert store.read_doc(raw)["consumed"] is False  # the hard rule (D9)
    assert brain.text_calls == 0  # no synthesis on abort


# --- step 4: upserts, provenance, supersession ---------------------------


def test_upsert_writes_fact_with_provenance(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {"slug": "fact-a", "body": "durable fact", "supersedes": [], "from_raw": ["r1.md"]}
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["upserts"] == 1
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc.body.split("\n")[0].startswith("durable fact")
    assert doc["provenance"] == ["raw/r1.md"]


def test_superseded_slug_is_tombstoned(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "old-fact", "old body")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {"slug": "new-fact", "body": "new body", "supersedes": ["old-fact"], "from_raw": []}
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["superseded"] == 1
    assert not (store.memory_dir("proj", root) / "curated" / "old-fact.md").exists()
    assert (store.memory_dir("proj", root) / ".tombstones" / "old-fact.md").exists()


def test_superseded_slug_reupserted_this_pass_is_not_tombstoned(root: Path) -> None:
    """Step 4: tombstone a superseded slug UNLESS it was re-upserted this pass."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-x", "v1")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            # fact-x is both re-upserted AND listed as superseded by another upsert
            {"slug": "fact-x", "body": "v2", "supersedes": [], "from_raw": []},
            {"slug": "fact-y", "body": "y", "supersedes": ["fact-x"], "from_raw": []},
        ],
        "tombstones": [],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    # fact-x survived (re-upserted), fact-y created.
    assert (store.memory_dir("proj", root) / "curated" / "fact-x.md").exists()
    assert (store.memory_dir("proj", root) / "curated" / "fact-y.md").exists()


def test_empty_slug_or_body_skipped(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {"slug": "", "body": "no slug", "from_raw": []},
            {"slug": "no-body", "body": "", "from_raw": []},
            {"slug": "good", "body": "ok", "from_raw": []},
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["upserts"] == 1
    assert [d["name"] for d in store.list_curated(root, "proj")] == ["good"]


def test_bad_slug_skipped_not_fatal(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {"slug": "Bad Slug!", "body": "x", "from_raw": []},
            {"slug": "good", "body": "ok", "from_raw": []},
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["upserts"] == 1
    assert [d["name"] for d in store.list_curated(root, "proj")] == ["good"]


def test_supersedes_filtered_of_self(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [{"slug": "fact-a", "body": "v2", "supersedes": ["fact-a"], "from_raw": []}],
        "tombstones": [],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    # fact-a superseding itself must not tombstone itself.
    assert (store.memory_dir("proj", root) / "curated" / "fact-a.md").exists()


# --- step 5: explicit tombstones -----------------------------------------


def test_explicit_tombstone_applied(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "stale", "body")
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [], "tombstones": [{"slug": "stale", "reason": "obsolete"}]}
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["tombstones"] == 1
    assert not (store.memory_dir("proj", root) / "curated" / "stale.md").exists()


def test_explicit_tombstone_skipped_if_upserted_this_pass(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "v1")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [{"slug": "fact-a", "body": "v2", "from_raw": []}],
        "tombstones": [{"slug": "fact-a", "reason": "?"}],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    assert (store.memory_dir("proj", root) / "curated" / "fact-a.md").exists()


# --- step 8 + partial-failure --------------------------------------------


def test_node_and_index_regenerated(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": ["r1.md"]}], "tombstones": []}
    engine.curate(root, "proj", FakeBrain(plan, node_text="# My Node\n\ncurrent work"))
    node = store.read_doc(store.memory_dir("proj", root) / "nodes" / "proj-status.md")
    assert "My Node" in node.body
    assert "## Synthesized from" in node.body and "[[fact-a]]" in node.body
    index = (store.memory_dir("proj", root) / "index.md").read_text()
    assert "1 active curated facts" in index


def test_node_synthesis_failure_is_partial_but_keeps_applied_state(root: Path) -> None:
    raw = _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": []}], "tombstones": []}
    brain = FakeBrain(plan, node_text=BrainError("node synth failed"))
    summary = engine.curate(root, "proj", brain)
    assert summary["status"] == "partial"
    # applied state kept: fact written, raw consumed.
    assert (store.memory_dir("proj", root) / "curated" / "fact-a.md").exists()
    assert store.read_doc(raw)["consumed"] is True


def test_non_brain_step8_failure_is_partial_after_consumption(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §2: ANY step-8 failure after raws are consumed (not just a
    BrainError) — e.g. a malformed sibling node tripping the index rebuild — is
    `partial`, not a crash. Applied state stands; the error is logged."""
    raw = _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": []}], "tombstones": []}

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("malformed sibling node broke the index rebuild")

    monkeypatch.setattr(engine.store, "rebuild_index", boom)

    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["status"] == "partial"
    assert "error" in summary
    # applied state kept: fact written, raw consumed — no crash.
    assert (store.memory_dir("proj", root) / "curated" / "fact-a.md").exists()
    assert store.read_doc(raw)["consumed"] is True
    # the partial pass was logged.
    trend = engine.read_fact_count_trend(root, "proj")
    assert trend == [1]


def test_resynth_regenerates_without_new_raw(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "fact-a", "x")
    brain = FakeBrain(node_text="# Resynth node")
    summary = engine.curate(root, "proj", brain, resynth=True)
    assert summary["status"] == "resynth"
    assert brain.plan_calls == 0  # no plan step on resynth
    node = store.read_doc(store.memory_dir("proj", root) / "nodes" / "proj-status.md")
    assert "Resynth node" in node.body


# --- dry-run --------------------------------------------------------------


def test_dry_run_changes_nothing(root: Path) -> None:
    raw = _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": []}], "tombstones": []}
    summary = engine.curate(root, "proj", FakeBrain(plan), dry_run=True)
    assert summary["status"] == "dry-run"
    assert summary["plan"] == plan
    assert store.list_curated(root, "proj") == []  # nothing applied
    assert store.read_doc(raw)["consumed"] is False  # nothing consumed


# --- curator log + trend --------------------------------------------------


def test_pass_logged_and_trend_read(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": []}], "tombstones": []}
    engine.curate(root, "proj", FakeBrain(plan))
    engine.curate(root, "proj", FakeBrain())  # noop pass
    trend = engine.read_fact_count_trend(root, "proj")
    assert trend == [1, 1]


# --- --if-stale gate ------------------------------------------------------


def test_is_stale_true_when_old_raw(root: Path) -> None:
    old = (datetime.now(UTC) - timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    _write_raw(root, "proj", "r1.md", when=old)
    assert engine.is_stale(root, "proj", hours=12) is True


def test_is_stale_false_when_recent(root: Path) -> None:
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    _write_raw(root, "proj", "r1.md", when=recent)
    assert engine.is_stale(root, "proj", hours=12) is False


def test_is_stale_false_when_no_raw(root: Path) -> None:
    store.ensure_tree("proj", root)
    assert engine.is_stale(root, "proj", hours=12) is False


# --- node prompt fence stripping -----------------------------------------


def test_node_text_outer_fence_stripped(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "x", "from_raw": []}], "tombstones": []}
    fenced = "```markdown\n# Node\n\nbody\n```"
    engine.curate(root, "proj", FakeBrain(plan, node_text=fenced))
    node = store.read_doc(store.memory_dir("proj", root) / "nodes" / "proj-status.md")
    assert node.body.startswith("# Node")
    assert "```" not in node.body.split("## Synthesized from")[0]


# --- pinned user-directed facts (spec §2, decision D-b) ------------------


def _pinned_fact(root: Path, project: str, slug: str, body: str) -> None:
    store.ensure_tree(project, root)
    store.upsert_curated(root, project, slug, body, provenance=["user-directed"])


def test_pinned_fact_is_not_tombstoned_or_reworded(root: Path) -> None:
    _pinned_fact(root, "proj", "prefer-uv", "Prefer uv over pip.")
    _write_raw(root, "proj", "r1.md")
    # Adversarial plan: try to both reword (upsert same slug) and tombstone it.
    plan = {
        "upserts": [{"slug": "prefer-uv", "body": "Use pip.", "from_raw": ["r1.md"]}],
        "tombstones": [{"slug": "prefer-uv", "reason": "outdated"}],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    facts = {d.get("name"): d.body for d in store.list_curated(root, "proj")}
    # Survived (not tombstoned) and unchanged content (not reworded). A linkify
    # lineage footer (spec §6) may be appended — check content, not equality.
    assert facts["prefer-uv"].startswith("Prefer uv over pip.")
    assert "Use pip." not in facts["prefer-uv"]


def test_pinned_fact_survives_attempted_supersession(root: Path) -> None:
    _pinned_fact(root, "proj", "prefer-uv", "Prefer uv over pip.")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {"slug": "use-pip", "body": "Use pip.", "supersedes": ["prefer-uv"], "from_raw": ["r1.md"]}
        ],
        "tombstones": [],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    slugs = {d.get("name") for d in store.list_curated(root, "proj")}
    assert "prefer-uv" in slugs  # supersession did not remove the pinned fact
    assert "use-pip" in slugs  # the new fact still lands


def test_non_pinned_fact_can_still_be_tombstoned(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "temp-fact", "ephemeral", provenance=["claude:scribe"])
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [], "tombstones": [{"slug": "temp-fact", "reason": "stale"}]}
    engine.curate(root, "proj", FakeBrain(plan))
    slugs = {d.get("name") for d in store.list_curated(root, "proj")}
    assert "temp-fact" not in slugs  # the guard is specific to pinned facts
