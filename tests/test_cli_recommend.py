"""CLI integration tests for Phase 8 Workstream F (spec §12.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli_module
from neurobase.brain.select import BrainResolution
from neurobase.cli import app
from neurobase.core import store
from neurobase.recommender import proposals
from neurobase.recommender.corpus import Corpus, EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores

runner = CliRunner()


def _ranked(
    slug: str = "prefer-uv-run",
    draft: str = "Always use uv run.",
    supersedes: list[str] | None = None,
) -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="rule",
        candidate_type="repeated-instruction",
        title="Prefer uv run",
        rationale="Repeated correction",
        draft=draft,
        target="AGENTS.md",
        project="neurobase",
        supersedes=supersedes or [],
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


def test_edit_race_to_rejected_keeps_a_named_blocked_status_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-UX-API-CONTRACT-010: if a reject lands between the CLI's status pre-check
    and the locked save, the in-lock guard raises `EditBlockedError`; the CLI must
    keep the named blocked-status message, not the generic "could not save"."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=None)  # still proposed → pre-check passes
    monkeypatch.setattr(cli_module.click, "edit", lambda *a, **k: "revised draft")

    def _raise_blocked(*_a: object, **_k: object) -> bool:
        raise proposals.EditBlockedError("prefer-uv-run", "rejected")

    monkeypatch.setattr(cli_module.proposals, "save_edited_draft", _raise_blocked)

    result = runner.invoke(app, ["recommend", "edit", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "status is rejected" in result.output
    assert "could not save" not in result.output


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


def test_accept_records_installed_hash_of_actual_written_bytes(tmp_path: Path) -> None:
    """§12.9/ADR-0007 D2 (workstream H): `recommend accept`'s ledger `accepted`
    event carries `installed_hash` computed from the artifact's real,
    just-written bytes on disk — not a stub or a hash of the draft alone."""
    import hashlib

    from neurobase.core import projects

    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    projects.register_project(root, repo, slug="neurobase")
    proposals.write_ranked(root, [_ranked()])

    result = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )

    assert result.exit_code == 0
    installed_path = repo / "AGENTS.md"
    assert installed_path.exists()
    expected_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
    history = proposals.ledger_history(root, "prefer-uv-run")
    accepted_events = [event for event in history if event["event"] == "accepted"]
    assert len(accepted_events) == 1
    assert accepted_events[-1]["installed_hash"] == expected_hash


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


# --- recommend reopen (ADR-0024, §12.7) — Codex P2-TEST-GAP-008 --------------
# The public command was previously exercised only at the function level; these
# drive the CLI itself: success on rejected, refusal on every other status
# (proposed / accepted / superseded) and on a missing proposal, with no write on
# the refusal path.


def test_reopen_returns_a_rejected_proposal_to_the_queue(tmp_path: Path) -> None:
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])
    proposals.reject_proposal(root, "prefer-uv-run", reason="not yet")

    result = runner.invoke(app, ["recommend", "reopen", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 0
    assert "rejected → proposed" in result.output
    assert _status(root) == "proposed"
    # exactly one reopened line, and the prior rejection is preserved
    events = [e["event"] for e in proposals.ledger_history(root, "prefer-uv-run")]
    assert events == ["proposed", "rejected", "reopened"]


def test_reopen_proposed_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])  # still proposed

    result = runner.invoke(app, ["recommend", "reopen", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "only a rejected proposal" in result.output
    assert _status(root) == "proposed"
    assert "reopened" not in [e["event"] for e in proposals.ledger_history(root, "prefer-uv-run")]


def test_reopen_accepted_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _accept_via_cli(tmp_path, root)  # status accepted, artifact live

    result = runner.invoke(app, ["recommend", "reopen", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "status is accepted" in result.output
    assert _status(root) == "accepted"
    assert "reopened" not in [e["event"] for e in proposals.ledger_history(root, "prefer-uv-run")]


def test_reopen_superseded_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """§12.7 names superseded as permanently non-reopenable — the CLI must refuse
    it and leave the proposal + ledger untouched."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="old-rule")])
    proposals.write_ranked(root, [_ranked(slug="new-rule", supersedes=["old-rule"])])
    assert _status(root, "old-rule") == "superseded"  # precondition
    ledger_before = proposals.ledger_history(root, "old-rule")

    result = runner.invoke(app, ["recommend", "reopen", "old-rule", "--root", str(root)])

    assert result.exit_code == 1
    assert "status is superseded" in result.output
    assert _status(root, "old-rule") == "superseded"
    assert proposals.ledger_history(root, "old-rule") == ledger_before


def test_reopen_missing_proposal_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "store"
    result = runner.invoke(app, ["recommend", "reopen", "does-not-exist", "--root", str(root)])
    assert result.exit_code == 1
    assert "not found or malformed" in result.output


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


# --- workstream H: `status --recommender` (§12.9/D4) -------------------------


def test_status_recommender_on_empty_store_prints_insufficient_data(tmp_path: Path) -> None:
    """`status --recommender` on an empty store never crashes and never
    requires an enabled project — every metric prints "insufficient data"."""
    root = tmp_path / "store"

    result = runner.invoke(app, ["status", "--recommender", "--root", str(root)])

    assert result.exit_code == 0
    assert "insufficient data" in result.output
    assert "Decided: 0" in result.output
    assert "Precision: insufficient data" in result.output
    assert "Edited rate: insufficient data" in result.output
    assert "Reviewed events: 0" in result.output
    # §12.9: zero accepted proposals is "no data", not a measured 0/0/0
    # (Codex round-2 finding) — the aggregate line reads "insufficient data".
    assert "Survival: insufficient data" in result.output
    assert "Recurrence reduction: insufficient data" in result.output


def test_status_recommender_refuses_newer_schema(tmp_path: Path) -> None:
    """D11 (Codex F1): `status --recommender` must refuse a store whose schema is
    newer than supported BEFORE reading any recommender state — the READ guard now
    runs on the store-wide metrics subpath too, not just the project-status path."""
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text(
        'schema = 999\ncreated_at = "2020-01-01T00:00:00Z"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["status", "--recommender", "--root", str(root)])

    assert result.exit_code == 1
    assert "schema" in result.output
    assert "insufficient data" not in result.output  # refused before any read


def test_status_recommender_without_project_flag_bypasses_project_resolution(
    tmp_path: Path,
) -> None:
    """Recommender metrics are store-wide (§12.9/D4) — `status --recommender`
    must succeed even when the launch cwd resolves to no enabled project,
    unlike plain `status`."""
    root = tmp_path / "store"

    plain = runner.invoke(app, ["status", "--root", str(root), "--cwd", str(tmp_path)])
    recommender = runner.invoke(
        app, ["status", "--recommender", "--root", str(root), "--cwd", str(tmp_path)]
    )

    assert plain.exit_code == 1
    assert "Not an enabled project" in plain.output
    assert recommender.exit_code == 0


def test_status_recommender_on_populated_ledger_prints_real_numbers(tmp_path: Path) -> None:
    """A populated ledger produces real, non-"insufficient data" precision/
    edited-rate/reviewed-events numbers."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="prefer-uv-run"), _ranked(slug="other-rule")])
    proposals.reject_proposal(root, "other-rule")
    installed_path = tmp_path / "AGENTS.md"
    installed_path.write_text("installed", encoding="utf-8")
    proposals.accept_proposal(
        root, "prefer-uv-run", target="AGENTS.md", installed_path=installed_path
    )

    result = runner.invoke(app, ["status", "--recommender", "--root", str(root)])

    assert result.exit_code == 0
    assert "Decided: 2 (accepted 1, rejected 1)" in result.output
    assert "Precision: 0.5000" in result.output
    assert "Edited rate: 0.0000" in result.output
    assert "Reviewed events: 2" in result.output
    assert "prefer-uv-run: insufficient data" in result.output


# --- G2 / ADR-0020: recommend revert -----------------------------------------
#
# Every test below drives the REAL `recommend accept` — which renders through
# `emitters.prepare`, writes the artifact, and records the hash later drift is
# measured against. Review F2 got past the first round precisely because the
# original tests called `accept_proposal` directly and so never met
# `prepare_install`'s no-op path; calling the command is the point.


def _accept_via_cli(tmp_path: Path, root: Path) -> Path:
    """Register a project, propose, and really accept — returning the installed
    artifact's path."""
    from neurobase.core import projects

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    projects.register_project(root, repo, slug="neurobase")
    proposals.write_ranked(root, [_ranked()])
    result = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )
    assert result.exit_code == 0, result.output
    installed = repo / "AGENTS.md"
    assert installed.exists()
    return installed


def _skill_ranked(slug: str = "commit-often") -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="skill",
        candidate_type="repeated-workflow",
        title="Commit often",
        rationale="Repeated workflow",
        draft="Commit early and often.",
        target="project-skill",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("old-example")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def _accept_skill_via_cli(tmp_path: Path, root: Path, slug: str = "commit-often") -> Path:
    """Really accept a *skill* proposal, `--target project` only — never `user`,
    which would write under the real ~/.claude/skills (repo SAFETY rule)."""
    from neurobase.core import projects

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    projects.register_project(root, repo, slug="neurobase")
    proposals.write_ranked(root, [_skill_ranked(slug)])
    result = runner.invoke(
        app, ["recommend", "accept", slug, "--target", "project", "--root", str(root), "--yes"]
    )
    assert result.exit_code == 0, result.output
    installed = repo / ".claude" / "skills" / slug / "SKILL.md"
    assert installed.exists()
    return installed


def _status(root: Path, slug: str = "prefer-uv-run") -> str | None:
    doc = proposals.load_proposal(root, slug)
    assert doc is not None
    status = doc.get("status")
    return str(status) if status is not None else None


def _revert(root: Path, *, extra: list[str] | None = None, stdin: str | None = None):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app,
        ["recommend", "revert", "prefer-uv-run", "--root", str(root), *(extra or [])],
        input=stdin,
    )


def test_revert_refuses_while_the_artifact_is_healthy(tmp_path: Path) -> None:
    """ADR-0020 D43 / review F1: `accept → revert` is refused outright while the
    installed artifact is present and unchanged, so the `accept → revert →
    reject` sequence that would orphan a live managed file cannot even start.
    Reject stays blocked on `accepted`, which is what makes the pair airtight."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)

    reverted = _revert(root, extra=["--yes"])
    rejected = runner.invoke(app, ["recommend", "reject", "prefer-uv-run", "--root", str(root)])

    assert reverted.exit_code == 1
    assert "not an uninstall" in reverted.output
    assert rejected.exit_code == 1
    assert "status is accepted" in rejected.output
    # The only two things that could contradict each other still agree.
    assert installed.exists()
    assert _status(root) == "accepted"


def test_revert_after_deleting_the_artifact_then_reject_leaves_nothing_installed(
    tmp_path: Path,
) -> None:
    """ADR-0020 D43: the sanctioned route to retiring an accepted proposal —
    remove the artifact, revert the now-false record, then reject. The end state
    is `rejected` with nothing installed, never `rejected` with a live file."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    installed.unlink()

    reverted = _revert(root, extra=["--yes"])
    rejected = runner.invoke(app, ["recommend", "reject", "prefer-uv-run", "--root", str(root)])

    assert reverted.exit_code == 0, reverted.output
    assert "is gone" in reverted.output
    assert rejected.exit_code == 0, rejected.output
    assert _status(root) == "rejected"
    assert not installed.exists()


def test_revert_then_reaccept_restores_acceptance_and_reinstalls_the_block(
    tmp_path: Path,
) -> None:
    """Review F2 regression, through the real install service: after repairing a
    modified artifact's record, `accept` must be able to restore acceptance —
    the previous shape returned "Already up to date." and left it `proposed`."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    installed.write_text("the user rewrote this by hand", encoding="utf-8")

    reverted = _revert(root, extra=["--yes"])
    assert reverted.exit_code == 0, reverted.output
    assert _status(root) == "proposed"

    reaccepted = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )

    assert reaccepted.exit_code == 0, reaccepted.output
    assert _status(root) == "accepted"
    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None and doc.get("installed_path") == str(installed)
    text = installed.read_text(encoding="utf-8")
    # The managed block is back, and §12's preserve-every-other-byte rule kept
    # the user's own text around it.
    assert "<!-- neurobase:rule:prefer-uv-run" in text
    assert "Always use uv run." in text
    assert "the user rewrote this by hand" in text
    # The freshly recorded hash describes what is actually on disk now.
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_LIVE
    accepted = [
        event
        for event in proposals.ledger_history(root, "prefer-uv-run")
        if event["event"] == "accepted"
    ]
    assert len(accepted) == 2


def test_reaccept_records_acceptance_when_the_artifact_already_matches(
    tmp_path: Path,
) -> None:
    """ADR-0020 D44: installed-but-unrecorded. The artifact was restored to
    exactly the rendered bytes after a revert, so there is nothing to write —
    but acceptance must still be recordable, or this proposal is stranded
    `proposed` forever with its artifact live on disk (review F2's residue)."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    rendered = installed.read_text(encoding="utf-8")
    installed.write_text("temporarily different", encoding="utf-8")
    assert _revert(root, extra=["--yes"]).exit_code == 0
    installed.write_text(rendered, encoding="utf-8")

    result = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "already matches" in result.output
    assert "recorded only" in result.output
    assert _status(root) == "accepted"
    doc = proposals.load_proposal(root, "prefer-uv-run")
    assert doc is not None and doc.get("installed_path") == str(installed)
    # Recorded, not rewritten: the bytes are untouched and the hash matches them.
    assert installed.read_text(encoding="utf-8") == rendered
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_LIVE


def test_reaccept_record_only_refuses_if_the_file_changes_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1-DATA-INTEGRITY-001, at the CLI surface: the record-only preview
    is built before the confirm prompt. If the target changes while the prompt is
    open, the command must record nothing rather than stamp the stale render's
    hash. Simulated by a `typer.confirm` that mutates the file, then says yes."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    rendered = installed.read_text(encoding="utf-8")
    installed.write_text("temporarily different", encoding="utf-8")
    assert _revert(root, extra=["--yes"]).exit_code == 0
    installed.write_text(rendered, encoding="utf-8")  # installed-but-unrecorded

    def _confirm_but_mutate(*_a: object, **_k: object) -> bool:
        installed.write_text("changed while you were deciding", encoding="utf-8")
        return True

    monkeypatch.setattr(cli_module.typer, "confirm", _confirm_but_mutate)
    result = runner.invoke(app, ["recommend", "accept", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "changed after the acceptance was previewed" in result.output
    assert _status(root) == "proposed"  # nothing recorded
    accepted = [
        event
        for event in proposals.ledger_history(root, "prefer-uv-run")
        if event["event"] == "accepted"
    ]
    assert len(accepted) == 1  # only the original accept, no stale record


def test_revert_refuses_a_crlf_converted_owned_skill(tmp_path: Path) -> None:
    """Review F1 (round 3): ownership is a property of the frontmatter *values*,
    not the file's line endings. A genuinely owned SKILL.md converted to CRLF
    keeps both ownership keys and is still installed — it must not read as gone
    and let `revert → reject` orphan it. Reproduced by Codex through the CLI."""
    root = tmp_path / "store"
    installed = _accept_skill_via_cli(tmp_path, root)
    lf_text = installed.read_text(encoding="utf-8")
    # Newline-only conversion: identical content and ownership keys.
    installed.write_bytes(lf_text.replace("\n", "\r\n").encode("utf-8"))

    reverted = runner.invoke(
        app, ["recommend", "revert", "commit-often", "--yes", "--root", str(root)]
    )
    rejected = runner.invoke(app, ["recommend", "reject", "commit-often", "--root", str(root)])

    assert reverted.exit_code == 1
    assert "still installed" in reverted.output
    assert rejected.exit_code == 1  # reject stays blocked on `accepted`
    assert _status(root, "commit-often") == "accepted"
    assert installed.exists()
    assert "neurobase_managed" in installed.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("label", "mangle"),
    [
        ("crlf", lambda t: t.replace("\n", "\r\n")),
        ("cr-only", lambda t: t.replace("\n", "\r")),
        ("utf8-bom", lambda t: "﻿" + t),
        ("fence-whitespace", lambda t: t.replace("---", "---   ", 1)),
    ],
)
def test_revert_refuses_a_live_skill_under_any_text_representation(
    tmp_path: Path, label: str, mangle: object
) -> None:
    """Review F1 (round 4): every round found another *representation* of a still
    installed, still owned SKILL.md that defeated the frontmatter parser and made
    a live skill look gone. The cause-level fix is that skill liveness no longer
    parses frontmatter at all — the file existing at the canonical path IS
    liveness — so none of these can orphan it."""
    root = tmp_path / "store"
    installed = _accept_skill_via_cli(tmp_path, root)
    original = installed.read_text(encoding="utf-8")
    installed.write_bytes(mangle(original).encode("utf-8"))  # type: ignore[operator]

    reverted = runner.invoke(
        app, ["recommend", "revert", "commit-often", "--yes", "--root", str(root)]
    )
    rejected = runner.invoke(app, ["recommend", "reject", "commit-often", "--root", str(root)])

    assert reverted.exit_code == 1, f"{label}: revert should refuse a live skill"
    assert "still installed" in reverted.output
    assert rejected.exit_code == 1  # reject stays blocked on `accepted`
    assert _status(root, "commit-often") == "accepted"
    assert installed.exists()


@pytest.mark.parametrize(
    ("label", "mangle"),
    [
        ("double-space", lambda t: t.replace("generated by", "generated  by")),
        ("reworded-suffix", lambda t: t.replace("hand edits inside this block", "DO NOT EDIT")),
        ("wrapped-comment", lambda t: t.replace("(generated by", "(\n    generated by")),
        ("dropped-suffix", lambda t: t.replace(" (generated by", " -->\nleftover (generated by")),
    ],
)
def test_revert_refuses_when_only_the_marker_suffix_changed(
    tmp_path: Path, label: str, mangle: object
) -> None:
    """Review F1 (round 5): the *identity token* for a rule block is the opening
    `<!-- neurobase:rule:<slug>` sentinel. The explanatory parenthetical after it
    is prose — re-wrapping or editing it leaves the block installed, so liveness
    must still say live. Matching the whole generated comment made a one-space
    change read as `orphaned` and let revert→reject orphan a live rule."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    installed.write_text(mangle(installed.read_text(encoding="utf-8")), encoding="utf-8")  # type: ignore[operator]

    reverted = _revert(root, extra=["--yes"])
    rejected = runner.invoke(app, ["recommend", "reject", "prefer-uv-run", "--root", str(root)])

    assert reverted.exit_code == 1, f"{label}: a live block must not be revertable"
    assert "still installed" in reverted.output
    assert rejected.exit_code == 1  # reject stays blocked on `accepted`
    assert _status(root) == "accepted"
    assert "Always use uv run." in installed.read_text(encoding="utf-8")


def test_revert_allows_it_once_the_sentinel_itself_is_gone(tmp_path: Path) -> None:
    """The other half of the sentinel contract: remove the identity token and the
    block really is gone, so the record is repairable. A lone *closing* marker
    left behind must not read as live."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    text = installed.read_text(encoding="utf-8")
    # Drop the opening sentinel, deliberately leaving the closing marker behind.
    text = text.replace("<!-- neurobase:rule:prefer-uv-run", "<!-- some other comment")
    installed.write_text(text, encoding="utf-8")
    assert "<!-- /neurobase:rule:prefer-uv-run -->" in text  # closing marker remains

    result = _revert(root, extra=["--yes"])

    assert result.exit_code == 0, result.output
    assert _status(root) == "proposed"


def test_revert_refuses_when_a_live_artifact_sits_at_another_registered_root(
    tmp_path: Path,
) -> None:
    """Review F1 (round 3): `_project_root` renders against `roots[0]`, but a
    moved repo re-registered under the same slug leaves `[old, new]`. Concluding
    "gone" from the stale first root would orphan the live block at the new one,
    so liveness checks every registered root (and the recorded install path)."""
    import shutil

    from neurobase.core import projects

    root = tmp_path / "store"
    _accept_via_cli(tmp_path, root)
    moved = tmp_path / "moved-repo"
    shutil.move(str(tmp_path / "repo"), str(moved))  # old root (and recorded path) now absent
    projects.register_project(root, moved, slug="neurobase")  # registry: [old, new]

    result = _revert(root, extra=["--yes"])

    assert result.exit_code == 1
    assert "still installed" in result.output
    assert _status(root) == "accepted"
    assert "<!-- neurobase:rule:prefer-uv-run" in (moved / "AGENTS.md").read_text(encoding="utf-8")


def test_record_only_refuses_if_the_proposal_draft_changes_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1-DATA-INTEGRITY-004: the target re-read alone is not enough. A
    concurrent `recommend edit` during the confirm prompt leaves the installed
    bytes untouched — so the target check passes against the stale render — while
    `accept_proposal` would reload and mark the *newly edited* draft accepted.
    Both sides of the preview are re-verified, so this records nothing."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    rendered = installed.read_text(encoding="utf-8")
    installed.write_text("temporarily different", encoding="utf-8")
    assert _revert(root, extra=["--yes"]).exit_code == 0
    installed.write_text(rendered, encoding="utf-8")  # installed-but-unrecorded

    def _confirm_but_edit_the_draft(*_a: object, **_k: object) -> bool:
        proposals.save_edited_draft(root, "prefer-uv-run", "A completely different rule.")
        return True

    monkeypatch.setattr(cli_module.typer, "confirm", _confirm_but_edit_the_draft)
    result = runner.invoke(app, ["recommend", "accept", "prefer-uv-run", "--root", str(root)])

    assert result.exit_code == 1
    assert "changed after the acceptance was previewed" in result.output
    assert _status(root) == "proposed"  # nothing recorded
    accepted = [
        event
        for event in proposals.ledger_history(root, "prefer-uv-run")
        if event["event"] == "accepted"
    ]
    assert len(accepted) == 1  # only the original accept
    # The installed bytes still match the ORIGINAL draft, not the edited one.
    assert installed.read_text(encoding="utf-8") == rendered


def test_record_only_refuses_if_only_candidate_type_changes_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1-DATA-INTEGRITY-004 (round 4): comparing a *subset* of proposal
    fields is the wrong shape. `candidate_type` feeds the skill emitter's
    description fallback, so changing it (valid→valid, exactly what a concurrent
    `recommend run` refresh does) alters the render while draft/status/target are
    untouched. The guard now compares the re-render, so this is refused."""
    root = tmp_path / "store"
    installed = _accept_skill_via_cli(tmp_path, root)
    rendered = installed.read_text(encoding="utf-8")
    installed.unlink()
    assert (
        runner.invoke(
            app, ["recommend", "revert", "commit-often", "--yes", "--root", str(root)]
        ).exit_code
        == 0
    )
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(rendered, encoding="utf-8")  # installed-but-unrecorded

    def _confirm_but_change_candidate_type(*_a: object, **_k: object) -> bool:
        doc = proposals.load_proposal(root, "commit-often")
        assert doc is not None
        frontmatter = dict(doc.frontmatter)
        frontmatter["candidate_type"] = "repeated-instruction"  # valid -> valid
        store.write_doc(doc.file_path, frontmatter, doc.body)
        return True

    monkeypatch.setattr(cli_module.typer, "confirm", _confirm_but_change_candidate_type)
    result = runner.invoke(
        app, ["recommend", "accept", "commit-often", "--target", "project", "--root", str(root)]
    )

    assert result.exit_code == 1
    assert "changed after the acceptance was previewed" in result.output
    assert _status(root, "commit-often") == "proposed"  # nothing recorded
    accepted = [
        event
        for event in proposals.ledger_history(root, "commit-often")
        if event["event"] == "accepted"
    ]
    assert len(accepted) == 1  # only the original accept


def test_reaccept_of_an_already_accepted_proposal_stays_a_pure_no_op(
    tmp_path: Path,
) -> None:
    """ADR-0020 D44 keeps §12.7's idempotent-accept contract intact: when the
    proposal already reads `accepted`, a matching render still writes nothing
    and records nothing."""
    root = tmp_path / "store"
    _accept_via_cli(tmp_path, root)

    result = runner.invoke(
        app, ["recommend", "accept", "prefer-uv-run", "--root", str(root), "--yes"]
    )

    assert result.exit_code == 0
    assert "Already up to date." in result.output
    accepted = [
        event
        for event in proposals.ledger_history(root, "prefer-uv-run")
        if event["event"] == "accepted"
    ]
    assert len(accepted) == 1


def test_revert_non_accepted_is_hard_error(tmp_path: Path) -> None:
    """G2: revert on a still-proposed proposal is a hard, named-status error."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()])

    result = _revert(root, extra=["--yes"])

    assert result.exit_code == 1
    assert "status is proposed" in result.output


def test_revert_prompt_abort_makes_no_change(tmp_path: Path) -> None:
    """G2: without `--yes`, declining the consent prompt leaves the proposal
    accepted and writes nothing."""
    root = tmp_path / "store"
    installed = _accept_via_cli(tmp_path, root)
    installed.unlink()

    result = _revert(root, stdin="n\n")

    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert _status(root) == "accepted"
    assert proposals.ledger_history(root, "prefer-uv-run")[-1]["event"] == "accepted"
