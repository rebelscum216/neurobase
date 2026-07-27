"""Tests for the Skills gallery + revert action (app-shell plan, Phase S).

Two accepted proposals are installed for real through the shared install service:
one stays **live** on disk, the other is **orphaned** (its managed block is
removed). The gallery must show both, offer revert only on the drifted one, and
the revert POST must repair the drifted record while **refusing** the live one
(the ADR-0020 boundary that stops accept→revert→reject from orphaning a live
artifact) and refusing any POST without a CSRF token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects
from neurobase.recommender import install, proposals
from neurobase.recommender.corpus import EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores
from neurobase.webui.app import build_app
from neurobase.webui.security import CSRF_FORM_FIELD

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def _ranked(**overrides: Any) -> RankedCandidate:
    base: dict[str, Any] = {
        "slug": "prefer-uv-run",
        "type": "rule",
        "candidate_type": "repeated-instruction",
        "title": "Prefer uv run",
        "rationale": "corrected repeatedly across sessions",
        "draft": "Always invoke Python via `uv run`.",
        "target": "AGENTS.md",
        "project": "neurobase",
        "supersedes": [],
        "evidence": [EvidenceRef.raw("neurobase", "2026-07-01T00-00-00Z_claude_ab12cd34.md")],
        "scores": Scores(recurrence=5, breadth=6, recency=0.86, total=25.8),
        "sessions": 3,
        "agents": 2,
        "projects": 1,
    }
    base.update(overrides)
    return RankedCandidate(**base)


def _accept(root: Path, slug: str, project: str) -> None:
    proposals.write_ranked(root, [_ranked(slug=slug, project=project, title=slug)], now=NOW)
    install.commit_install(root, install.prepare_install(root, slug))


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    repo_alpha = tmp_path / "alpha"
    repo_beta = tmp_path / "beta"
    repo_alpha.mkdir()
    repo_beta.mkdir()
    projects.register_project(root, repo_alpha, slug="alpha")
    projects.register_project(root, repo_beta, slug="beta")

    _accept(root, "live-skill", "alpha")  # stays live on disk
    _accept(root, "drifted-skill", "beta")
    # remove the managed block for the drifted one → artifact_state == orphaned
    (repo_beta / "AGENTS.md").write_text("the user replaced the whole file\n", encoding="utf-8")
    return root


@pytest.fixture
def app(store_root: Path) -> Starlette:
    return build_app(store_root)


@pytest.fixture
def client(app: Starlette) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _read_status(root: Path, slug: str) -> str:
    doc = proposals.load_proposal(root, slug)
    assert doc is not None
    return str(doc.get("status"))


def test_skills_nav_is_live_with_count(client: TestClient) -> None:
    html = client.get("/skills").text
    assert 'href="/skills"' in html
    assert '<span class="count">2</span>' in html  # two accepted proposals


def test_gallery_shows_both_skills_and_their_states(client: TestClient) -> None:
    html = client.get("/skills").text
    assert "live-skill" in html and "drifted-skill" in html
    assert ">live<" in html and ">orphaned<" in html


def test_live_skill_has_no_revert_button(client: TestClient) -> None:
    html = client.get("/skills").text
    assert 'action="/skills/drifted-skill/revert"' in html, "the drifted skill must offer revert"
    assert 'action="/skills/live-skill/revert"' not in html, "a live skill must NOT offer revert"


def test_revert_repairs_the_drifted_skill(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    r = client.post(
        "/skills/drifted-skill/revert",
        data={CSRF_FORM_FIELD: app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _read_status(store_root, "drifted-skill") == "proposed"


def test_revert_of_a_live_skill_is_refused(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    """The load-bearing ADR-0020 guard: even a well-formed, CSRF-valid POST must
    NOT revert a live artifact — revert_proposal re-checks liveness at write time
    and 409s, so the proposal stays accepted."""
    r = client.post(
        "/skills/live-skill/revert",
        data={CSRF_FORM_FIELD: app.state.csrf_token},
        headers={"origin": str(client.base_url)},
    )
    assert r.status_code == 409
    assert _read_status(store_root, "live-skill") == "accepted", "a live skill was reverted"


def test_revert_without_csrf_is_rejected_and_writes_nothing(
    client: TestClient, store_root: Path
) -> None:
    r = client.post("/skills/drifted-skill/revert", data={})
    assert r.status_code == 403
    assert _read_status(store_root, "drifted-skill") == "accepted", "a CSRF-less POST mutated state"
