"""Unit tests for the shared install service (Web UI Phase 1 D-1).

`install.py` lifts the diff -> consent -> backup -> atomic-write -> ledger
choreography out of `cli/__init__.py:recommend_accept` so it can be split
across a GET (preview) and a POST (commit) for the web UI. These tests drive
`prepare_install`/`commit_install` directly, without going through the CLI —
`tests/test_cli_recommend.py` remains the behavior-preservation oracle for the
CLI wrapper itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from neurobase.core import projects, store
from neurobase.recommender import install, proposals
from neurobase.recommender.corpus import EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores


def _rule_candidate(
    slug: str = "prefer-uv-run", draft: str = "Always use uv run."
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
        supersedes=[],
        evidence=[EvidenceRef.proposal("old-example")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def _skill_candidate(
    slug: str = "commit-often", draft: str = "Commit early and often."
) -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="skill",
        candidate_type="repeated-workflow",
        title="Commit often",
        rationale="Repeated workflow",
        draft=draft,
        target="project-skill",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("old-example")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def _register(root: Path, repo: Path, slug: str = "neurobase") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    projects.register_project(root, repo, slug=slug)


# --- not found ---------------------------------------------------------------


def test_prepare_install_missing_slug_raises_not_found(tmp_path: Path) -> None:
    root = tmp_path / "store"

    with pytest.raises(install.ProposalNotFoundError):
        install.prepare_install(root, "does-not-exist")


def test_prepare_install_malformed_proposal_raises_not_found(tmp_path: Path) -> None:
    root = tmp_path / "store"
    directory = root / "proposals"
    directory.mkdir(parents=True)
    (directory / "broken.md").write_text(
        "---\nname: broken\nevidence: not-a-list\n---\n\nbody\n", encoding="utf-8"
    )

    with pytest.raises(install.ProposalNotFoundError):
        install.prepare_install(root, "broken")


# --- decided-status guard ------------------------------------------------------


@pytest.mark.parametrize("status", ["rejected", "superseded"])
def test_prepare_install_decided_status_guard_fires_before_render(
    tmp_path: Path, status: str
) -> None:
    """The guard must fire BEFORE `emitters.prepare` — proved here by never
    registering the candidate's project. If the guard didn't fire first, the
    call would instead raise a `ValueError` from `emitters.prepare`'s
    unregistered-project check, not `ProposalDecidedError`."""
    root = tmp_path / "store"
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    if status == "rejected":
        proposals.reject_proposal(root, slug)
    else:
        doc = proposals.load_proposal(root, slug)
        assert doc is not None
        frontmatter = dict(doc.frontmatter)
        frontmatter["status"] = "superseded"
        store.write_doc(doc.file_path, frontmatter, doc.body)

    with pytest.raises(install.ProposalDecidedError) as exc_info:
        install.prepare_install(root, slug)

    assert exc_info.value.slug == slug
    assert exc_info.value.status == status
    assert not (root / "backups").exists()


# --- no-op detection -----------------------------------------------------------


def test_prepare_install_no_op_after_commit(tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])

    first = install.prepare_install(root, slug)
    assert first.already_up_to_date is False
    assert first.artifact.before != first.artifact.after

    install.commit_install(root, first)

    second = install.prepare_install(root, slug)
    assert second.already_up_to_date is True
    assert second.artifact.before == second.artifact.after
    # ADR-0020 D40: already `accepted`, so this stays a plain no-op — there is
    # nothing to write AND nothing to record.
    assert second.records_acceptance is False


# --- ADR-0020 D40: installed-but-unrecorded ------------------------------------


def test_prepare_install_flags_a_matching_artifact_the_record_does_not_claim(
    tmp_path: Path,
) -> None:
    """The state a drift-repair `revert` leaves behind: the artifact matches the
    render byte-for-byte but the proposal reads `proposed`. That is recordable
    without a write — the alternative (review F2) strands it forever."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    installed = repo / "AGENTS.md"
    install.commit_install(root, install.prepare_install(root, slug))
    rendered = installed.read_bytes()
    installed.write_text("hand-edited", encoding="utf-8")
    proposals.revert_proposal(root, slug)
    installed.write_bytes(rendered)

    preview = install.prepare_install(root, slug)

    assert preview.already_up_to_date is True
    assert preview.records_acceptance is True

    result = install.record_acceptance(root, preview)

    assert result.backup_dir is None
    assert installed.read_bytes() == rendered  # recorded, never rewritten
    doc = proposals.load_proposal(root, slug)
    assert doc is not None and doc.get("status") == "accepted"
    assert proposals.artifact_state(root, doc, slug) == proposals.ARTIFACT_LIVE


def test_record_acceptance_refuses_a_preview_that_is_not_recordable(
    tmp_path: Path,
) -> None:
    """The two accept outcomes must never be confusable at a call site: a
    preview with real bytes to write is a `commit_install`, not a record."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])

    preview = install.prepare_install(root, slug)

    assert preview.records_acceptance is False
    with pytest.raises(ValueError, match="not in a recordable state"):
        install.record_acceptance(root, preview)
    assert not (repo / "AGENTS.md").exists()
    assert proposals.ledger_history(root, slug)[-1]["event"] == "proposed"


def test_record_acceptance_refuses_a_stale_proposal(tmp_path: Path) -> None:
    """Review P1-DATA-INTEGRITY-004: the target is untouched, but the *proposal's
    draft* changed since the preview. Recording would mark the new draft accepted
    while the installed artifact came from the old one — refuse, record nothing."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    installed = repo / "AGENTS.md"
    install.commit_install(root, install.prepare_install(root, slug))
    rendered = installed.read_bytes()
    installed.write_text("hand-edited", encoding="utf-8")
    proposals.revert_proposal(root, slug)
    installed.write_bytes(rendered)

    preview = install.prepare_install(root, slug)
    assert preview.records_acceptance is True
    ledger_before = proposals.ledger_history(root, slug)

    # The proposal moves under us; the TARGET is deliberately left alone.
    assert proposals.save_edited_draft(root, slug, "A completely different rule.")

    with pytest.raises(install.StaleProposalError):
        install.record_acceptance(root, preview)

    assert proposals.load_proposal(root, slug).get("status") == "proposed"  # type: ignore[union-attr]
    assert [e["event"] for e in proposals.ledger_history(root, slug)] == [
        *[e["event"] for e in ledger_before],
        "edited",  # only the edit itself, no acceptance
    ]
    assert installed.read_bytes() == rendered


def test_record_acceptance_refuses_a_stale_preview(tmp_path: Path) -> None:
    """Review P1-DATA-INTEGRITY-001: `records_acceptance` is decided at preview
    time, but the target can change before the write (e.g. during a long confirm
    prompt). `record_acceptance` re-reads at the mutation boundary and refuses if
    the bytes no longer equal the previewed render — never stamping a hash that
    disagrees with disk, and never mutating the proposal or ledger."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    installed = repo / "AGENTS.md"
    install.commit_install(root, install.prepare_install(root, slug))
    rendered = installed.read_bytes()
    # Reach the recordable state (installed-but-unrecorded): orphan, revert, restore.
    installed.write_text("hand-edited", encoding="utf-8")
    proposals.revert_proposal(root, slug)
    installed.write_bytes(rendered)

    preview = install.prepare_install(root, slug)
    assert preview.records_acceptance is True
    ledger_before = proposals.ledger_history(root, slug)

    # The world changes between preview and the write.
    installed.write_text("changed after the preview", encoding="utf-8")

    with pytest.raises(install.StaleArtifactError):
        install.record_acceptance(root, preview)

    # Nothing recorded, nothing backed up, the file left exactly as it now is.
    assert proposals.load_proposal(root, slug).get("status") == "proposed"  # type: ignore[union-attr]
    assert proposals.ledger_history(root, slug) == ledger_before
    assert not (root / "backups").exists()
    assert installed.read_text(encoding="utf-8") == "changed after the preview"


# --- foreign-target detection ---------------------------------------------------


def test_a_matching_foreign_target_is_never_silently_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0020 D40's exclusion. A file Neurobase does not own must not become
    an "acceptance" just because its bytes happen to match — recording it would
    hand ownership (and a later overwrite-on-accept) to a file the user wrote.
    The renderer is stubbed because a genuinely foreign file cannot normally
    carry our ownership markers, which is what makes this defence-in-depth."""
    from neurobase.recommender import emitters

    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    identical = "same bytes on both sides"
    monkeypatch.setattr(
        install.emitters,
        "prepare",
        lambda *a, **k: emitters.Artifact(
            path=repo / "AGENTS.md",
            before=identical,
            after=identical,
            target="AGENTS.md",
            foreign=True,
        ),
    )

    preview = install.prepare_install(root, slug)

    assert preview.already_up_to_date is True
    assert preview.records_acceptance is False
    with pytest.raises(ValueError, match="not in a recordable state"):
        install.record_acceptance(root, preview)


def test_prepare_install_surfaces_foreign_target_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "commit-often"
    proposals.write_ranked(root, [_skill_candidate(slug)])

    skill_path = repo / ".claude" / "skills" / slug / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    foreign_text = "---\nname: commit-often\n---\n\nHand-written, not ours.\n"
    skill_path.write_text(foreign_text, encoding="utf-8")

    preview = install.prepare_install(root, slug, target="project")

    assert preview.artifact.foreign is True
    assert preview.already_up_to_date is False
    # Pure preview: the foreign file on disk must be untouched.
    assert skill_path.read_text(encoding="utf-8") == foreign_text
    assert not (root / "backups").exists()


# --- full commit -----------------------------------------------------------------


def test_commit_install_writes_backs_up_and_records_ledger_hash(tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    target_path = repo / "AGENTS.md"
    target_path.write_text("# Existing notes\n", encoding="utf-8")

    preview = install.prepare_install(root, slug)
    assert preview.already_up_to_date is False

    result = install.commit_install(root, preview)

    assert result.path == target_path
    assert target_path.read_text(encoding="utf-8") == preview.artifact.after
    expected_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
    assert result.installed_hash == expected_hash

    # A backup of the pre-existing content was taken before the overwrite.
    assert result.backup_dir is not None
    backed_up = result.backup_dir / "AGENTS.md"
    assert backed_up.exists()
    assert backed_up.read_text(encoding="utf-8") == "# Existing notes\n"

    doc = proposals.load_proposal(root, slug)
    assert doc is not None
    assert doc.get("status") == "accepted"
    assert doc.get("installed_path") == str(target_path)

    history = proposals.ledger_history(root, slug)
    accepted_events = [event for event in history if event["event"] == "accepted"]
    assert len(accepted_events) == 1
    assert accepted_events[-1]["installed_hash"] == expected_hash


def test_commit_install_no_backup_when_target_did_not_exist(tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])

    preview = install.prepare_install(root, slug)
    result = install.commit_install(root, preview)

    assert result.backup_dir is None
    assert result.path.exists()


def test_record_acceptance_binds_the_checked_document_to_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1-DATA-INTEGRITY-004 (round 4): `record_acceptance` validates one
    document, then `accept_proposal` reloads independently — so without binding,
    the version that becomes `accepted` need not be the version checked. Mutate
    deterministically inside that window (after the render comparison, before the
    write) and the acceptance must be refused, not silently applied to the new
    version."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    _register(root, repo)
    slug = "prefer-uv-run"
    proposals.write_ranked(root, [_rule_candidate(slug)])
    installed = repo / "AGENTS.md"
    install.commit_install(root, install.prepare_install(root, slug))
    rendered = installed.read_bytes()
    installed.write_text("hand-edited", encoding="utf-8")
    proposals.revert_proposal(root, slug)
    installed.write_bytes(rendered)

    preview = install.prepare_install(root, slug)
    assert preview.records_acceptance is True
    ledger_before = proposals.ledger_history(root, slug)

    real_prepare = install.emitters.prepare

    def _prepare_then_mutate(*args: object, **kwargs: object) -> object:
        artifact = real_prepare(*args, **kwargs)  # type: ignore[arg-type]
        # The guard has now loaded + rendered `current`; change the proposal
        # before `accept_proposal` does its own reload.
        doc = proposals.load_proposal(root, slug)
        assert doc is not None
        frontmatter = dict(doc.frontmatter)
        frontmatter["candidate_type"] = "repeated-correction"
        store.write_doc(doc.file_path, frontmatter, doc.body)
        return artifact

    monkeypatch.setattr(install.emitters, "prepare", _prepare_then_mutate)

    with pytest.raises(install.StaleProposalError):
        install.record_acceptance(root, preview)

    assert proposals.load_proposal(root, slug).get("status") == "proposed"  # type: ignore[union-attr]
    assert proposals.ledger_history(root, slug) == ledger_before
