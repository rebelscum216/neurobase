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

import re
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
    # Loopback base_url — TestClient's default `testserver` Host authority is
    # (correctly) rejected by the §14 loopback-Host gate.
    return TestClient(app, base_url="http://127.0.0.1:8765")


_FINGERPRINT_RE = re.compile(r'name="fingerprint" value="([0-9a-f]{64})"')


def _preview_fingerprint(client: TestClient, slug: str) -> str:
    """Scrape the consent fingerprint from a live GET preview — the same
    hidden field a real browser form would submit."""
    response = client.get(f"/suggestions/{slug}/accept")
    assert response.status_code == 200
    match = _FINGERPRINT_RE.search(response.text)
    assert match is not None, "accept preview did not render a fingerprint field"
    return match.group(1)


# --- list ---------------------------------------------------------------------


def test_queue_defaults_to_pending_only(client: TestClient, seed: Seed) -> None:
    """The queue is a *review* queue: by default it shows only pending
    (proposed) proposals, so decided items don't clutter it — matching the rail
    badge, which also counts only pending. The filter chips reach the rest."""
    response = client.get("/suggestions")
    assert response.status_code == 200
    assert seed.proposed_slug in response.text
    assert seed.accepted_slug not in response.text  # accepted filtered out of default
    assert seed.rejected_slug not in response.text  # rejected too
    assert 'href="/suggestions?status=all"' in response.text  # filter chips present
    assert 'href="/suggestions?status=rejected"' in response.text


def test_status_filter_selects_the_asked_for_status(client: TestClient, seed: Seed) -> None:
    all_text = client.get("/suggestions", params={"status": "all"}).text
    assert seed.proposed_slug in all_text
    assert seed.accepted_slug in all_text
    assert seed.rejected_slug in all_text

    rejected_text = client.get("/suggestions", params={"status": "rejected"}).text
    assert seed.rejected_slug in rejected_text
    assert seed.proposed_slug not in rejected_text  # only rejected shown


def test_default_pane_shows_recommender_health(client: TestClient) -> None:
    # With nothing selected, the right pane is the metrics overview (no crash).
    assert "Recommender health" in client.get("/suggestions").text


def test_clicking_a_proposal_previews_it_inline(client: TestClient, seed: Seed) -> None:
    response = client.get(f"/suggestions/{seed.proposed_slug}")
    assert response.status_code == 200
    assert 'class="split"' in response.text  # two-pane, queue still on the left
    assert "Always use uv run." in response.text  # the draft in the right pane
    assert f"/suggestions/{seed.proposed_slug}?view=full" in response.text  # open-as-page
    # a proposed proposal offers the inline reject form
    assert f'action="/suggestions/{seed.proposed_slug}/reject"' in response.text


def test_suggestion_fragment_is_pane_only_and_keeps_csrf(client: TestClient, seed: Seed) -> None:
    """The ?fragment=1 swap must be pane-only AND still carry the CSRF token, or
    the inline reject form it contains would be unusable after a no-reload swap."""
    html = client.get(f"/suggestions/{seed.proposed_slug}", params={"fragment": "1"}).text
    assert "Always use uv run." in html
    assert "<html" not in html.lower(), "fragment must not carry the page shell"
    assert 'class="split"' not in html, "fragment must be the pane only"
    assert 'name="csrf_token"' in html, "fragment lost the CSRF token for its reject form"


def test_suggestion_open_as_page_is_standalone(client: TestClient, seed: Seed) -> None:
    html = client.get(f"/suggestions/{seed.proposed_slug}", params={"view": "full"}).text
    assert "Always use uv run." in html
    assert 'class="split"' not in html
    assert "page narrow" in html


# --- reconsider / reopen (ADR-0024) --------------------------------------------


def test_rejected_proposal_offers_reconsider_and_reopen_works(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    # a rejected proposal's detail shows the Reconsider form
    detail = client.get(f"/suggestions/{seed.rejected_slug}", params={"view": "full"}).text
    assert f'action="/suggestions/{seed.rejected_slug}/reopen"' in detail
    # and reopening it flips it back to proposed
    response = client.post(
        f"/suggestions/{seed.rejected_slug}/reopen",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    doc = proposals.load_proposal(seed.root, seed.rejected_slug)
    assert doc is not None
    assert doc.get("status") == "proposed"


def test_reopen_on_a_non_rejected_proposal_is_409(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/reopen",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": str(client.base_url)},
    )
    assert response.status_code == 409
    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    assert doc.get("status") == "proposed"  # untouched


def test_reopen_without_csrf_is_rejected_and_writes_nothing(client: TestClient, seed: Seed) -> None:
    response = client.post(f"/suggestions/{seed.rejected_slug}/reopen", data={})
    assert response.status_code == 403
    doc = proposals.load_proposal(seed.root, seed.rejected_slug)
    assert doc is not None
    assert doc.get("status") == "rejected"  # CSRF-less POST mutated nothing


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
        data={
            "csrf_token": app.state.csrf_token,
            "fingerprint": _preview_fingerprint(client, seed.proposed_slug),
        },
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


# --- accept (POST) — consent binds to the previewed diff (§14) -----------------


def _state_snapshot(seed: Seed) -> dict[str, object]:
    """Exact bytes of everything a refused commit must not touch: the target
    artifact, the backup tree, the proposal file, and the complete ledger. A
    409 that leaves any of these different from before is a contract bug even
    if the status/accepted-event spot checks would still pass."""
    from neurobase.recommender.corpus import ledger_path

    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    target = seed.repo / "AGENTS.md"
    backups = seed.root / "backups"
    ledger = ledger_path(seed.root)
    return {
        "target": target.read_bytes() if target.exists() else None,
        # Recursive relpath -> bytes, not just top-level names — a mutation
        # INSIDE an existing backup dir must fail this comparison too.
        "backups": {
            str(p.relative_to(backups)): p.read_bytes()
            for p in sorted(backups.rglob("*"))
            if p.is_file()
        }
        if backups.exists()
        else {},
        "proposal": doc.file_path.read_bytes(),
        "ledger": ledger.read_bytes() if ledger.exists() else b"",
    }


def test_accept_post_with_stale_fingerprint_returns_409_and_writes_nothing(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    # The P1-CORRECTNESS-002 shape: preview one draft, change the proposal,
    # submit the original form — the freshly prepared bytes were never
    # previewed, so the commit must refuse with a typed 409 and no side
    # effects (no backup, no artifact, no proposal mutation, no ledger event).
    stale = _preview_fingerprint(client, seed.proposed_slug)
    assert proposals.save_edited_draft(seed.root, seed.proposed_slug, "A different draft entirely.")
    before = _state_snapshot(seed)  # after the legitimate edit, before the POST
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={"csrf_token": app.state.csrf_token, "fingerprint": stale},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "changed after the diff" in response.text
    assert _state_snapshot(seed) == before
    # A fresh preview + commit still works after the drift refusal.
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={
            "csrf_token": app.state.csrf_token,
            "fingerprint": _preview_fingerprint(client, seed.proposed_slug),
        },
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "A different draft entirely." in (seed.repo / "AGENTS.md").read_text(encoding="utf-8")


def test_accept_post_with_stale_fingerprint_after_target_drift_returns_409(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    # The OTHER drift dimension: the proposal is untouched but the target
    # file's bytes changed after the preview — the previewed diff no longer
    # describes what the install would do, so the stale form must 409 with
    # zero side effects.
    target = seed.repo / "AGENTS.md"
    target.write_text("# Original target bytes\n", encoding="utf-8")
    stale = _preview_fingerprint(client, seed.proposed_slug)
    target.write_text("# Someone else touched the target\n", encoding="utf-8")
    before = _state_snapshot(seed)
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={"csrf_token": app.state.csrf_token, "fingerprint": stale},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert _state_snapshot(seed) == before


def test_accept_post_without_fingerprint_returns_409_and_writes_nothing(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    before = _state_snapshot(seed)
    response = client.post(
        f"/suggestions/{seed.proposed_slug}/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert _state_snapshot(seed) == before


def test_preview_fingerprint_encoding_is_injective(tmp_path: Path) -> None:
    # P1-CORRECTNESS-002 round-2 probe: artifact text may contain NUL, so a
    # NUL-delimited serialization collides — ("A\0B","C") vs ("A","B\0C").
    # The length-prefixed encoding must keep these distinct.
    from neurobase.core.store import Document
    from neurobase.recommender import emitters
    from neurobase.webui import routes as webui_routes

    doc = Document(frontmatter={}, body="", file_path=tmp_path / "p.md")

    def preview(before: str, after: str) -> install.InstallPreview:
        artifact = emitters.Artifact(
            path=tmp_path / "AGENTS.md", before=before, after=after, target="AGENTS.md"
        )
        return install.InstallPreview(doc=doc, artifact=artifact, already_up_to_date=False)

    assert webui_routes._preview_fingerprint(
        preview("A\0B", "C")
    ) != webui_routes._preview_fingerprint(preview("A", "B\0C"))


def test_preview_fingerprint_uses_resolved_path_identity(tmp_path: Path) -> None:
    # §14 names the RESOLVED path: two spellings of the same file must
    # fingerprint identically, and genuinely different paths must not.
    from neurobase.core.store import Document
    from neurobase.recommender import emitters
    from neurobase.webui import routes as webui_routes

    doc = Document(frontmatter={}, body="", file_path=tmp_path / "p.md")
    (tmp_path / "sub").mkdir()

    def fp(path: Path) -> str:
        artifact = emitters.Artifact(path=path, before="a", after="b", target="AGENTS.md")
        return webui_routes._preview_fingerprint(
            install.InstallPreview(doc=doc, artifact=artifact, already_up_to_date=False)
        )

    assert fp(tmp_path / "sub" / ".." / "AGENTS.md") == fp(tmp_path / "AGENTS.md")
    assert fp(tmp_path / "AGENTS.md") != fp(tmp_path / "sub" / "AGENTS.md")


def test_accept_post_when_already_up_to_date_is_a_no_op(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    # §14: a no-op install (before == after) short-circuits — no backup, no
    # artifact write, no second ledger event. The seed's accepted proposal is
    # already installed unchanged, so its fresh preview is up to date.
    backups_dir = seed.root / "backups"
    backups_before = sorted(backups_dir.iterdir()) if backups_dir.exists() else []
    accepted_before = [
        event
        for event in proposals.ledger_history(seed.root, seed.accepted_slug)
        if event["event"] == "accepted"
    ]

    preview = install.prepare_install(seed.root, seed.accepted_slug, target="project")
    assert preview.already_up_to_date
    from neurobase.webui import routes as webui_routes

    response = client.post(
        f"/suggestions/{seed.accepted_slug}/accept",
        data={
            "csrf_token": app.state.csrf_token,
            "target": "project",
            "fingerprint": webui_routes._preview_fingerprint(preview),
        },
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Already+up+to+date" in response.headers["location"]

    backups_after = sorted(backups_dir.iterdir()) if backups_dir.exists() else []
    assert backups_after == backups_before
    accepted_after = [
        event
        for event in proposals.ledger_history(seed.root, seed.accepted_slug)
        if event["event"] == "accepted"
    ]
    assert accepted_after == accepted_before


def test_accept_post_records_acceptance_when_installed_but_unrecorded(
    client: TestClient, app: Starlette, seed: Seed
) -> None:
    """ADR-0020 D44, the web UI's half of review F2: after a drift-repair
    `revert` the artifact can be back to exactly the rendered bytes, and the
    POST must then record acceptance rather than short-circuit on "already up
    to date" — which is what stranded the proposal as `proposed` with a live
    artifact before."""
    from neurobase.webui import routes as webui_routes

    doc = proposals.load_proposal(seed.root, seed.accepted_slug)
    assert doc is not None
    installed = Path(str(doc.get("installed_path")))
    rendered = installed.read_bytes()

    # Drift, repair the record, then restore the bytes: installed-but-unrecorded.
    # A skill is live while its file EXISTS (ADR-0020 D43 — liveness is not a
    # frontmatter-parsing question), so reaching the record-only state means
    # deleting it, reverting the now-true drift, then restoring the same bytes.
    installed.unlink()
    proposals.revert_proposal(seed.root, seed.accepted_slug)
    installed.write_bytes(rendered)
    assert proposals.load_proposal(seed.root, seed.accepted_slug).get("status") == "proposed"  # type: ignore[union-attr]

    backups_dir = seed.root / "backups"
    backups_before = sorted(backups_dir.iterdir()) if backups_dir.exists() else []
    preview = install.prepare_install(seed.root, seed.accepted_slug, target="project")
    assert preview.already_up_to_date and preview.records_acceptance

    response = client.post(
        f"/suggestions/{seed.accepted_slug}/accept",
        data={
            "csrf_token": app.state.csrf_token,
            "target": "project",
            "fingerprint": webui_routes._preview_fingerprint(preview),
        },
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "recorded+the+acceptance" in response.headers["location"]
    after = proposals.load_proposal(seed.root, seed.accepted_slug)
    assert after is not None and after.get("status") == "accepted"
    assert after.get("installed_path") == str(installed)
    # Recorded, never rewritten: identical bytes, no new backup.
    assert installed.read_bytes() == rendered
    assert (sorted(backups_dir.iterdir()) if backups_dir.exists() else []) == backups_before
    assert proposals.artifact_state(seed.root, after, seed.accepted_slug) == (
        proposals.ARTIFACT_LIVE
    )


def test_accept_post_returns_409_when_the_record_only_write_goes_stale(
    client: TestClient, app: Starlette, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1-DATA-INTEGRITY-004 (web surface): if either side of the preview
    drifts at the record-only mutation boundary, the shared service raises and the
    route must surface a 409 having recorded nothing — not a 500, and never a
    half-applied acceptance."""
    from neurobase.webui import routes as webui_routes

    doc = proposals.load_proposal(seed.root, seed.accepted_slug)
    assert doc is not None
    installed = Path(str(doc.get("installed_path")))
    rendered = installed.read_bytes()
    # A skill is live while its file EXISTS (ADR-0020 D43 — liveness is not a
    # frontmatter-parsing question), so reaching the record-only state means
    # deleting it, reverting the now-true drift, then restoring the same bytes.
    installed.unlink()
    proposals.revert_proposal(seed.root, seed.accepted_slug)
    installed.write_bytes(rendered)  # installed-but-unrecorded

    preview = install.prepare_install(seed.root, seed.accepted_slug, target="project")
    assert preview.records_acceptance is True

    def _stale(*_a: object, **_k: object) -> object:
        raise install.StaleProposalError(seed.accepted_slug)

    monkeypatch.setattr(webui_routes.install, "record_acceptance", _stale)
    response = client.post(
        f"/suggestions/{seed.accepted_slug}/accept",
        data={
            "csrf_token": app.state.csrf_token,
            "target": "project",
            "fingerprint": webui_routes._preview_fingerprint(preview),
        },
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )

    assert response.status_code == 409
    after = proposals.load_proposal(seed.root, seed.accepted_slug)
    assert after is not None and after.get("status") == "proposed"  # nothing recorded
    assert installed.read_bytes() == rendered


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


def _inject_secret_into_draft(seed: Seed) -> str:
    """Write a secret directly into the on-disk managed draft region — the
    legacy/hand-edited shape that bypassed every redacting write path
    (``save_edited_draft`` would redact it, which is exactly the point)."""
    doc = proposals.load_proposal(seed.root, seed.proposed_slug)
    assert doc is not None
    secret = "ghp_" + "A" * 36
    path = doc.file_path
    text = path.read_text(encoding="utf-8")
    assert "Always use uv run." in text
    path.write_text(
        text.replace("Always use uv run.", f"Always use uv run. token: {secret}"),
        encoding="utf-8",
    )
    return secret


def test_edit_get_redacts_legacy_draft_secrets(client: TestClient, seed: Seed) -> None:
    # §14/§12.8: display-time redaction on EVERY draft surface, including a
    # legacy/hand-edited proposal whose file was written outside the redacting
    # write paths.
    secret = _inject_secret_into_draft(seed)
    response = client.get(f"/suggestions/{seed.proposed_slug}/edit")
    assert response.status_code == 200
    assert secret not in response.text
    assert "[REDACTED:github-token]" in response.text


def test_detail_redacts_legacy_draft_secrets(client: TestClient, seed: Seed) -> None:
    secret = _inject_secret_into_draft(seed)
    response = client.get(f"/suggestions/{seed.proposed_slug}")
    assert response.status_code == 200
    assert secret not in response.text
    assert "[REDACTED:github-token]" in response.text


def test_accept_preview_redacts_legacy_draft_secrets(client: TestClient, seed: Seed) -> None:
    # The preview diff renders the emitter-rendered artifact — the emitter's
    # own configured redaction (§12.8 belt-and-suspenders) must hold on this
    # surface too.
    secret = _inject_secret_into_draft(seed)
    response = client.get(f"/suggestions/{seed.proposed_slug}/accept")
    assert response.status_code == 200
    assert secret not in response.text
    assert "[REDACTED:github-token]" in response.text
