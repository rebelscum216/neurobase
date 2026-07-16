"""Tests for the Suggestions review routes (Web UI Phase 1 plan, "Routes
(Suggestions only)" and "Testing"): list + metrics strip, detail (draft,
evidence, history), the accept preview -> CSRF-protected confirm -> commit
flow (the exact diff -> consent -> backup -> atomic-write -> ledger
choreography `install.py` shares with the CLI), reject, and the edit
round-trip.

Every store root here is a pytest ``tmp_path`` fixture — never a real path
on this machine (repo SAFETY rules) — and no fixture or test ever installs a
"user"-scope skill (which would write under the real ``~/.claude/skills``);
every skill acceptance below uses ``target=project``, landing entirely
inside the tmp repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects
from neurobase.recommender import install, proposals
from neurobase.recommender.corpus import EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores
from neurobase.webui.app import build_app

# --- fixture candidates ------------------------------------------------------


def _rule_candidate(slug: str, draft: str = "Always use uv run.") -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="rule",
        candidate_type="repeated-instruction",
        title="Prefer uv run",
        rationale="Repeated correction across sessions.",
        draft=draft,
        target="AGENTS.md",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("seed-evidence")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def _skill_candidate(slug: str, draft: str = "Commit early and often.") -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="skill",
        candidate_type="repeated-workflow",
        title="Commit often",
        rationale="Repeated workflow across sessions.",
        draft=draft,
        target="project-skill",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("seed-evidence")],
        scores=Scores(4, 2, 1.0, 7.0),
        sessions=3,
        agents=1,
        projects=1,
    )


@dataclass
class Seed:
    root: Path
    repo: Path
    proposed_slug: str  # rule, still `proposed`
    accepted_slug: str  # skill, genuinely accepted+installed under `repo`
    rejected_slug: str  # rule, genuinely rejected


@pytest.fixture
def seed(tmp_path: Path) -> Seed:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    projects.register_project(root, repo, slug="neurobase")

    proposed_slug = "prefer-uv-run"
    accepted_slug = "commit-often"
    rejected_slug = "avoid-force-push"

    proposals.write_ranked(
        root,
        [
            _rule_candidate(proposed_slug),
            _skill_candidate(accepted_slug),
            _rule_candidate(rejected_slug, draft="Never force-push a shared branch."),
        ],
    )

    # `accepted_slug` goes through the real install service (never `--yes`,
    # never a hand-crafted frontmatter edit) so it carries a genuine
    # `accepted` ledger event and an installed artifact under the tmp repo —
    # `target="project"` only, never the real `~/.claude/skills`.
    preview = install.prepare_install(root, accepted_slug, target="project")
    install.commit_install(root, preview)

    proposals.reject_proposal(root, rejected_slug, reason="not a real pattern")

    return Seed(root, repo, proposed_slug, accepted_slug, rejected_slug)


@pytest.fixture
def app(seed: Seed) -> Starlette:
    return build_app(seed.root)


@pytest.fixture
def client(app: Starlette) -> TestClient:
    return TestClient(app)


# --- list ---------------------------------------------------------------------


def test_list_renders_all_three_statuses(client: TestClient, seed: Seed) -> None:
    response = client.get("/suggestions")
    assert response.status_code == 200
    assert seed.proposed_slug in response.text
    assert seed.accepted_slug in response.text
    assert seed.rejected_slug in response.text
    assert "status-chip proposed" in response.text
    assert "status-chip accepted" in response.text
    assert "status-chip rejected" in response.text
    # The metrics strip (metrics.compute_metrics) rendered without a crash.
    assert "Decided" in response.text


# --- detail ---------------------------------------------------------------------


def test_detail_renders_evidence_and_draft(client: TestClient, seed: Seed) -> None:
    response = client.get(f"/suggestions/{seed.proposed_slug}")
    assert response.status_code == 200
    assert "Always use uv run." in response.text
    # The seed evidence ref names a proposal slug that doesn't exist on disk
    # -> resolve_evidence reports it unresolved (D21 fail-soft), never dropped.
    assert "seed-evidence" in response.text
    assert "unresolved" in response.text


def test_detail_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/suggestions/does-not-exist")
    assert response.status_code == 404


# --- accept preview (GET) ------------------------------------------------------


def test_accept_preview_shows_diff(client: TestClient, seed: Seed) -> None:
    response = client.get(f"/suggestions/{seed.proposed_slug}/accept")
    assert response.status_code == 200
    assert "AGENTS.md" in response.text
    assert "diff-add" in response.text
    assert "Always use uv run." in response.text


def test_accept_preview_on_decided_proposal_returns_409(client: TestClient, seed: Seed) -> None:
    response = client.get(f"/suggestions/{seed.rejected_slug}/accept")
    assert response.status_code == 409


# --- accept (POST) — CSRF / origin rejection writes nothing --------------------


def test_accept_post_without_csrf_token_is_rejected_and_writes_nothing(
    client: TestClient, seed: Seed
) -> None:
    target_path = seed.repo / "AGENTS.md"
    response = client.post(f"/suggestions/{seed.proposed_slug}/accept", data={})
    assert response.status_code == 403
    assert not target_path.exists()
    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert doc.get("status") == "proposed"


def test_accept_post_with_mismatched_origin_is_rejected_and_writes_nothing(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    target_path = seed.repo / "AGENTS.md"
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert not target_path.exists()
    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert doc.get("status") == "proposed"


# --- accept (POST) — full commit ------------------------------------------------


def test_full_accept_writes_backs_up_and_flips_status(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    target_path = seed.repo / "AGENTS.md"
    target_path.write_text("# Pre-existing notes\n", encoding="utf-8")

    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/suggestions/{seed.proposed_slug}")

    assert target_path.exists()
    assert "Always use uv run." in target_path.read_text(encoding="utf-8")

    backups_dir = seed.root / "backups"
    assert backups_dir.exists()
    backup_dirs = list(backups_dir.iterdir())
    assert len(backup_dirs) == 1
    assert (backup_dirs[0] / "AGENTS.md").read_text(encoding="utf-8") == "# Pre-existing notes\n"

    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert doc.get("status") == "accepted"

    history = proposals.ledger_history(seed.root, seed.proposed_slug)
    assert history[-1]["event"] == "accepted"


# --- reject ---------------------------------------------------------------------


def test_reject_flips_proposed_to_rejected(client: TestClient, app: Starlette, seed: Seed) -> None:
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/reject",
        data={"csrf_token": app.state.csrf_token, "reason": "no longer relevant"},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert doc.get("status") == "rejected"

    history = proposals.ledger_history(seed.root, seed.proposed_slug)
    assert history[-1]["event"] == "rejected"
    assert history[-1]["reason"] == "no longer relevant"


# --- edit -------------------------------------------------------------------------


def test_edit_round_trips_new_draft(client: TestClient, app: Starlette, seed: Seed) -> None:
    get_response = client.get(f"/suggestions/{seed.proposed_slug}/edit")
    assert get_response.status_code == 200
    assert "Always use uv run." in get_response.text

    new_draft = "Always use `uv run`, every single time."
    post_response = client.post(
        f"/suggestions/{seed.proposed_slug}/edit",
        data={"csrf_token": app.state.csrf_token, "draft": new_draft},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert post_response.status_code == 303

    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert proposals.extract_draft(doc.body) == new_draft

    history = proposals.ledger_history(seed.root, seed.proposed_slug)
    assert history[-1]["event"] == "edited"
