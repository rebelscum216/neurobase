"""CLI integration tests for Phase 8 Workstream F (spec §12.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli_module
from neurobase.brain.select import BrainResolution
from neurobase.cli import app
from neurobase.recommender import proposals
from neurobase.recommender.corpus import Corpus, EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores

runner = CliRunner()


def _ranked(slug: str = "prefer-uv-run", draft: str = "Always use uv run.") -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="rule",
        candidate_type="repeated-instruction",
        title="Prefer uv run",
        rationale="Repeated correction",
        draft=draft,
        target="AGENTS.md",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("old-example")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def test_list_and_show_on_empty_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    listed = runner.invoke(app, ["recommend", "list", "--root", str(root)])
    shown = runner.invoke(app, ["recommend", "show", "missing", "--root", str(root)])
    assert listed.exit_code == 0
    assert listed.output == ""
    assert shown.exit_code == 1
    assert "not found or malformed" in shown.output


def test_dry_run_prints_candidates_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    fake_brain = object()
    monkeypatch.setattr(
        cli_module,
        "resolve_brain",
        lambda config: (fake_brain, BrainResolution("fake", True, "test")),
    )
    monkeypatch.setattr(cli_module.miner, "mine", lambda *args, **kwargs: [{}])
    monkeypatch.setattr(cli_module.recommend_corpus, "load_corpus", lambda *a, **k: Corpus())
    monkeypatch.setattr(cli_module.ranker, "rank", lambda *args, **kwargs: [_ranked()])

    result = runner.invoke(app, ["recommend", "run", "--dry-run", "--root", str(root)])

    assert result.exit_code == 0
    assert "prefer-uv-run" in result.output
    assert not (root / "proposals").exists()


def test_edit_updates_only_draft_redacts_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=None)
    secret = "AKIAIOSFODNN7EXAMPLE"
    monkeypatch.setattr(cli_module.click, "edit", lambda *a, **k: f"Revised {secret}")

    result = runner.invoke(app, ["recommend", "edit", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 0
    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None
    assert proposals.extract_draft(doc.body) == "Revised [REDACTED:aws-key]"
    assert "**Rationale:** Repeated correction" in doc.body
    history = proposals.ledger_history(root, "prefer-uv-run")
    assert [event["event"] for event in history].count("edited") == 1


def test_reject_updates_proposal_and_ledger(tmp_path: Path) -> None:
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])

    result = runner.invoke(
        app,
        ["recommend", "reject", "prefer-uv-run", "--reason", "too narrow", "--root", str(root)],
    )

    assert result.exit_code == 0
    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None and doc.get("status") == "rejected"
    assert proposals.ledger_history(root, "prefer-uv-run")[-1]["reason"] == "too narrow"


def test_reject_decided_proposal_is_hard_error(tmp_path: Path) -> None:
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])
    proposals.reject_proposal(root, "prefer-uv-run")

    result = runner.invoke(app, ["recommend", "reject", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "status is rejected" in result.output
