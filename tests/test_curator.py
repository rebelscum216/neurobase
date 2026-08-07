"""Tests for the curator engine (spec §2), with a fake injected brain."""

from __future__ import annotations

import json
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


class SequencedBrain(FakeBrain):
    def __init__(self, plans: list[Any], node_text: str = "# Status") -> None:
        super().__init__(node_text=node_text)
        self._plans = iter(plans)
        self.plan_users: list[str] = []

    def plan_json(self, system: str, user: str) -> dict:
        self.plan_calls += 1
        self.plan_users.append(user)
        plan = next(self._plans)
        if isinstance(plan, Exception):
            raise plan
        return plan


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


def _read_log(root: Path, project: str) -> list[dict[str, Any]]:
    """Parse the curator journal (`.curator-log.jsonl`) into records."""
    path = store.memory_dir(project, root) / engine.CURATOR_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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


# --- size-aware plan batching --------------------------------------------


def _request_size_for(root: Path, raw_names: list[str]) -> int:
    docs = {d.file_path.name: d for d in store.list_raw(root, "proj")}
    entries = [engine._raw_payload(docs[name]) for name in raw_names]
    payload = engine._plan_user_payload(store.list_curated(root, "proj"), entries)
    return engine._plan_request_bytes(payload)


def test_single_batch_payload_is_v01_byte_identical(root: Path) -> None:
    _write_raw(root, "proj", "r1.md", "alpha")
    _write_raw(root, "proj", "r2.md", "beta")
    brain = SequencedBrain([{"upserts": [], "tombstones": []}])

    summary = engine.curate(root, "proj", brain)

    expected = json.dumps(
        {
            "curated_facts": [],
            "raw_captures": [
                {"raw": "r1.md", "body": "alpha"},
                {"raw": "r2.md", "body": "beta"},
            ],
        },
        ensure_ascii=False,
    )
    assert brain.plan_users == [expected]
    assert summary["batches"] == 1


def test_batches_oldest_first_and_reloads_facts_between_batches(root: Path) -> None:
    first = _write_raw(root, "proj", "r1.md", "a" * 80)
    second = _write_raw(root, "proj", "r2.md", "b" * 80)
    one_size = _request_size_for(root, ["r1.md"])
    two_size = _request_size_for(root, ["r1.md", "r2.md"])
    assert one_size < two_size
    brain = SequencedBrain(
        [
            {
                "upserts": [{"slug": "from-first", "body": "new state", "from_raw": ["r1.md"]}],
                "tombstones": [],
            },
            {"upserts": [], "tombstones": []},
        ]
    )

    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=one_size)

    assert summary["batches"] == 2
    assert store.read_doc(first)["consumed"] is True
    assert store.read_doc(second)["consumed"] is True
    first_payload, second_payload = map(json.loads, brain.plan_users)
    assert [r["raw"] for r in first_payload["raw_captures"]] == ["r1.md"]
    assert [r["raw"] for r in second_payload["raw_captures"]] == ["r2.md"]
    assert second_payload["curated_facts"] == [{"slug": "from-first", "body": "new state"}]
    assert all(engine._plan_request_bytes(user) <= one_size for user in brain.plan_users)
    assert brain.text_calls == 1


def test_batch_failure_keeps_earlier_commit_and_later_raws_unconsumed(root: Path) -> None:
    first = _write_raw(root, "proj", "r1.md", "a" * 80)
    second = _write_raw(root, "proj", "r2.md", "b" * 80)
    third = _write_raw(root, "proj", "r3.md", "c" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = SequencedBrain(
        [
            {
                "upserts": [{"slug": "landed", "body": "yes", "from_raw": ["r1.md"]}],
                "tombstones": [],
            },
            BrainError("batch two failed"),
        ]
    )

    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)

    assert summary["status"] == "error"
    assert summary["batches"] == 1
    assert store.read_doc(first)["consumed"] is True
    assert store.read_doc(second)["consumed"] is False
    assert store.read_doc(third)["consumed"] is False
    assert (store.memory_dir("proj", root) / "curated" / "landed.md").exists()
    # The pass failed, but batch 1's facts are committed — so the derived node
    # MUST be refreshed anyway, or recall would never see them: retrying only
    # re-hits the same failing batch, so "a later pass will fix it" is false.
    assert brain.text_calls == 1
    node = store.memory_dir("proj", root) / "nodes" / "proj-status.md"
    assert node.exists()
    assert summary["upserts"] == 1
    assert summary["active_facts"] == 1


def test_single_oversize_unicode_raw_is_marked_and_fits_byte_budget(root: Path) -> None:
    raw = _write_raw(root, "proj", "r1.md", "🧠" * 500)
    # Pick a budget that fits a marked prefix but not the full four-byte body.
    doc = store.read_doc(raw)
    minimum = engine._plan_request_bytes(
        engine._plan_user_payload([], [{"raw": "r1.md", "body": engine.OVERSIZE_RAW_MARKER}])
    )
    budget = minimum + 100
    brain = SequencedBrain([{"upserts": [], "tombstones": []}])

    engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)

    rendered = brain.plan_users[0]
    assert engine._plan_request_bytes(rendered) <= budget
    body = json.loads(rendered)["raw_captures"][0]["body"]
    assert body.endswith("[truncated for plan payload]")
    assert len(body) < len(doc.body)


def test_recall_sees_committed_facts_even_when_a_later_batch_keeps_failing(root: Path) -> None:
    """A permanently-failing raw must not hold committed facts out of recall
    forever. Two passes both fail on batch 2; the node still carries batch 1."""
    _write_raw(root, "proj", "r1.md", "a" * 80)
    _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = SequencedBrain(
        [
            {"upserts": [{"slug": "landed", "body": "durable", "from_raw": ["r1.md"]}]},
            BrainError("poison raw"),
            BrainError("poison raw"),  # the retry hits the same wall
        ],
        node_text="# Status\n\nlanded: durable",
    )

    first_pass = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    node = store.memory_dir("proj", root) / "nodes" / "proj-status.md"
    assert first_pass["status"] == "error"
    assert "landed" in node.read_text(encoding="utf-8")

    # Second pass: only the poison raw is left, nothing commits, node untouched.
    second_pass = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    assert second_pass["status"] == "error"
    assert second_pass["batches"] == 0
    assert "landed" in node.read_text(encoding="utf-8")


def test_dry_run_previews_every_batch_and_consumes_nothing(root: Path) -> None:
    first = _write_raw(root, "proj", "r1.md", "a" * 80)
    second = _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = SequencedBrain(
        [
            {"upserts": [{"slug": "one", "body": "x", "from_raw": ["r1.md"]}], "tombstones": []},
            {"upserts": [{"slug": "two", "body": "y", "from_raw": ["r2.md"]}], "tombstones": []},
        ]
    )

    summary = engine.curate(root, "proj", brain, dry_run=True, plan_payload_max_bytes=budget)

    assert summary["status"] == "dry-run"
    assert summary["batches"] == 2
    assert [p["upserts"][0]["slug"] for p in summary["plans"]] == ["one", "two"]
    assert store.read_doc(first)["consumed"] is False
    assert store.read_doc(second)["consumed"] is False
    assert not (store.memory_dir("proj", root) / "curated" / "one.md").exists()
    assert brain.text_calls == 0
    assert not (store.memory_dir("proj", root) / engine.CURATOR_LOG).exists()


def test_budget_too_small_errors_without_consuming(root: Path) -> None:
    raw = _write_raw(root, "proj", "r1.md", "body")
    summary = engine.curate(root, "proj", FakeBrain(), plan_payload_max_bytes=1)
    assert summary["status"] == "error"
    assert store.read_doc(raw)["consumed"] is False


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


def test_is_stale_true_when_backlog_reaches_trigger(root: Path) -> None:
    """The count arm fires on a backlog of recent raws the time arm would pass
    over. Without it the time gate guarantees a pile-up before anything folds
    (G19)."""
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    for i in range(3):
        _write_raw(root, "proj", f"r{i}.md", when=recent)
    assert engine.is_stale(root, "proj", hours=12, trigger_raws=3) is True


def test_is_stale_false_below_trigger_and_not_old(root: Path) -> None:
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    for i in range(2):
        _write_raw(root, "proj", f"r{i}.md", when=recent)
    assert engine.is_stale(root, "proj", hours=12, trigger_raws=3) is False


def test_is_stale_time_arm_still_fires_below_trigger(root: Path) -> None:
    """The two arms are independent: one old raw is enough even though the
    backlog never reaches the count. A pure count starves the tail."""
    old = (datetime.now(UTC) - timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    _write_raw(root, "proj", "r1.md", when=old)
    assert engine.is_stale(root, "proj", hours=12, trigger_raws=3) is True


def test_is_stale_count_arm_ignores_consumed_raws(root: Path) -> None:
    """The backlog is unconsumed raws only — consumed ones must not push the
    trigger, or every pass would immediately re-arm the next one."""
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    for i in range(4):
        path = _write_raw(root, "proj", f"r{i}.md", when=recent)
        doc = store.read_doc(path)
        store.write_doc(path, {**doc.frontmatter, "consumed": True}, doc.body)
    assert engine.is_stale(root, "proj", hours=12, trigger_raws=3) is False


# --- plan batch ceilings (G19) -------------------------------------------


def test_plan_batch_stops_at_raw_ceiling(root: Path) -> None:
    """The raw-count ceiling binds even when the byte budget is nowhere near
    it — the G19 condition exactly: small payload, too many raws."""
    docs = [store.read_doc(_write_raw(root, "proj", f"r{i}.md", body="tiny")) for i in range(10)]
    batch, _ = engine._next_plan_batch([], docs, 1_000_000, 3)
    assert len(batch) == 3
    assert [d.file_path.name for d in batch] == ["r0.md", "r1.md", "r2.md"]


def test_plan_batch_byte_budget_still_binds_below_raw_ceiling(root: Path) -> None:
    """Whichever ceiling binds first wins — the byte cap is not weakened by
    adding the count cap."""
    docs = [
        store.read_doc(_write_raw(root, "proj", f"r{i}.md", body="x" * 4_000)) for i in range(5)
    ]
    # a cap sized to exactly one raw, derived rather than guessed
    one_raw = engine._plan_request_bytes(
        engine._plan_user_payload([], [engine._raw_payload(docs[0])])
    )
    batch, _ = engine._next_plan_batch([], docs, one_raw, 99)
    assert len(batch) == 1  # the byte cap binds long before the count cap does


def test_plan_batch_rejects_non_positive_raw_ceiling(root: Path) -> None:
    docs = [store.read_doc(_write_raw(root, "proj", "r0.md"))]
    with pytest.raises(ValueError, match="raw-count budget must be positive"):
        engine._next_plan_batch([], docs, 1_000_000, 0)


def test_curate_splits_backlog_into_raw_capped_batches(root: Path) -> None:
    """End to end: 7 raws under one plan call's byte budget still plan as 3
    batches, not 1."""
    for i in range(7):
        _write_raw(root, "proj", f"r{i}.md", body="tiny")
    brain = FakeBrain({"upserts": [], "tombstones": []})
    summary = engine.curate(root, "proj", brain, plan_max_raws=3)
    assert summary["batches"] == 3
    assert brain.plan_calls == 3


# --- resynth cost record --------------------------------------------------


def test_resynth_summary_carries_budget_fields(root: Path) -> None:
    """A resynth makes exactly one brain call; its cost must reach the journal
    like every other pass's does."""
    store.ensure_tree("proj", root)
    # synthesis short-circuits on an empty fact set and makes no call at all,
    # so the fixture needs a fact for there to be a cost to record
    store.upsert_curated(root, "proj", "fact-a", "body", provenance=[])
    summary = engine.curate(root, "proj", FakeBrain(), resynth=True)
    assert summary["status"] == "resynth"
    assert summary["budget_calls"] == 1
    assert _read_log(root, "proj")[-1]["budget_calls"] == 1


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
            {
                "slug": "use-pip",
                "body": "Use pip.",
                "supersedes": ["prefer-uv"],
                "from_raw": ["r1.md"],
            }
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


# --- provenance hardening: from_raw validation + fold journal (ADR-0022) --


def test_hallucinated_from_raw_dropped_and_counted(root: Path) -> None:
    """B1: a `from_raw` filename not shown to the model this batch is dropped
    before it reaches the permanent provenance record, and counted."""
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {
                "slug": "fact-a",
                "body": "durable",
                # r1.md is real (in the batch); the others are hallucinations —
                # e.g. echoes of old raw basenames in a fact-body lineage block.
                "from_raw": ["r1.md", "ghost-1.md", "ghost-2.md"],
            }
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    doc = store.read_doc(store.memory_dir("proj", root) / "curated" / "fact-a.md")
    assert doc["provenance"] == ["raw/r1.md"]  # only the validated name survived
    assert summary["dropped_from_raw"] == 2

    # Codex P2-TEST-GAP-001: the journal is a SECOND permanent consumer of the
    # same validation, and the assertions above do not protect it. Validating
    # only for `provenance` while accumulating the raw `from_raw` into
    # `_ApplyResult.edges` would keep every assertion above green and still put
    # ghost filenames into the map Slice A reads back after raw retention
    # expires. The fold-edge test elsewhere cannot catch it either — it only
    # ever passes a valid filename, so validated and unvalidated agree there.
    (record,) = _read_log(root, "proj")
    assert record["fold"]["edges"] == {"fact-a": ["r1.md"]}
    # Belt-and-braces: no ghost anywhere in the edge map, under any slug.
    all_edge_names = {name for names in record["fold"]["edges"].values() for name in names}
    assert all_edge_names == {"r1.md"}


def test_summary_key_set_is_exact_and_carries_no_fold(root: Path) -> None:
    """B2: `fold` must never join the returned/printed summary (it carries raw
    session ids). The summary shape is pinned so any drift fails loudly."""
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "f", "body": "b", "from_raw": ["r1.md"]}], "tombstones": []}
    summary = engine.curate(root, "proj", FakeBrain(plan))
    assert summary["status"] == "ok"
    assert summary["node_refreshed"] is True
    assert set(summary) == {
        "status",
        # Journals whether synthesis left the node current. `status` cannot carry
        # this: `error` is logged both when the *plan* fails after synthesis has
        # already refreshed the node, and for a failed `--dry-run` that never
        # attempts synthesis. A consumer reading only `status` called a refreshed
        # node frozen (review round 2, F3 on the doctor health check).
        "node_refreshed",
        "raw",
        "batches",
        "distilled",
        "fallback",
        "upserts",
        "superseded",
        "tombstones",
        "dropped_from_raw",
        "pruned_tombstones",
        "active_facts",
        # upstream's pass budget (P0, 2026-07-17 runaway) adds these three to
        # every summary; pinned here too so drift in either feature fails loudly.
        "backlog",
        "budget_calls",
        "budget_deferred_raws",
    }
    assert "fold" not in summary


def test_fold_records_consumed_edges_and_session_identity(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "fact-a", "body": "b", "from_raw": ["r1.md"]}], "tombstones": []}
    engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    assert "fold" in record  # rides the journal record, not the summary
    fold = record["fold"]
    assert fold["v"] == 1
    assert fold["edges"] == {"fact-a": ["r1.md"]}
    assert len(fold["consumed"]) == 1
    entry = fold["consumed"][0]
    assert entry["file"] == "r1.md"
    assert entry["session_id"] == "s1"  # session identity captured at consumption
    assert entry["agent"] == "claude"
    assert entry["captured_at"] == "2026-07-07T12:00:00Z"


def test_fold_absent_on_noop(root: Path) -> None:
    store.ensure_tree("proj", root)
    engine.curate(root, "proj", FakeBrain())
    (record,) = _read_log(root, "proj")
    assert record["status"] == "noop"
    assert "fold" not in record  # nothing committed


def test_fold_absent_on_first_batch_abort(root: Path) -> None:
    _write_raw(root, "proj", "r1.md")
    engine.curate(root, "proj", FakeBrain(BrainError("unparseable")))
    (record,) = _read_log(root, "proj")
    assert record["status"] == "error"
    assert "fold" not in record  # first-batch abort committed nothing


def test_fold_present_on_empty_plan_with_empty_edges(root: Path) -> None:
    """A valid-but-empty plan consumes the raw but attributes it to no fact —
    the fold records the consumed session with an empty edge set (an orphan)."""
    _write_raw(root, "proj", "r1.md")
    engine.curate(root, "proj", FakeBrain({"upserts": [], "tombstones": []}))
    (record,) = _read_log(root, "proj")
    assert record["status"] == "ok"
    fold = record["fold"]
    assert fold["edges"] == {}
    assert [c["file"] for c in fold["consumed"]] == ["r1.md"]


def test_fold_superseded_records_only_applied_soft_deletes(root: Path) -> None:
    """Adversarial: a superseded slug that is re-upserted this pass is NOT
    tombstoned, so it must NOT appear in the fold's `superseded` list."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "old-fact", "old")
    store.upsert_curated(root, "proj", "fact-x", "v1")
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            # fact-x is re-upserted this pass AND superseded by fact-y → survives.
            {"slug": "fact-x", "body": "v2", "from_raw": ["r1.md"]},
            {
                "slug": "fact-y",
                "body": "y",
                "supersedes": ["fact-x", "old-fact"],
                "from_raw": ["r1.md"],
            },
        ],
        "tombstones": [],
    }
    engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    # Only old-fact's soft-delete actually applied.
    assert record["fold"]["superseded"] == [{"slug": "old-fact", "by": "fact-y"}]
    assert (store.memory_dir("proj", root) / "curated" / "fact-x.md").exists()


def test_fold_tombstoned_records_explicit_removals(root: Path) -> None:
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "stale", "old")
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [], "tombstones": [{"slug": "stale", "reason": "gone"}]}
    engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    assert record["fold"]["tombstoned"] == ["stale"]


# Codex P2-TEST-GAP-002: the two tests above prove the SKIP guards (re-upserted /
# pinned), which return before `_safe_soft_delete` is ever called. Neither
# reaches the case where the call is made and FAILS. That leaves the false
# branches of both `if _safe_soft_delete(...)` uncovered, so moving the fold
# append and the counter increment outside the successful branch would journal —
# and count — a removal that never happened, with every existing test still
# green. A model naming a slug that does not exist is a routine fail-soft path,
# not a hostile-tree hypothetical: the plan is LLM output.


def test_fold_superseded_omits_a_target_that_does_not_exist(root: Path) -> None:
    """`supersedes` names a slug with no curated file — the soft-delete fails,
    so nothing may be journalled or counted."""
    store.ensure_tree("proj", root)
    _write_raw(root, "proj", "r1.md")
    plan = {
        "upserts": [
            {
                "slug": "fact-y",
                "body": "y",
                "supersedes": ["never-existed"],
                "from_raw": ["r1.md"],
            }
        ],
        "tombstones": [],
    }
    summary = engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    assert record["fold"]["superseded"] == []
    assert summary["superseded"] == 0
    assert summary["upserts"] == 1  # the upsert itself still landed


def test_fold_tombstoned_omits_a_target_that_does_not_exist(root: Path) -> None:
    """Same probe on the explicit-tombstone path (spec §2 step 5)."""
    store.ensure_tree("proj", root)
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [], "tombstones": [{"slug": "never-existed", "reason": "gone"}]}
    summary = engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    assert record["fold"]["tombstoned"] == []
    assert summary["tombstones"] == 0


def test_fold_tombstoned_omits_an_invalid_slug(root: Path) -> None:
    """A malformed slug cannot resolve to a path at all — same contract: the
    pass fails soft and journals nothing."""
    store.ensure_tree("proj", root)
    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [], "tombstones": [{"slug": "Not A Slug!", "reason": "gone"}]}
    summary = engine.curate(root, "proj", FakeBrain(plan))
    (record,) = _read_log(root, "proj")
    assert record["fold"]["tombstoned"] == []
    assert summary["tombstones"] == 0


def test_fold_rides_after_commit_error_and_records_only_committed(root: Path) -> None:
    """A later batch failing (status `error`) must not erase the durable
    identity of raws already consumed this pass — the fold is still written and
    reflects only the committed batch."""
    _write_raw(root, "proj", "r1.md", "a" * 80)
    _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = SequencedBrain(
        [
            {
                "upserts": [{"slug": "landed", "body": "durable", "from_raw": ["r1.md"]}],
                "tombstones": [],
            },
            BrainError("second batch fails"),
        ]
    )
    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    assert summary["status"] == "error"
    (record,) = _read_log(root, "proj")
    fold = record["fold"]  # present despite the error status
    assert fold["edges"] == {"landed": ["r1.md"]}
    assert [c["file"] for c in fold["consumed"]] == ["r1.md"]  # only the committed batch
    assert store.read_doc(store.memory_dir("proj", root) / "raw" / "r2.md")["consumed"] is False


def _two_batch_brain(batch1: dict[str, Any], batch2: dict[str, Any]) -> SequencedBrain:
    return SequencedBrain([batch1, batch2])


def test_fold_superseded_reconciled_when_a_later_batch_reupserts_the_slug(root: Path) -> None:
    """P1-DATA-INTEGRITY-001 (cross-batch, D22): batch 1 supersedes `old-fact`;
    a later batch re-upserts it. At pass end `old-fact` is active, so it must not
    be journaled as removed and must not be counted as superseded. Its batch-1
    tombstone deliberately REMAINS on disk — §1 forbids unowned deletion of
    `.tombstones/<slug>` — and readers resolve the pair toward `curated/`."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "old-fact", "v1")
    _write_raw(root, "proj", "r1.md", "a" * 80)
    _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])  # one raw per batch
    brain = _two_batch_brain(
        {
            "upserts": [
                {
                    "slug": "new-fact",
                    "body": "n",
                    "supersedes": ["old-fact"],
                    "from_raw": ["r1.md"],
                }
            ],
            "tombstones": [],
        },
        {"upserts": [{"slug": "old-fact", "body": "resurrected", "from_raw": ["r2.md"]}]},
    )
    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    assert summary["batches"] == 2
    mem = store.memory_dir("proj", root)
    assert (mem / "curated" / "old-fact.md").exists()  # active again
    (record,) = _read_log(root, "proj")
    assert record["fold"]["superseded"] == []  # not journaled as removed
    assert summary["superseded"] == 0  # count stays consistent with the fold list
    # The batch-1 tombstone stays on disk as documented residue (deleting it is
    # not concurrency-safe, §1); readers resolve it toward curated/.
    assert [d["name"] for d in store.list_curated(root, "proj")] == ["new-fact", "old-fact"]


def test_fold_tombstoned_reconciled_when_a_later_batch_reupserts_the_slug(root: Path) -> None:
    """Same class as P1-DATA-INTEGRITY-001 for explicit tombstones: batch 1
    tombstones `stale`; a later batch re-upserts it. Found while fixing the
    superseded case — the two fold-removal lists share the reconciliation."""
    store.ensure_tree("proj", root)
    store.upsert_curated(root, "proj", "stale", "v1")
    _write_raw(root, "proj", "r1.md", "a" * 80)
    _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = _two_batch_brain(
        {"upserts": [], "tombstones": [{"slug": "stale", "reason": "gone"}]},
        {"upserts": [{"slug": "stale", "body": "back", "from_raw": ["r2.md"]}]},
    )
    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    assert summary["batches"] == 2
    mem = store.memory_dir("proj", root)
    assert (mem / "curated" / "stale.md").exists()
    (record,) = _read_log(root, "proj")
    assert record["fold"]["tombstoned"] == []
    assert summary["tombstones"] == 0


def test_fold_superseded_kept_when_slug_stays_removed_across_batches(root: Path) -> None:
    """Guard the reconciliation's other direction: a slug upserted in batch 1 and
    genuinely superseded in batch 2 is NOT active at pass end, so it MUST remain
    journaled as removed (the active-set oracle must not over-drop)."""
    _write_raw(root, "proj", "r1.md", "a" * 80)
    _write_raw(root, "proj", "r2.md", "b" * 80)
    budget = _request_size_for(root, ["r1.md"])
    brain = _two_batch_brain(
        {"upserts": [{"slug": "doomed", "body": "v1", "from_raw": ["r1.md"]}], "tombstones": []},
        {
            "upserts": [
                {"slug": "winner", "body": "v2", "supersedes": ["doomed"], "from_raw": ["r2.md"]}
            ],
            "tombstones": [],
        },
    )
    summary = engine.curate(root, "proj", brain, plan_payload_max_bytes=budget)
    assert summary["batches"] == 2
    mem = store.memory_dir("proj", root)
    assert not (mem / "curated" / "doomed.md").exists()  # genuinely removed
    (record,) = _read_log(root, "proj")
    assert record["fold"]["superseded"] == [{"slug": "doomed", "by": "winner"}]
    assert summary["superseded"] == 1


def test_cross_pass_resurrection_leaves_residue_that_readers_resolve(root: Path) -> None:
    """A slug tombstoned in one pass and re-derived under its stable slug in a
    later pass leaves the old tombstone on disk — the curator deliberately does
    NOT delete it (that cannot be made concurrency-safe, §1 / G4). What must hold
    is that no reader is misled and the pass's fold is honest."""
    store.ensure_tree("proj", root)
    mem = store.memory_dir("proj", root)
    store.upsert_curated(root, "proj", "topic-x", "v1")

    # Pass 1: tombstone topic-x.
    _write_raw(root, "proj", "r1.md")
    engine.curate(
        root,
        "proj",
        FakeBrain({"upserts": [], "tombstones": [{"slug": "topic-x", "reason": "stale"}]}),
    )
    assert (mem / ".tombstones" / "topic-x.md").exists()
    assert not (mem / "curated" / "topic-x.md").exists()

    # Pass 2 (a separate curate pass): the model re-derives the same stable slug.
    _write_raw(root, "proj", "r2.md")
    engine.curate(
        root,
        "proj",
        FakeBrain({"upserts": [{"slug": "topic-x", "body": "reborn", "from_raw": ["r2.md"]}]}),
    )

    assert (mem / "curated" / "topic-x.md").exists()
    assert (mem / ".tombstones" / "topic-x.md").exists()  # documented residue
    # Resolution rule: curated/ is authoritative, so the fact reads as active once.
    assert [d["name"] for d in store.list_curated(root, "proj")] == ["topic-x"]
    body = store.read_doc(mem / "curated" / "topic-x.md").body
    assert body.split("\n")[0].startswith("reborn")  # linkify appends a lineage footer
    # Pass 2's fold is honest: it removed nothing, so both removal lists are empty.
    pass1, pass2 = _read_log(root, "proj")
    assert pass1["fold"]["tombstoned"] == ["topic-x"]
    assert pass2["fold"]["tombstoned"] == []
    assert pass2["fold"]["superseded"] == []
    assert pass2["fold"]["edges"] == {"topic-x": ["r2.md"]}


def test_curate_never_deletes_a_tombstone_beside_an_active_fact(root: Path) -> None:
    """Regression for P1-DATA-INTEGRITY-003: an active+tombstoned pair is
    indistinguishable from the midpoint of an in-flight soft_delete_curated, so
    NO curate path may delete it. A pass must leave such a pair alone."""
    store.ensure_tree("proj", root)
    mem = store.memory_dir("proj", root)
    store.upsert_curated(root, "proj", "topic-x", "active body")
    tomb = mem / ".tombstones" / "topic-x.md"
    # Fresh timestamp: this is the shape of an IN-FLIGHT soft-delete, which is
    # exactly what no curate path may delete. (Prune skips it here because it is
    # inside the grace window — but prune's own TOCTOU is a separate, recorded
    # gap, see known-gaps G4; it is not what makes this safe.)
    fresh = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    store.write_doc(
        tomb,
        {"name": "topic-x", "status": "tombstoned", "tombstoned_at": fresh},
        "in-flight or stale copy",
    )

    # A normal pass, a noop pass, and a dry run must all leave the pair intact.
    _write_raw(root, "proj", "r1.md")
    engine.curate(root, "proj", FakeBrain({"upserts": [], "tombstones": []}))
    assert tomb.exists() and (mem / "curated" / "topic-x.md").exists()

    assert engine.curate(root, "proj", FakeBrain())["status"] == "noop"
    assert tomb.exists() and (mem / "curated" / "topic-x.md").exists()

    _write_raw(root, "proj", "r2.md")
    engine.curate(root, "proj", FakeBrain({"upserts": [], "tombstones": []}), dry_run=True)
    assert tomb.exists() and (mem / "curated" / "topic-x.md").exists()

    # And the reader is never misled while the pair persists.
    assert [d["name"] for d in store.list_curated(root, "proj")] == ["topic-x"]


# --- G5: the untrusted-data fence on the curator's own prompts ---------------
#
# `curate` was dead in the water on 2026-07-29: the brain REFUSED every plan
# call, citing this project's own curated runaway-incident facts back at it
# ("This is the curator-prompt-replay misfire..."). Cause: `_plan_user_payload`
# ships every curated fact in every plan request, and PLAN_SYSTEM — unlike
# DISTILL_SYSTEM and MERGE_SYSTEM — had no fence marking that payload as data.
# The write-ups of a past incident became an argument against running the prompt
# they were inside. Full evidence in `docs/known-gaps.md` G5.
#
# A unit test cannot prove a real model complies; only a live pass does that.
# What these pin is the part that *can* regress silently: that the fence still
# exists, and that it still covers the payload actually sent.


def test_plan_fence_covers_every_key_the_payload_actually_sends() -> None:
    """The fence names payload keys, so a new key must be added to it too.

    This is the assertion with teeth: adding a third top-level key to
    `_plan_user_payload` while leaving PLAN_SYSTEM alone would ship an unfenced
    data region and silently reopen G5. Derived from the real payload rather
    than hard-coded, so it tracks the serializer.
    """
    payload = json.loads(engine._plan_user_payload([], [{"raw": "r.md", "body": "b"}]))

    assert set(payload) == {"curated_facts", "raw_captures"}, (
        "plan payload keys changed; the PLAN_SYSTEM fence must name the new key"
    )
    for key in payload:
        assert f'"{key}"' in engine.PLAN_SYSTEM, f"payload key {key!r} is not named in the fence"


@pytest.mark.parametrize("prompt", ["PLAN_SYSTEM", "NODE_SYSTEM"])
def test_curator_prompts_keep_their_untrusted_data_fence(prompt: str) -> None:
    """Removal canary for the fence, on both prompts that take curated facts.

    NODE_SYSTEM is included because synthesis reads the same facts through the
    same brain, so it carries the identical exposure — G5 was only *observed* on
    the plan call because planning runs first.
    """
    text = getattr(engine, prompt)

    assert "UNTRUSTED DATA" in text
    assert "never instructions" in text
    # The refusal itself is the failure mode, so the prompt must say so.
    assert "fails the pass" in text
    # And the specific trap: facts describing past curator incidents are data,
    # not evidence that *this* call is one of them. (Substring only — PLAN_SYSTEM
    # emphasises it as "NOT grounds to decline", NODE_SYSTEM does not.)
    assert "grounds to decline" in text


def test_brain_usage_rides_the_journal_and_never_the_summary(root: Path) -> None:
    """Same split `fold` and `distill_detail` use. The summary's key set is pinned
    (see `test_summary_key_set_is_exact_and_carries_no_fold`), so token accounting
    must not grow it — and the journal is where per-pass detail already lives."""
    from neurobase.brain.base import BrainUsage

    class UsageBrain(FakeBrain):
        name = "usage-fake"

        def __init__(self, plan: dict) -> None:
            super().__init__(plan)
            self.usage = BrainUsage()

        def plan_json(self, system: str, user: str) -> dict:
            self.usage.record(
                {
                    "usage": {"input_tokens": 40, "output_tokens": 9},
                    "total_cost_usd": 0.02,
                    "modelUsage": {"claude-opus-5[1m]": {}},
                }
            )
            return super().plan_json(system, user)

    _write_raw(root, "proj", "r1.md")
    plan = {"upserts": [{"slug": "f", "body": "b", "from_raw": ["r1.md"]}], "tombstones": []}

    summary = engine.curate(root, "proj", UsageBrain(plan))

    assert "brain_usage" not in summary  # pinned key set is untouched
    record = _read_log(root, "proj")[-1]
    usage = record["brain_usage"]
    assert usage["input_tokens"] == 40
    assert usage["models"] == {"claude-opus-5[1m]": 1}


# --- G13: a dry run must not journal the noop branch -----------------------


def test_dry_run_on_empty_backlog_writes_no_log_record(root: Path) -> None:
    """G13. The noop return precedes every other `dry_run` read, so before the
    guard a PREVIEW appended an unmarked `noop` record — indistinguishable from a
    real pass, and enough to retire doctor's "no curate passes recorded yet" state
    permanently. A dry run journals only its failures, and marks them."""
    store.ensure_tree("proj", root)
    summary = engine.curate(root, "proj", FakeBrain(), dry_run=True)
    assert summary["status"] == "noop"
    assert not (store.memory_dir("proj", root) / engine.CURATOR_LOG).exists()


def test_real_noop_pass_still_journals(root: Path) -> None:
    """The other half of G13's fix: only the PREVIEW stops writing. A real no-op
    pass is the cadence evidence that a hook fired and found nothing to fold."""
    store.ensure_tree("proj", root)
    engine.curate(root, "proj", FakeBrain())
    (record,) = _read_log(root, "proj")
    assert record["status"] == "noop"
    assert record.get("dry_run") is not True


def test_dry_run_noop_leaves_doctor_never_curated_state_intact(root: Path) -> None:
    """The consequence that made G13 worth fixing, pinned directly: the log must
    stay ABSENT, because `no curate passes recorded yet` is reported only while it
    is missing or empty — a state a preview must not be able to consume."""
    store.ensure_tree("proj", root)
    for _ in range(3):
        engine.curate(root, "proj", FakeBrain(), dry_run=True)
    assert not (store.memory_dir("proj", root) / engine.CURATOR_LOG).exists()


# --- ADR-0027 D47: `authority` is provenance, never a grant of trust -------


def _write_raw_with_authority(
    root: Path, project: str, name: str, *, authority: str | None, body: str = "authored body"
) -> Path:
    """Two raws that differ in EXACTLY one frontmatter key. Written through the
    real writer, so a change to how `authority` is persisted is picked up here
    rather than silently diverging from a hand-built fixture."""
    store.ensure_tree(project, root)
    return store.write_raw(
        root,
        project,
        agent="claude",
        session_id="s1",
        cwd="/x",
        branch="main",
        captured_at=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
        body=body,
        authority=authority,
        unique=True,
    )


def test_authority_absent_and_agent_authored_produce_identical_plan_payloads() -> None:
    """D47 requirement 3, the core of it: two raws identical but for `authority`
    MUST produce byte-identical plan payloads. The seam that makes this true is
    `_raw_payload`, which projects a raw to `{raw, body}` and passes NO
    frontmatter — this test is what turns removing that seam into a visible
    change rather than a silent one."""
    import tempfile

    payloads = []
    for authority in (None, "agent-authored"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            path = _write_raw_with_authority(root, "proj", "r.md", authority=authority)
            doc = store.read_doc(path)
            payloads.append(engine._raw_payload(doc))

    assert payloads[0] == payloads[1]
    assert "authority" not in json.dumps(payloads[1])


def test_authority_does_not_reach_the_serialized_plan_user_payload() -> None:
    """The same guarantee one layer up, at the string actually sent to the brain —
    `_raw_payload` could be correct while the payload assembler added frontmatter
    of its own. Checking only the projection would not have caught that."""
    import tempfile

    rendered = []
    for authority in (None, "agent-authored"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            path = _write_raw_with_authority(root, "proj", "r.md", authority=authority)
            doc = store.read_doc(path)
            rendered.append(engine._plan_user_payload([], [engine._raw_payload(doc)]))

    assert rendered[0] == rendered[1]
    assert "agent-authored" not in rendered[1]


def test_authority_does_not_change_selection_or_ordering(root: Path) -> None:
    """D47's 'identical selection' half. An authored raw and a captured one written
    at the same instant must be selected and ordered identically — `authority` must
    not become a tiebreak, a filter, or a weight."""
    store.ensure_tree("proj", root)
    a = _write_raw_with_authority(root, "proj", "a.md", authority=None, body="body one")
    b = _write_raw_with_authority(root, "proj", "b.md", authority="agent-authored", body="body two")

    docs = store.list_raw(root, "proj", unconsumed_only=True)

    assert [d.file_path.name for d in docs] == sorted([a.name, b.name])
    assert len(docs) == 2


def test_a_v1_authored_raw_takes_no_brain_call_to_distill(root: Path) -> None:
    """The cost mechanism the whole shape rests on: an authored raw carries no
    `transcript_path`, so it is a v1 raw and `_distill_one` returns before any
    cache, render or brain call. This is what makes `wrap` cheaper than distill —
    pinned here so a change to that early return shows up as a failing test."""
    store.ensure_tree("proj", root)
    _write_raw_with_authority(root, "proj", "a.md", authority="agent-authored", body="authored")
    brain = FakeBrain()

    summary = engine.curate(root, "proj", brain)

    assert summary["status"] == "ok"
    assert summary["distilled"] == 0
    # `distilled == 0` alone would be weaker than it looks — a v2 raw whose transcript
    # vanished also distils nothing. The REASON code is what pins the no-brain early
    # return at `distill.py:321-324`, and it rides the journal's `distill_detail`
    # rather than the summary's deliberately pinned key set.
    (record,) = _read_log(root, "proj")
    assert record["distill_detail"]["fallback_no_pointer"] == 1
