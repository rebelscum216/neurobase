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


def test_accept_rejected_proposal_errors_before_any_write(tmp_path: Path) -> None:
    """F1 (§12.7): accept on a rejected proposal is a hard error that renders,
    backs up, and writes NOTHING — the status guard must fire before the artifact
    could ever reach disk."""
    root = tmp_path / "store"
    from neurobase.core import projects

    repo = tmp_path / "repo"
    repo.mkdir()
    projects.register_project(root, repo, slug="neurobase")
    proposals.write_ranked(root, [_ranked()])
    proposals.reject_proposal(root, "prefer-uv-run")

    result = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )

    assert result.exit_code == 1
    assert "status is rejected" in result.output
    assert not (repo / "AGENTS.md").exists()  # no artifact written
    assert not (root / "backups").exists()  # no backup taken


def test_show_on_parseable_but_malformed_proposal_is_fail_soft(tmp_path: Path) -> None:
    """F2: a proposal whose frontmatter parses but violates the §12.1 schema
    (here ``evidence`` is a bare string) is treated as malformed and skipped —
    ``show`` returns a clean error, never an ``AttributeError`` traceback."""
    root = tmp_path / "store"
    directory = root / "proposals"
    directory.mkdir(parents=True)
    (directory / "prefer-uv-run.md").write_text(
        "---\n"
        "name: prefer-uv-run\n"
        "status: proposed\n"
        "type: rule\n"
        "evidence: broken\n"  # a string, not a list of refs
        "---\n\nbody\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["recommend", "show", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "not found or malformed" in result.output


def test_show_redacts_stored_body_with_configured_extra_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4 (§12.8/D15(b)): ``recommend show`` must redact the stored body at
    display time with the *currently configured* extras — so a custom pattern
    added AFTER the proposal was persisted (or a legacy/hand-edited body) still
    can't leak a secret into `show`'s output."""
    from neurobase.core.config import Config, RedactConfig

    root = tmp_path / "store"
    # Persisted first (built-in redaction misses the custom shape → stored raw).
    proposals.write_ranked(root, [_ranked(draft="deploy with SEKRET-4242 now")])
    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None and "SEKRET-4242" in doc.body  # secret is in the stored body

    # The custom pattern is configured only now, after persistence.
    monkeypatch.setattr(
        cli_module.proposals,
        "load_config",
        lambda: Config(redact=RedactConfig(extra_patterns=[r"SEKRET-[0-9]+"])),
    )

    result = runner.invoke(app, ["recommend", "show", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 0
    assert "SEKRET-4242" not in result.output
    assert "[REDACTED:custom]" in result.output


def test_show_and_list_skip_unparseable_yaml_proposal(tmp_path: Path) -> None:
    """R2-1 (§12.6 Invariant): a proposal whose frontmatter is genuinely
    unparseable YAML (not merely schema-invalid) MUST be skipped, never crash —
    ``show`` errors cleanly and ``list`` omits it."""
    root = tmp_path / "store"
    directory = root / "proposals"
    directory.mkdir(parents=True)
    proposals.write_ranked(root, [_ranked(slug="good-one")])
    # An unterminated flow sequence — raises yaml.YAMLError, not ValueError.
    (directory / "prefer-uv-run.md").write_text(
        "---\nname: prefer-uv-run\nevidence: [unterminated\n---\n\nbody\n", encoding="utf-8"
    )

    shown = runner.invoke(app, ["recommend", "show", "prefer-uv-run", "--root", str(root)])
    listed = runner.invoke(app, ["recommend", "list", "--root", str(root)])

    assert shown.exit_code == 1
    assert "not found or malformed" in shown.output
    assert listed.exit_code == 0
    assert "good-one" in listed.output
    assert "prefer-uv-run" not in listed.output


def test_reject_records_candidate_type_for_miner_feedback(tmp_path: Path) -> None:
    """F5 (§12.2/§12.4): a CLI rejection carries the proposal's candidate_type on
    its ledger event, so ``corpus.load_ledger_summary`` can build the per-type
    reject counts the miner prompt depends on."""
    from neurobase.recommender import corpus

    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])

    result = runner.invoke(app, ["recommend", "reject", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 0
    summary = corpus.load_ledger_summary(root)
    assert summary.reject_counts == {"repeated-instruction": 1}
