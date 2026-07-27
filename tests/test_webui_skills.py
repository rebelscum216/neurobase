"""Tests for the machine-wide Skills gallery + drift-repair revert.

The gallery shows every SKILL.md on the machine, not just what Neurobase
installed: a Neurobase-managed skill on disk, a hand-authored **external** skill
in the user scope, and a Neurobase skill that's **missing** on disk (drift). The
external one offers no revert; the missing one does, and the ADR-0020 guard still
refuses to revert a live artifact even via a crafted POST.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects
from neurobase.recommender import install, proposals
from neurobase.recommender.corpus import EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores
from neurobase.webui import skills_scan
from neurobase.webui.app import build_app
from neurobase.webui.security import CSRF_FORM_FIELD

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def _skill(slug: str) -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="skill",
        candidate_type="repeated-workflow",
        title=slug,
        rationale="repeated across sessions",
        draft="# Do the thing\n\nStep one, step two.",
        target="project-skill",
        project="proj",
        supersedes=[],
        evidence=[EvidenceRef.raw("proj", "2026-07-01T00-00-00Z_claude_ab12cd34.md")],
        scores=Scores(recurrence=4, breadth=2, recency=0.9, total=8.0),
        sessions=3,
        agents=1,
        projects=1,
    )


def _accept_project_skill(root: Path, slug: str) -> None:
    proposals.write_ranked(root, [_skill(slug)], now=NOW)
    install.commit_install(root, install.prepare_install(root, slug, target="project"))


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    projects.register_project(root, repo, slug="proj")

    # 1. A Neurobase-managed skill, installed on disk (project scope).
    _accept_project_skill(root, "nb-skill")
    # 2. A Neurobase skill accepted, then its SKILL.md removed → drift (missing).
    _accept_project_skill(root, "gone-skill")
    shutil.rmtree(repo / ".claude" / "skills" / "gone-skill")
    # 3. A hand-authored EXTERNAL skill in the (test-scoped) user skills dir.
    user_skills = tmp_path / "userhome" / ".claude" / "skills" / "my-external-tool"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text(
        "---\nname: my-external-tool\ndescription: A hand-authored skill.\n---\n\n# Do it\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_scan, "user_skills_root", lambda: user_skills.parent)
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


def test_gallery_shows_neurobase_and_external_skills(client: TestClient) -> None:
    html = client.get("/skills").text
    assert "nb-skill" in html  # Neurobase-managed, on disk
    assert "my-external-tool" in html  # hand-authored external skill
    assert "gone-skill" in html  # Neurobase skill missing on disk (drift)
    # source is labelled on each card
    assert ">neurobase<" in html and ">external<" in html


def test_external_skill_offers_no_revert(client: TestClient) -> None:
    html = client.get("/skills").text
    assert 'action="/skills/gone-skill/revert"' in html, "missing NB skill must offer revert"
    # neither the external skill nor the live NB skill exposes a revert form
    assert 'action="/skills/my-external-tool/revert"' not in html
    assert 'action="/skills/nb-skill/revert"' not in html


def test_revert_repairs_a_missing_skill(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    r = client.post(
        "/skills/gone-skill/revert",
        data={CSRF_FORM_FIELD: app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _read_status(store_root, "gone-skill") == "proposed"


def test_revert_of_a_live_skill_is_refused(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    """The ADR-0020 guard: a crafted, CSRF-valid POST must not revert a skill
    that is live on disk — revert_proposal 409s at write time."""
    r = client.post(
        "/skills/nb-skill/revert",
        data={CSRF_FORM_FIELD: app.state.csrf_token},
        headers={"origin": str(client.base_url)},
    )
    assert r.status_code == 409
    assert _read_status(store_root, "nb-skill") == "accepted", "a live skill was reverted"


def test_revert_without_csrf_is_rejected(client: TestClient, store_root: Path) -> None:
    r = client.post("/skills/gone-skill/revert", data={})
    assert r.status_code == 403
    assert _read_status(store_root, "gone-skill") == "accepted", "a CSRF-less POST mutated state"


def test_discover_skills_tags_managed_vs_external(store_root: Path) -> None:
    """The discovery module directly: the on-disk NB skill is tagged managed with
    its nb_slug; the external one is not."""
    from neurobase.core.store_handle import StoreMode, open_store

    found = {s.slug: s for s in skills_scan.discover_skills(open_store(store_root, StoreMode.READ))}
    assert found["nb-skill"].managed is True
    assert found["nb-skill"].nb_slug == "nb-skill"
    assert found["my-external-tool"].managed is False
    assert found["my-external-tool"].nb_slug is None
    assert "gone-skill" not in found  # removed from disk
