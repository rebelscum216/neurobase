"""The recommender *in the loop* (coverage report Gap 4, edge 2).

``test_cross_agent.py`` carries a store from capture through curate to recall and
stops there. Every recommender stage — corpus → miner → ranker → proposal store →
emitter — has its own unit tests, but the wiring *between* them was only ever
exercised piecewise. A mis-wire between two adjacent stages would pass all of
them: the ranker scoring against a corpus the miner never read, the proposal
store persisting the miner's self-reported counts instead of the ranker's
recomputed ones, or the emitter splicing a body whose draft markers the proposal
renderer had moved.

So this test starts where ``test_cross_agent`` ends — a store that a real
``engine.curate`` pass has just written facts into — and drives it the rest of
the way through the shipped CLI (``recommend run`` → ``show`` → ``accept``).

It deliberately asserts numbers that can only be right if data flowed through
*every* stage rather than exit code 0:

- ``breadth == 6`` is ``sessions × agents × projects`` recomputed from the
  ``agent``/``session_id`` frontmatter of the raw files that the curator's
  ``provenance`` points back to. Getting it requires corpus → miner → ranker to
  agree on the same store, and the ranker to walk curated → provenance → raw.
- The miner's self-reported counts are absurd on purpose; if any of them survive
  into the proposal, the §12.6 "recompute strictly from evidence" contract is
  broken somewhere in the handoff.
- The installed ``AGENTS.md`` carries the exact draft the miner proposed, inside
  the rule markers — the last hop, proposal body → emitter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli_module
from neurobase.brain.select import BrainResolution
from neurobase.cli import app
from neurobase.core import projects, store
from neurobase.core.config import Config
from neurobase.curator import engine
from neurobase.recommender import proposals

runner = CliRunner()

PROJECT = "myrepo"
DRAFT = "Always run `uv run pytest -q` before pushing."


class FakeCuratorBrain:
    """The curator's brain, as ``test_cross_agent.py`` uses it: a fixed plan and
    a fixed node body, so the curate pass is real but deterministic."""

    name = "fake"

    def __init__(self, plan: dict[str, Any], node_text: str) -> None:
        self._plan = plan
        self._node_text = node_text

    def plan_json(self, system: str, user: str) -> dict:
        return self._plan

    def text(self, system: str, user: str) -> str:
        return self._node_text


class FakeMinerBrain:
    """The miner's brain. Records the payload the *real* miner built from the
    *real* corpus (so the test can prove curate's output reached the prompt), and
    answers with one candidate whose evidence names store objects the curate pass
    actually produced.

    Its self-reported ``occurrences``/``agents``/``projects`` are nonsense on
    purpose: §12.5 calls them advisory display text and §12.6 makes the ranker
    recompute from ``evidence``. If any of them reach the written proposal, the
    miner→ranker handoff is trusting the model's arithmetic."""

    name = "fake"

    def __init__(self, candidate: dict[str, Any]) -> None:
        self._candidate = candidate
        self.payloads: list[str] = []

    def plan_json(self, system: str, user: str) -> dict:
        self.payloads.append(user)
        return {"candidates": [self._candidate]}


def test_curated_store_flows_through_recommend_run_to_an_installed_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One curated store, carried end to end: curate → corpus → mine → rank →
    proposal → emit, through the CLI the user actually runs."""
    root = tmp_path / "store"
    repo = tmp_path / PROJECT
    repo.mkdir()
    projects.register_project(root, repo, slug=PROJECT)
    store.ensure_tree(PROJECT, root)

    # Pin the store's own config so the developer's ~/.config/neurobase/config.toml
    # can never move the ranker's gates out from under the assertions below.
    monkeypatch.setattr(cli_module, "load_config", lambda: Config())

    # 1) Three raw captures, written with pinned metadata rather than through the
    #    scribes (test_cross_agent owns that half). Two agents, three sessions —
    #    that is what the ranker must rediscover on its own, via provenance.
    #    Timestamps are relative to now so they never age out of the corpus
    #    loader's 30-day lookback window, and their order is fixed by the offsets.
    now = datetime.now(UTC)
    raw_paths = [
        store.write_raw(
            root,
            PROJECT,
            agent=agent,
            session_id=session_id,
            cwd=str(repo),
            branch="main",
            captured_at=now - timedelta(days=days_ago),
            body=body,
        )
        for agent, session_id, days_ago, body in [
            ("claude", "sess-a", 3, "User asked again to run the tests before pushing."),
            ("codex", "sess-b", 2, "Reminded twice: `uv run pytest -q` before every push."),
            ("claude", "sess-c", 1, "Pushed without testing; user corrected it once more."),
        ]
    ]
    file_a, file_b, file_c = (path.name for path in raw_paths)

    # 2) A real curate pass folds them into two facts. `test-before-push` draws
    #    on two raws (two sessions, two agents); `pytest-command` on the third.
    curate_plan = {
        "upserts": [
            {
                "slug": "test-before-push",
                "body": "Run the test suite before pushing.",
                "supersedes": [],
                "from_raw": [file_a, file_b],
            },
            {
                "slug": "pytest-command",
                "body": "The test command is `uv run pytest -q`.",
                "supersedes": [],
                "from_raw": [file_c],
            },
        ],
        "tombstones": [],
    }
    summary = engine.curate(
        root, PROJECT, FakeCuratorBrain(curate_plan, "# Status\n\nTest before pushing.")
    )
    assert summary["upserts"] == 2

    # 3) The miner proposes a rule from those facts. Evidence spans both curated
    #    facts plus one raw directly — three refs, the §12.6 recurrence gate.
    candidate = {
        "slug": "test-before-push-rule",
        "type": "rule",
        "candidate_type": "repeated-instruction",
        "title": "Test before pushing",
        "rationale": "Corrected across several sessions and both agents.",
        "draft": DRAFT,
        "target": "AGENTS.md",
        "evidence": [
            {"kind": "curated", "project": PROJECT, "slug": "test-before-push"},
            {"kind": "curated", "project": PROJECT, "slug": "pytest-command"},
            {"kind": "raw", "project": PROJECT, "file": file_c},
        ],
        # Advisory nonsense — the ranker must ignore all three (§12.5/§12.6).
        "occurrences": 99,
        "agents": ["everyone"],
        "projects": ["nowhere"],
        "supersedes": [],
    }
    brain = FakeMinerBrain(candidate)
    monkeypatch.setattr(
        cli_module, "resolve_brain", lambda config: (brain, BrainResolution("fake", True, "test"))
    )

    result = runner.invoke(app, ["recommend", "run", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip())["created"] == ["test-before-push-rule"]

    # curate → corpus → miner: the freshly curated fact bodies reached the prompt
    # the real miner built, so the miner is reading the store curate just wrote.
    assert len(brain.payloads) == 1
    payload = json.loads(brain.payloads[0])
    assert {fact["slug"] for fact in payload["curated_facts"]} == {
        "test-before-push",
        "pytest-command",
    }
    payload_bodies = "\n".join(fact["body"] for fact in payload["curated_facts"])
    assert "The test command is `uv run pytest -q`." in payload_bodies

    # 4) miner → ranker → proposal store: every count recomputed from evidence.
    doc = proposals.load_proposal(root, "test-before-push-rule")
    assert doc is not None
    scores = doc.get("scores")
    assert isinstance(scores, dict)
    # recurrence = len(evidence); breadth = sessions × agents × projects, where
    # sessions/agents are reachable ONLY by resolving the two curated refs
    # through the provenance curate wrote back to raw files, then reading those
    # files' frontmatter. 3 sessions × 2 agents × 1 project.
    assert scores["recurrence"] == 3
    assert scores["breadth"] == 6
    assert 0.0 < scores["recency"] <= 1.0
    assert scores["total"] == pytest.approx(round(3 * 6 * scores["recency"], 4))
    # The miner's self-reported counts did not survive anywhere.
    assert doc.get("project") == PROJECT  # derived from evidence, not "nowhere"
    assert "99" not in doc.body
    assert (
        "recurred 3× across 3 session(s), 2 agent(s), 1 project(s) "
        f"(project `{PROJECT}`)" in doc.body
    )
    assert proposals.extract_draft(doc.body) == DRAFT

    # 5) `recommend show`: every persisted evidence ref resolves against the real
    #    store — the refs were written in a shape the resolver accepts.
    shown = runner.invoke(app, ["recommend", "show", "test-before-push-rule", "--root", str(root)])
    assert shown.exit_code == 0, shown.output
    assert shown.output.count("[resolved]") == 3
    assert "[unresolved]" not in shown.output

    # 6) proposal → emitter: accept installs the draft into the project's
    #    AGENTS.md, inside the managed rule markers.
    accepted = runner.invoke(
        app, ["recommend", "accept", "test-before-push-rule", "--root", str(root), "--yes"]
    )
    assert accepted.exit_code == 0, accepted.output

    agents_md = repo / "AGENTS.md"
    installed = agents_md.read_text(encoding="utf-8")
    assert "<!-- neurobase:rule:test-before-push-rule" in installed
    assert "<!-- /neurobase:rule:test-before-push-rule -->" in installed
    assert DRAFT in installed

    # The ledger and frontmatter agree on where the artifact landed.
    final = proposals.load_proposal(root, "test-before-push-rule")
    assert final is not None
    assert final.get("status") == "accepted"
    assert final.get("installed_path") == str(agents_md)
    history = proposals.ledger_history(root, "test-before-push-rule")
    assert [event["event"] for event in history] == ["proposed", "accepted"]
    assert history[-1]["target"] == "AGENTS.md"
