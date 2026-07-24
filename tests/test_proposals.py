"""Tests for the proposal store (spec §12.6/§12.1, workstream E) —
``recommender/proposals.py``.

Covers the workstream-E named tests that live on the store side:

- **stable ordering** — ``recommend list``'s sort contract (total desc,
  created_at asc, name asc);
- **rejected/accepted proposals are not silently reset to proposed**;
- **malformed proposal files skipped**;
- **a secret-shaped string in a candidate's draft is redacted before the
  proposal file is ever written**;
- **a proposal edited by the user is not silently overwritten by a subsequent
  run**.

Plus the Advisory supersede-transition rule and the ranker-side near-duplicate
re-check (§12.6) — added here for real coverage rather than left untested."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from neurobase.core import store
from neurobase.recommender import corpus, proposals
from neurobase.recommender.corpus import EvidenceRef, ledger_path, proposal_path
from neurobase.recommender.ranker import RankedCandidate, Scores

NOW = datetime(2026, 7, 10, tzinfo=UTC)


# --- helpers -----------------------------------------------------------------


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


def _read(root: Path, slug: str) -> store.Document:
    return store.read_doc(proposal_path(root, slug))


def _ledger_events(root: Path) -> list[dict[str, Any]]:
    path = ledger_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- named test: secret redacted before the proposal file is ever written -----


def test_secret_in_draft_redacted_before_write(tmp_path: Path) -> None:
    """Workstream E: 'a secret-shaped string in a candidate's draft is redacted
    before the proposal file is ever written'. The AWS-key-shaped token must not
    survive anywhere in the on-disk proposal."""
    root = tmp_path / "store"
    secret = "AKIAIOSFODNN7EXAMPLE"  # AWS-key shape: AKIA + 16 chars
    candidate = _ranked(draft=f"Deploy with the key {secret} configured.")

    proposals.write_ranked(root, [candidate], now=NOW)

    raw_text = proposal_path(root, "prefer-uv-run").read_text(encoding="utf-8")
    assert secret not in raw_text
    assert "[REDACTED:aws-key]" in raw_text


# --- named test: stable ordering ---------------------------------------------


def test_stable_ordering_total_then_created_then_name(tmp_path: Path) -> None:
    """Workstream E: 'stable ordering'. ``load_all_proposals`` sorts by total
    descending, then created_at ascending, then name ascending."""
    root = tmp_path / "store"
    # high total sorts first; the two 10.0-total proposals tie-break by
    # created_at (earlier first), and the two identical-created_at ones by name.
    proposals.write_ranked(
        root,
        [_ranked(slug="low-score", scores=Scores(1, 1, 1.0, 5.0))],
        now=NOW,
    )
    proposals.write_ranked(
        root,
        [_ranked(slug="high-score", scores=Scores(9, 9, 1.0, 81.0))],
        now=NOW,
    )
    proposals.write_ranked(
        root,
        [_ranked(slug="mid-late", scores=Scores(2, 5, 1.0, 10.0))],
        now=NOW + timedelta(hours=1),
    )
    proposals.write_ranked(
        root,
        [_ranked(slug="mid-early", scores=Scores(2, 5, 1.0, 10.0))],
        now=NOW,
    )

    order = [d.get("name") for d in proposals.load_all_proposals(root)]
    assert order == ["high-score", "mid-early", "mid-late", "low-score"]


# --- named test: decided proposals are not silently reset to proposed ---------


def test_accepted_proposal_not_reset_to_proposed(tmp_path: Path) -> None:
    """Workstream E: 'accepted proposals are not silently reset to proposed'. A
    later run over the same slug leaves an accepted proposal's status and body
    untouched."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    # Simulate an accept: flip status + set a marker body.
    doc = _read(root, "prefer-uv-run")
    fm = dict(doc.frontmatter)
    fm["status"] = "accepted"
    fm["installed_path"] = str(tmp_path / "AGENTS.md")  # absolute on every OS (§12.1)
    store.write_doc(proposal_path(root, "prefer-uv-run"), fm, "ACCEPTED BODY — do not touch")

    outcome = proposals.write_ranked(
        root, [_ranked(draft="a totally different draft")], now=NOW + timedelta(days=1)
    )

    after = _read(root, "prefer-uv-run")
    assert after.get("status") == "accepted"
    assert after.body == "ACCEPTED BODY — do not touch"
    assert outcome.skipped_decided == ["prefer-uv-run"]
    assert outcome.refreshed == []


def test_rejected_proposal_not_reset_to_proposed(tmp_path: Path) -> None:
    """A ``rejected`` proposal is likewise never reopened to ``proposed``."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    doc = _read(root, "prefer-uv-run")
    fm = dict(doc.frontmatter)
    fm["status"] = "rejected"
    store.write_doc(proposal_path(root, "prefer-uv-run"), fm, "REJECTED BODY")

    outcome = proposals.write_ranked(root, [_ranked()], now=NOW + timedelta(days=1))

    assert _read(root, "prefer-uv-run").get("status") == "rejected"
    assert outcome.skipped_decided == ["prefer-uv-run"]


def test_proposed_proposal_is_refreshed(tmp_path: Path) -> None:
    """A still-``proposed`` proposal IS refreshed by a later run: body/scores
    update, ``created_at`` is kept, ``updated_at`` bumped."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(scores=Scores(3, 4, 1.0, 12.0))], now=NOW)
    later = NOW + timedelta(days=1)

    outcome = proposals.write_ranked(root, [_ranked(scores=Scores(9, 9, 1.0, 81.0))], now=later)

    after = _read(root, "prefer-uv-run")
    assert outcome.refreshed == ["prefer-uv-run"]
    assert after.get("created_at") == "2026-07-10T00:00:00Z"  # kept
    assert after.get("updated_at") == "2026-07-11T00:00:00Z"  # bumped
    assert after.get("scores")["total"] == 81.0


# --- named test: malformed proposal files skipped ----------------------------


def test_malformed_proposal_files_skipped(tmp_path: Path) -> None:
    """Workstream E: 'malformed proposal files skipped'. A garbage file in
    ``proposals/`` is skipped by ``load_all_proposals`` and by ``load_proposal``,
    never fatal — the valid proposal still loads."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="good-one")], now=NOW)
    directory = corpus.proposals_dir(root)
    (directory / "broken.md").write_text("this file has no frontmatter block", encoding="utf-8")

    loaded = proposals.load_all_proposals(root)

    assert [d.get("name") for d in loaded] == ["good-one"]
    assert proposals.load_proposal(root, "broken") is None
    assert proposals.load_proposal(root, "good-one") is not None


def test_malformed_existing_same_slug_is_not_overwritten(tmp_path: Path) -> None:
    """A malformed existing proposal is preserved on ``recommend run`` rather
    than being mistaken for a missing proposal and silently replaced."""
    root = tmp_path / "store"
    path = proposal_path(root, "prefer-uv-run")
    path.parent.mkdir(parents=True)
    original = "this file has no frontmatter block"
    path.write_text(original, encoding="utf-8")

    outcome = proposals.write_ranked(root, [_ranked()], now=NOW)

    assert path.read_text(encoding="utf-8") == original
    assert outcome.skipped_malformed == ["prefer-uv-run"]
    assert outcome.created == []


def test_load_all_proposals_empty_when_no_dir(tmp_path: Path) -> None:
    """No ``proposals/`` directory at all ⇒ ``[]`` (fail-soft, never raises)."""
    assert proposals.load_all_proposals(tmp_path / "store") == []


def test_schema_invalid_proposals_are_skipped_on_load(tmp_path: Path) -> None:
    """F2: parseable-but-malformed proposals are skipped by both load paths — a
    ``name`` that doesn't match the filename slug (a path-traversal vector for the
    skill emitter) and an ``evidence`` that isn't a list of mappings (an
    AttributeError vector for ``recommend show``)."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="good-one")], now=NOW)
    directory = corpus.proposals_dir(root)
    # Frontmatter parses fine, but name != stem — a traversal-shaped name must
    # never be trusted as a path component downstream.
    (directory / "evil.md").write_text(
        "---\nname: ../../escape\nstatus: proposed\ntype: skill\nevidence: []\n---\n\nx\n",
        encoding="utf-8",
    )
    # evidence is a bare string, not a list of refs.
    (directory / "bad-evidence.md").write_text(
        "---\nname: bad-evidence\nstatus: proposed\ntype: rule\nevidence: broken\n---\n\ny\n",
        encoding="utf-8",
    )

    loaded = proposals.load_all_proposals(root)

    assert [d.get("name") for d in loaded] == ["good-one"]
    assert proposals.load_proposal(root, "evil") is None
    assert proposals.load_proposal(root, "bad-evidence") is None


@pytest.mark.parametrize(
    "field, bad_value",
    [
        # evidence: a non-string slug (from_frontmatter stringifies — must still
        # be rejected via the round-trip check).
        ("evidence", [{"kind": "proposal", "slug": 123}]),
        # evidence: a forbidden extra key on a proposal ref.
        ("evidence", [{"kind": "proposal", "slug": "x", "project": "forbidden"}]),
        # evidence: canonical-shaped but traversal-valued strings — one per kind.
        ("evidence", [{"kind": "proposal", "slug": "../bad"}]),
        ("evidence", [{"kind": "curated", "project": "../bad", "slug": "ok"}]),
        ("evidence", [{"kind": "raw", "project": "p", "file": "../escape.md"}]),
        # supersedes: a traversal-shaped, non-slug string.
        ("supersedes", ["../bad"]),
        # created_at: not an ISO8601 timestamp.
        ("created_at", "last tuesday"),
        # installed_path: a relative path (§12.1 requires absolute).
        ("installed_path", "relative/AGENTS.md"),
    ],
)
def test_nested_field_validation_rejects_malformed_values(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    """R3-1: each nested §12.1 field family is validated exactly — a malformed
    value in any one of them makes the whole proposal fail to load, rather than
    reaching a consumer or emitter."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="subject")], now=NOW)
    doc = _read(root, "subject")
    fm = dict(doc.frontmatter)
    fm[field] = bad_value
    store.write_doc(proposal_path(root, "subject"), fm, doc.body)

    assert proposals.load_proposal(root, "subject") is None
    assert proposals.load_all_proposals(root) == []


# --- named test: a user edit is not silently overwritten ----------------------


def test_user_edit_not_silently_overwritten(tmp_path: Path) -> None:
    """Workstream E: 'a proposal edited by the user is not silently overwritten
    by a subsequent run'. After an ``edited`` ledger event newer than our last
    ``proposed`` write, a fresh candidate for that slug preserves the edited
    body verbatim."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)

    # Simulate `recommend edit`: overwrite the body and append an `edited` event
    # newer than the `proposed` event the initial run recorded.
    doc = _read(root, "prefer-uv-run")
    store.write_doc(
        proposal_path(root, "prefer-uv-run"), dict(doc.frontmatter), "MY HAND-EDITED BODY"
    )
    with ledger_path(root).open("a", encoding="utf-8") as handle:
        edited_at = (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        handle.write(
            json.dumps({"at": edited_at, "slug": "prefer-uv-run", "event": "edited"}) + "\n"
        )

    outcome = proposals.write_ranked(
        root, [_ranked(draft="the miner's brand new draft")], now=NOW + timedelta(days=1)
    )

    assert outcome.preserved_edits == ["prefer-uv-run"]
    assert outcome.refreshed == []
    assert _read(root, "prefer-uv-run").body == "MY HAND-EDITED BODY"


def test_refresh_proceeds_when_edit_predates_last_proposed(tmp_path: Path) -> None:
    """An ``edited`` event OLDER than the most recent ``proposed`` write is not a
    pending edit — the refresh proceeds normally (the guard is time-ordered, not
    a permanent latch)."""
    root = tmp_path / "store"
    # Manually seed a ledger where an edit predates the latest proposed write.
    lp = ledger_path(root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"at": "2026-07-10T00:00:00Z", "slug": "prefer-uv-run", "event": "proposed"},
                {"at": "2026-07-10T00:01:00Z", "slug": "prefer-uv-run", "event": "edited"},
                {"at": "2026-07-10T00:02:00Z", "slug": "prefer-uv-run", "event": "proposed"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proposals.write_ranked(root, [_ranked()], now=NOW)  # creates the file fresh

    outcome = proposals.write_ranked(root, [_ranked()], now=NOW + timedelta(days=1))
    assert outcome.refreshed == ["prefer-uv-run"]
    assert outcome.preserved_edits == []


# --- Advisory: supersede only retires a still-proposed slug -------------------


def test_supersede_retires_only_a_still_proposed_slug(tmp_path: Path) -> None:
    """A candidate's explicit ``supersedes`` flips a still-``proposed`` prior
    slug to ``superseded`` and records the linkage; the new proposal is written."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="old-rule")], now=NOW)

    outcome = proposals.write_ranked(
        root, [_ranked(slug="new-rule", supersedes=["old-rule"])], now=NOW + timedelta(days=1)
    )

    assert _read(root, "old-rule").get("status") == "superseded"
    assert outcome.superseded == ["old-rule"]
    new_doc = _read(root, "new-rule")
    assert new_doc.get("status") == "proposed"
    assert new_doc.get("supersedes") == ["old-rule"]


def test_supersede_leaves_a_decided_slug_untouched(tmp_path: Path) -> None:
    """``supersedes`` can retire an undecided proposal but MUST NOT reach into a
    decided one — a named ``accepted`` slug is left exactly as it was, though the
    linkage is still recorded on the new proposal (§12.6)."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked(slug="old-rule")], now=NOW)
    doc = _read(root, "old-rule")
    fm = dict(doc.frontmatter)
    fm["status"] = "accepted"
    store.write_doc(proposal_path(root, "old-rule"), fm, "INSTALLED BODY")

    proposals.write_ranked(
        root, [_ranked(slug="new-rule", supersedes=["old-rule"])], now=NOW + timedelta(days=1)
    )

    old_after = _read(root, "old-rule")
    assert old_after.get("status") == "accepted"  # untouched
    assert old_after.body == "INSTALLED BODY"
    assert _read(root, "new-rule").get("supersedes") == ["old-rule"]  # linkage still recorded


# --- Advisory: ranker-side near-duplicate re-check ---------------------------


def test_near_duplicate_of_rejected_is_declined(tmp_path: Path) -> None:
    """A fresh candidate whose rendered body near-duplicates a still-``rejected``
    proposal is declined before a new ``proposed`` file is written (§12.6/D18) —
    belt-and-suspenders on the miner prompt."""
    root = tmp_path / "store"
    # Create then reject a proposal (file status + ledger event), so the corpus
    # ledger summary surfaces its body for the near-duplicate check.
    proposals.write_ranked(root, [_ranked(slug="declined-idea")], now=NOW)
    doc = _read(root, "declined-idea")
    fm = dict(doc.frontmatter)
    fm["status"] = "rejected"
    store.write_doc(proposal_path(root, "declined-idea"), fm, doc.body)
    with ledger_path(root).open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"at": "2026-07-10T01:00:00Z", "slug": "declined-idea", "event": "rejected"})
            + "\n"
        )

    # A NEW slug, but the same title/rationale/draft → identical rendered body →
    # Jaccard 1.0 ≥ threshold → declined.
    outcome = proposals.write_ranked(
        root, [_ranked(slug="fresh-slug")], now=NOW + timedelta(days=1)
    )

    assert outcome.declined == ["fresh-slug"]
    assert outcome.created == []
    assert proposals.load_proposal(root, "fresh-slug") is None


def test_new_proposal_is_created_and_logged(tmp_path: Path) -> None:
    """The happy path: a brand-new candidate writes a ``proposed`` file with the
    §12.1 frontmatter shape and appends one ``proposed`` ledger event carrying
    the candidate_type."""
    root = tmp_path / "store"
    outcome = proposals.write_ranked(root, [_ranked()], now=NOW)

    assert outcome.created == ["prefer-uv-run"]
    doc = _read(root, "prefer-uv-run")
    assert doc.get("name") == "prefer-uv-run"
    assert doc.get("status") == "proposed"
    assert doc.get("type") == "rule"
    assert doc.get("installed_path") is None
    assert doc.get("scores")["total"] == 25.8
    assert doc.get("evidence") == [
        {"kind": "raw", "project": "neurobase", "file": "2026-07-01T00-00-00Z_claude_ab12cd34.md"}
    ]
    events = _ledger_events(root)
    assert events == [
        {
            "at": "2026-07-10T00:00:00Z",
            "slug": "prefer-uv-run",
            "event": "proposed",
            "candidate_type": "repeated-instruction",
        }
    ]


def test_draft_markers_extract_verbatim_and_fail_closed_on_duplicates() -> None:
    candidate = _ranked(draft="# Exact draft\n\n- keep markdown")
    body = proposals.render_body(candidate)
    assert proposals.extract_draft(body) == "# Exact draft\n\n- keep markdown"
    assert proposals.extract_draft(body + proposals.DRAFT_START) is None
    assert proposals.replace_draft(body + proposals.DRAFT_END, "replacement") is None


# --- G2: revert an acceptance back to the review queue -----------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _accept(root: Path, slug: str = "prefer-uv-run", *, now: datetime = NOW) -> None:
    """Accept against a path that does not exist — `artifact_state` reads
    `missing`, the drift state revert exists to repair (ADR-0020)."""
    proposals.accept_proposal(
        root,
        slug,
        target="AGENTS.md",
        installed_path=Path("/repo/AGENTS.md"),
        installed_hash="deadbeef",
        now=now,
    )


def _accept_installed(
    root: Path,
    installed: Path,
    *,
    content: str = "managed rule block",
    installed_hash: str | None = "",
    slug: str = "prefer-uv-run",
    now: datetime = NOW,
) -> None:
    """Accept against a real file on disk. ``installed_hash=""`` (the default)
    means "hash of ``content``" — i.e. a healthy artifact; pass ``None`` to
    simulate a pre-ADR-0011 acceptance that recorded no hash at all."""
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(content, encoding="utf-8")
    proposals.accept_proposal(
        root,
        slug,
        target="AGENTS.md",
        installed_path=installed,
        installed_hash=_sha256(content) if installed_hash == "" else installed_hash,
        now=now,
    )


def test_revert_flips_accepted_to_proposed_and_clears_install(tmp_path: Path) -> None:
    """G2: revert returns an accepted proposal to `proposed`, clears the dangling
    `installed_path`, and appends exactly one `reverted` event while keeping the
    full proposed→accepted→reverted ledger history."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept(root, now=NOW)

    prior = proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=1))

    assert prior == "accepted"
    doc = _read(root, "prefer-uv-run")
    assert doc.get("status") == "proposed"
    assert doc.get("installed_path") is None
    events = [e["event"] for e in _ledger_events(root)]
    assert events == ["proposed", "accepted", "reverted"]


def test_revert_never_deletes_the_artifact_on_disk(tmp_path: Path) -> None:
    """G2: revert corrects the store record only — a modified installed file is
    left exactly where it is, with the user's edit intact (revert is not an
    uninstall, ADR-0020)."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("managed rule block", encoding="utf-8")
    proposals.write_ranked(root, [_ranked()], now=NOW)
    proposals.accept_proposal(
        root,
        "prefer-uv-run",
        target="AGENTS.md",
        installed_path=installed,
        installed_hash=_sha256("managed rule block"),
        now=NOW,
    )
    installed.write_text("hand-edited by the user", encoding="utf-8")

    proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=1))

    assert installed.exists()
    assert installed.read_text(encoding="utf-8") == "hand-edited by the user"


def test_revert_only_allowed_on_accepted(tmp_path: Path) -> None:
    """G2: revert is defined only on `accepted`; every other status is a hard,
    named-status error (mirrors the blocked-status rules of §12.7)."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)

    with pytest.raises(ValueError, match="only an accepted proposal"):
        proposals.revert_proposal(root, "prefer-uv-run")  # still proposed

    proposals.reject_proposal(root, "prefer-uv-run", now=NOW)
    with pytest.raises(ValueError, match="status is rejected"):
        proposals.revert_proposal(root, "prefer-uv-run")


def test_revert_missing_proposal_raises(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with pytest.raises(ValueError, match="not found or malformed"):
        proposals.revert_proposal(root, "does-not-exist")


def test_reverted_proposal_can_be_reaccepted(tmp_path: Path) -> None:
    """G2: a reverted proposal is a normal `proposed` one again — re-accepting it
    reinstalls and flips it back to `accepted`."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept(root, now=NOW)
    proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=1))

    _accept(root, now=NOW + timedelta(minutes=2))

    assert _read(root, "prefer-uv-run").get("status") == "accepted"


# --- ADR-0020 D39: revert is drift repair, never an un-accept ----------------


def test_revert_refuses_a_healthy_artifact(tmp_path: Path) -> None:
    """ADR-0020 D39 / review F1: an artifact still installed and byte-identical
    to what acceptance recorded is NOT revertable. Allowing it would let
    `accept → revert → reject` retire the proposal while leaving the managed
    file live on disk — the exact orphaning §12.7's blocked reject-on-accepted
    rule exists to prevent."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, now=NOW)

    with pytest.raises(ValueError, match="still installed and unchanged"):
        proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=1))

    doc = _read(root, "prefer-uv-run")
    assert doc.get("status") == "accepted"
    assert doc.get("installed_path") == str(installed)
    assert [e["event"] for e in _ledger_events(root)] == ["proposed", "accepted"]


def test_revert_refuses_an_unverifiable_legacy_acceptance(tmp_path: Path) -> None:
    """ADR-0020 D39: a present artifact whose acceptance predates
    `installed_hash` (ADR-0011) cannot be *proven* drifted, so revert fails
    closed rather than dropping ownership of a possibly-live file. Mirrors
    `metrics._survival_one`'s existence-only fallback for the same events."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, installed_hash=None, now=NOW)

    with pytest.raises(ValueError, match="predates content hashing"):
        proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=1))

    assert _read(root, "prefer-uv-run").get("status") == "accepted"


def test_revert_allows_a_missing_artifact(tmp_path: Path) -> None:
    """ADR-0020 D39: the original G2 drift — the user deleted the installed file
    by hand — is exactly what revert is for."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, now=NOW)
    installed.unlink()

    assert proposals.revert_proposal(root, "prefer-uv-run", now=NOW) == "accepted"

    assert _read(root, "prefer-uv-run").get("status") == "proposed"
    assert _ledger_events(root)[-1]["reason"] == proposals.ARTIFACT_MISSING


def test_revert_allows_a_modified_artifact(tmp_path: Path) -> None:
    """ADR-0020 D39: a file the user rewrote no longer matches the accept-time
    `installed_hash`, so the record is demonstrably false and repairable."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, now=NOW)
    installed.write_text("the user rewrote this", encoding="utf-8")

    proposals.revert_proposal(root, "prefer-uv-run", now=NOW)

    assert _read(root, "prefer-uv-run").get("status") == "proposed"
    assert _ledger_events(root)[-1]["reason"] == proposals.ARTIFACT_MODIFIED


def test_revert_allows_an_accepted_proposal_with_no_installed_path(tmp_path: Path) -> None:
    """ADR-0020 D39: `accepted` with no `installed_path` names no artifact to
    orphan, so the incoherent record is repairable rather than stuck."""
    root = tmp_path / "store"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept(root, now=NOW)
    doc = _read(root, "prefer-uv-run")
    frontmatter = dict(doc.frontmatter)
    frontmatter["installed_path"] = None
    store.write_doc(doc.file_path, frontmatter, doc.body)

    proposals.revert_proposal(root, "prefer-uv-run", now=NOW)

    assert _read(root, "prefer-uv-run").get("status") == "proposed"
    assert _ledger_events(root)[-1]["reason"] == proposals.ARTIFACT_UNRECORDED


def test_artifact_state_classifies_every_case(tmp_path: Path) -> None:
    """The classification `revert` and `metrics` both key on (ADR-0020)."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, now=NOW)

    doc = _read(root, "prefer-uv-run")
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_HEALTHY

    installed.write_text("changed", encoding="utf-8")
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_MODIFIED

    installed.unlink()
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_MISSING


def test_artifact_state_uses_the_latest_acceptance(tmp_path: Path) -> None:
    """A re-accepted proposal has more than one `accepted` event; the drift guard
    must compare against the newest hash, not the first one. Otherwise a
    re-accept that legitimately changed the artifact would read as `modified`
    forever, making a healthy artifact revertable again (review F1)."""
    root = tmp_path / "store"
    installed = tmp_path / "repo" / "AGENTS.md"
    proposals.write_ranked(root, [_ranked()], now=NOW)
    _accept_installed(root, installed, content="first render", now=NOW)
    _accept_installed(root, installed, content="second render", now=NOW + timedelta(minutes=5))

    doc = _read(root, "prefer-uv-run")
    assert proposals.artifact_state(root, doc, "prefer-uv-run") == proposals.ARTIFACT_HEALTHY
    with pytest.raises(ValueError, match="still installed and unchanged"):
        proposals.revert_proposal(root, "prefer-uv-run", now=NOW + timedelta(minutes=6))
