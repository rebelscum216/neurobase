"""Request-boundary schema guard for the web UI (Codex P1-SAFETY-SECURITY-002,
spec §10/D11).

The CLI validates the store once when it starts the long-running server, but a
store on disk can advance to a newer schema *after* that — another Neurobase
version writing to the same store. Every request must therefore re-open a
validated ``StoreHandle`` at its own boundary: GETs refuse/degrade without
reading forward-schema state (and never materialize ``store.toml`` on an absent
store), and mutating POSTs make no proposal/ledger change.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from neurobase.core import store
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.recommender import proposals
from neurobase.recommender.corpus import EvidenceRef
from neurobase.recommender.ranker import RankedCandidate, Scores
from neurobase.webui.app import build_app
from neurobase.webui.security import CSRF_FORM_FIELD


def _ranked(slug: str = "prefer-uv-run") -> RankedCandidate:
    return RankedCandidate(
        slug=slug,
        type="rule",
        candidate_type="repeated-instruction",
        title="Prefer uv run",
        rationale="Repeated correction",
        draft="Always use uv run.",
        target="AGENTS.md",
        project="neurobase",
        supersedes=[],
        evidence=[EvidenceRef.proposal("old-example")],
        scores=Scores(3, 2, 1.0, 6.0),
        sessions=2,
        agents=1,
        projects=1,
    )


def _store_with_rejected_proposal(root: Path) -> None:
    open_store(root, StoreMode.WRITE)  # creates store.toml at the current schema
    proposals.write_ranked(root, [_ranked()])
    proposals.reject_proposal(root, "prefer-uv-run")


def _bump_schema_beyond_support(root: Path) -> None:
    """Rewrite store.toml to a schema newer than this binary supports — the state
    a parallel newer-Neurobase write would leave, which the server started before."""
    store.store_toml_path(root).write_text(
        f"schema = {store.STORE_SCHEMA_VERSION + 1}\n", encoding="utf-8"
    )


def _client(root: Path) -> TestClient:
    return TestClient(build_app(root), base_url="http://127.0.0.1:8765")


def test_get_suggestions_refuses_a_forward_schema_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _store_with_rejected_proposal(root)
    client = _client(root)
    assert client.get("/suggestions").status_code == 200  # supported → normal

    _bump_schema_beyond_support(root)  # store advances under the running server
    r = client.get("/suggestions")
    assert r.status_code == 409, "a GET must refuse a forward-schema store, not 500"
    assert "newer than this binary" in r.text


def test_get_suggestion_detail_refuses_a_forward_schema_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _store_with_rejected_proposal(root)
    client = _client(root)
    _bump_schema_beyond_support(root)
    assert client.get("/suggestions/prefer-uv-run").status_code == 409


def test_reopen_post_makes_no_change_on_a_forward_schema_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _store_with_rejected_proposal(root)
    app = build_app(root)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    _bump_schema_beyond_support(root)

    r = client.post(
        "/suggestions/prefer-uv-run/reopen",
        data={CSRF_FORM_FIELD: app.state.csrf_token},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )
    assert r.status_code == 409, "a CSRF-valid mutation must be refused, not applied"
    # No forward-schema mutation: the proposal is still rejected and no `reopened`
    # event was appended. (Read it back with a DOCTOR handle that tolerates the
    # bumped schema — the point is that the WEB write path made no change.)
    events = [e["event"] for e in proposals.read_ledger(root)]
    assert events == ["proposed", "rejected"], "the ledger gained a forward-schema event"
    doc = store.read_doc(store.store_toml_path(root).parent / "proposals" / "prefer-uv-run.md")
    assert str(doc.get("status")) == "rejected"


def test_get_on_an_absent_store_does_not_materialize_store_toml(tmp_path: Path) -> None:
    root = tmp_path / "store"  # never initialized — no store.toml
    client = _client(root)
    r = client.get("/suggestions")
    assert r.status_code == 200, "an empty store is an empty queue, not an error"
    assert not store.store_toml_path(root).exists(), "a READ GET must not create store.toml"
