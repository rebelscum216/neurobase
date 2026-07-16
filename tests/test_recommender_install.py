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


# --- foreign-target detection ---------------------------------------------------


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
