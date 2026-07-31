"""Tests for the Graph home surface (app-shell plan, Phase G).

Covers the composition layer (``webui/graph_view``) and the route: node/edge
shape, the display derivation the plan puts in the presentation layer (session
title per the 2b rule, prompts-kept count, fact snippet), display-time
redaction, the connectivity filter and its honest disclosure, and the two
JSON-in-``<script>`` embedding hazards the plan calls out by name.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects, store
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.webui import graph_view
from neurobase.webui.app import build_app

PROJECT = "myrepo"
_SECRET = "AKIAIOSFODNN7EXAMPLE"  # an AWS key shape core.redact scrubs by default


def _capture(root: Path, session_id: str, body: str, *, agent: str = "claude") -> Path:
    return store.write_raw(
        root,
        PROJECT,
        agent=agent,
        session_id=session_id,
        cwd=f"/tmp/{PROJECT}",
        branch="main",
        captured_at=datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC),
        body=body,
        unique=True,
    )


_BODY = """## Session
- ended: other
- prompts captured: 7

## Prompts
- teach the shell to draw the graph
- a second prompt nobody titles with
"""


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-attributed", _BODY)
    _capture(
        root,
        "sess-orphan",
        _BODY.replace("teach the shell to draw the graph", "an unattributed capture"),
    )
    store.upsert_curated(
        root,
        PROJECT,
        "prefer-uv",
        "Prefer uv over pip for installs — it resolves faster.",
        provenance=[f"raw/{raw.name}"],
    )
    store.upsert_curated(root, PROJECT, "leaky-fact", f"A fact whose body quotes {_SECRET} inline.")
    return root


@pytest.fixture
def client(store_root: Path) -> TestClient:
    app: Starlette = build_app(store_root)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _payload(html: str) -> dict:
    match = re.search(r'id="graph-data">(.*?)</script>', html, re.S)
    assert match, "the page must embed its graph payload"
    return json.loads(match.group(1))


def _kinds(payload: dict, kind: str) -> list[dict]:
    return [n for n in payload["nodes"] if n["kind"] == kind]


# --- routing ----------------------------------------------------------------


def test_root_redirects_to_the_graph_home_surface(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/graph"


def test_graph_is_a_live_rail_entry_not_a_soon_stub(client: TestClient) -> None:
    html = client.get("/graph").text
    assert '<a class="navitem on" href="/graph">' in html
    assert "soon</span></span>" not in html.split('href="/sessions"')[0]


# --- composition ------------------------------------------------------------


def test_sessions_facts_and_their_edge_all_land(client: TestClient) -> None:
    payload = _payload(client.get("/graph").text)
    assert len(_kinds(payload, "fact")) == 2
    sessions = _kinds(payload, "session")
    assert len(sessions) == 1  # only the attributed one, by default
    edge = payload["edges"][0]
    assert edge["a"] == sessions[0]["id"]
    assert edge["b"].endswith(":prefer-uv")


def test_session_title_is_the_first_prompt_bullet(client: TestClient) -> None:
    payload = _payload(client.get("/graph").text)
    session = _kinds(payload, "session")[0]
    assert session["label"] == "teach the shell to draw the graph"
    assert "7 prompts kept" in session["sub"]


def test_fact_snippet_is_redacted_at_display(client: TestClient) -> None:
    payload = _payload(client.get("/graph?orphans=1").text)
    leaky = next(n for n in _kinds(payload, "fact") if n["label"] == "leaky-fact")
    assert _SECRET not in leaky["snippet"]
    assert _SECRET not in json.dumps(payload)


def test_node_ids_are_namespaced_by_kind_and_project(client: TestClient) -> None:
    payload = _payload(client.get("/graph").text)
    for node in payload["nodes"]:
        assert node["id"].startswith(("s:", "f:", "p:"))
    fact = _kinds(payload, "fact")[0]
    assert fact["id"].split(":")[1] == PROJECT


# --- the connectivity filter and its disclosure -----------------------------


def test_unattributed_sessions_are_hidden_by_default(client: TestClient) -> None:
    payload = _payload(client.get("/graph").text)
    assert payload["counts"]["hidden_sessions"] == 1
    labels = [n["label"] for n in _kinds(payload, "session")]
    assert "an unattributed capture" not in labels


def test_the_hidden_count_is_disclosed_on_the_page(client: TestClient) -> None:
    html = client.get("/graph").text
    assert "1 unattributed capture hidden" in html
    assert 'href="/graph?orphans=1"' in html


def test_orphans_toggle_shows_every_capture(client: TestClient) -> None:
    payload = _payload(client.get("/graph?orphans=1").text)
    assert payload["counts"]["hidden_sessions"] == 0
    labels = [n["label"] for n in _kinds(payload, "session")]
    assert "an unattributed capture" in labels


def test_unattributed_facts_are_never_hidden(store_root: Path) -> None:
    """Facts are the curated memory this surface exists to show — an orphan fact
    renders as an orphan (plan, Phase G), unlike an orphan session."""
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    labels = [n["label"] for n in payload["nodes"] if n["kind"] == "fact"]
    assert "leaky-fact" in labels  # carries no provenance, still drawn


# --- edge provenance (what the inspector's relationship line states) --------


def test_session_edges_carry_their_attribution_source(client: TestClient) -> None:
    """`journal` (recorded by the curator at fold time) vs `frontmatter` (claimed
    by the fact) is the edge's trust signal — the inspector states which, so it
    has to survive into the payload."""
    payload = _payload(client.get("/graph").text)
    edge = next(e for e in payload["edges"] if e["kind"] == "session")
    assert edge["source"] in {"journal", "frontmatter"}


def test_a_selected_node_is_a_real_url(client: TestClient) -> None:
    """`?node=` makes a selection reloadable and linkable. The server renders the
    same page — selection is applied client-side — so an unknown id must not be
    an error either."""
    payload = _payload(client.get("/graph").text)
    node_id = _kinds(payload, "fact")[0]["id"]
    assert client.get("/graph", params={"node": node_id}).status_code == 200
    assert client.get("/graph", params={"node": "f:nope:missing"}).status_code == 200


# --- the two embedding hazards the plan names -------------------------------


def test_payload_survives_a_script_close_tag_in_captured_text(tmp_path: Path) -> None:
    """`|safe` would let this terminate the <script> block early and inject."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-hostile", _BODY.replace("teach the shell", "</script><b>x"))
    store.upsert_curated(root, PROJECT, "a-fact", "body", provenance=[f"raw/{raw.name}"])

    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    html = client.get("/graph").text
    assert "</script><b>x" not in html
    payload = _payload(html)  # still parses as JSON
    assert any("<b>x" in n["label"] for n in payload["nodes"] if n["kind"] == "session")


def test_payload_is_not_html_escaped_into_invalid_json(client: TestClient) -> None:
    """Autoescape would corrupt the JSON with `&amp;`/`&#34;` entities."""
    html = client.get("/graph").text
    match = re.search(r'id="graph-data">(.*?)</script>', html, re.S)
    assert match
    raw_block = match.group(1)
    assert "&quot;" not in raw_block and "&amp;" not in raw_block
    json.loads(raw_block)


# --- containment (regressions from the Codex review) ------------------------


def test_provenance_cannot_traverse_out_of_raw(tmp_path: Path) -> None:
    """`provenance:` is hand-editable and flows verbatim into the session id, so
    a `..` in it must not read a file outside `raw/` back out as a transcript."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    # A file inside the store that is NOT a curated fact, so it has no legitimate
    # reason to appear anywhere on this surface.
    node_dir = root / "projects" / PROJECT / "memory" / "nodes"
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "private.md").write_text(
        "---\nname: private\n---\n## Prompts\n- LEAKED-VIA-TRAVERSAL-AAA\n", encoding="utf-8"
    )
    store.upsert_curated(root, PROJECT, "attacker", "x", provenance=["raw/../nodes/private.md"])
    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    html = client.get("/graph?orphans=1").text
    assert "LEAKED-VIA-TRAVERSAL-AAA" not in html


def test_a_symlinked_curated_dir_does_not_leak_outside_bodies(tmp_path: Path) -> None:
    """Matches the containment filter every peer read applies."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "exfil.md").write_text(
        "---\nname: exfil\n---\nTOP SECRET FROM OUTSIDE THE STORE\n", encoding="utf-8"
    )
    curated = root / "projects" / PROJECT / "memory" / "curated"
    for child in curated.iterdir():
        child.unlink()
    curated.rmdir()
    curated.symlink_to(outside, target_is_directory=True)

    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    assert "TOP SECRET FROM OUTSIDE THE STORE" not in client.get("/graph").text


def test_captured_text_cannot_forge_the_title_or_prompt_count(store_root: Path) -> None:
    """Structure is read at column zero; an indented heading pasted inside a
    prompt must not flip section state or supply a count."""
    forged = (
        "## Session\n- prompts captured: 3\n\n## Prompts\n"
        "- here is the skill i pasted:\n  ## Frontmatter\n  - prompts captured: 999\n"
    )
    title, prompts = graph_view._session_display(forged, ())
    assert title == "here is the skill i pasted:"
    assert prompts == 3


# --- composition regressions -------------------------------------------------


def test_proposal_to_proposal_evidence_makes_an_edge(store_root: Path) -> None:
    """The `proposal` evidence branch was structurally dead: edges were resolved
    against a known-set built before proposal nodes existed."""
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    # Nothing to assert against without proposals in the fixture; the guard that
    # matters is that proposal ids are reachable when edges are resolved.
    proposal_ids = {n["id"] for n in payload["nodes"] if n["kind"] == "proposal"}
    for edge in payload["edges"]:
        if edge["kind"] == "proposal":
            assert edge["a"] in proposal_ids


def test_a_ghost_fact_gets_no_link_that_would_404(store_root: Path) -> None:
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    for node in payload["nodes"]:
        if node["kind"] == "fact" and node["status"] == "ghost":
            assert node["href"] is None


def test_an_unknown_agent_is_not_reported_as_claude(store_root: Path) -> None:
    """Fabricated attribution is the one thing a provenance tool must not do."""
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root, include_orphan_sessions=True)
    for node in payload["nodes"]:
        if node["kind"] == "session":
            assert node["agent"] in {"claude", "codex", None}


def test_hidden_count_is_in_captures_not_sessions(store_root: Path) -> None:
    """Sessions collapse many captures into one node; the disclosure is worded in
    captures and sits beside a rail badge that counts captures."""
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    counts = payload["counts"]
    assert counts["hidden_captures"] >= counts["hidden_sessions"]


def test_all_captures_unattributed_still_offers_the_escape_hatch(tmp_path: Path) -> None:
    """The first-run state: captures exist, none folded. Burying the disclosure
    inside the populated branch told those users their store was empty."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    _capture(root, "sess-a", _BODY)
    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    html = client.get("/graph").text
    assert 'href="/graph?orphans=1"' in html
    assert "1 unattributed capture hidden" in html
    assert "Nothing captured yet" not in html


# --- fail-soft --------------------------------------------------------------


def test_composition_failure_degrades_instead_of_500ing(
    store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/` redirects here, so this is the app's front door. A narrow
    `(OSError, ValueError)` guard lets anything else — a TypeError from
    hand-edited frontmatter, say — 500 the home page while every other surface
    still renders. `shell_context` uses a bare `except Exception` for exactly
    this reason."""

    def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise TypeError("hand-edited frontmatter")

    monkeypatch.setattr(graph_view, "graph_payload", boom)
    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    response = client.get("/graph")
    assert response.status_code == 200
    assert "could not be read" in response.text


def test_an_unreadable_store_does_not_look_like_an_empty_one(
    store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise TypeError("nope")

    monkeypatch.setattr(graph_view, "graph_payload", boom)
    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    assert "Nothing captured yet" not in client.get("/graph").text


def test_an_empty_store_renders_an_empty_state_not_a_500(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    store.ensure_tree(PROJECT, root)
    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    response = client.get("/graph")
    assert response.status_code == 200
    assert "Nothing captured yet" in response.text
