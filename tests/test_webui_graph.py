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

from neurobase.core import graph as core_graph
from neurobase.core import projects, store
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.recommender.corpus import EvidenceRef, proposal_path
from neurobase.webui import graph_view
from neurobase.webui.app import build_app

PROJECT = "myrepo"
_SECRET = "AKIAIOSFODNN7EXAMPLE"  # an AWS key shape core.redact scrubs by default


def _proposal(
    root: Path,
    slug: str,
    *,
    kind: str = "skill",
    status: str = "proposed",
    evidence: list[EvidenceRef] | None = None,
    installed_path: str | None = None,
    body: str | None = None,
) -> Path:
    """One §12.1-valid proposal on disk.

    Written directly rather than through ``write_ranked``/``accept_proposal``
    because those install real artifacts; this surface only ever *reads* the
    proposal file, and the tests need statuses and types the write path would
    make expensive to reach. The shape is the validator's, so a drift in §12.1
    fails these tests rather than silently skipping every proposal —
    ``load_all_proposals`` drops anything malformed.
    """
    target = "AGENTS.md" if kind == "rule" else "user-skill"
    frontmatter: dict[str, object] = {
        "name": slug,
        "status": status,
        "type": kind,
        "target": target,
        "project": PROJECT,
        "candidate_type": "repeated-instruction",
        "scores": {"recurrence": 4, "breadth": 2, "recency": 0.9, "total": 12.5},
        "evidence": [ref.to_frontmatter() for ref in (evidence or [])],
        "supersedes": [],
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "installed_path": installed_path,
    }
    return store.write_doc(
        proposal_path(root, slug), frontmatter, body or f"Draft body for {slug}."
    )


def _node(payload: dict, node_id: str) -> dict:
    return next(n for n in payload["nodes"] if n["id"] == node_id)


def _capture(
    root: Path, session_id: str, body: str, *, agent: str = "claude", project: str = PROJECT
) -> Path:
    return store.write_raw(
        root,
        project,
        agent=agent,
        session_id=session_id,
        cwd=f"/tmp/{project}",
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


def test_javascript_object_keys_do_not_blank_the_canvas(tmp_path: Path) -> None:
    """Node ids become JS lookup keys. On a plain object, `byId["constructor"]`
    answers with an inherited function: the existence guard passes, the node is
    `undefined`, the camera goes NaN and the canvas stays blank for the life of
    the page. `SLUG_RE` (`^[a-z0-9-]+$`) admits `constructor`, `prototype` and
    `tostring` as real fact names, and `?node=` puts any of them in the URL."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    for dangerous in ("constructor", "prototype", "tostring"):
        store.upsert_curated(root, PROJECT, dangerous, f"a fact named {dangerous}")

    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    for dangerous in ("constructor", "prototype", "tostring"):
        response = client.get("/graph", params={"node": f"f:{PROJECT}:{dangerous}"})
        assert response.status_code == 200
        assert _node(_payload(response.text), f"f:{PROJECT}:{dangerous}")["kind"] == "fact"
    # Unknown-but-dangerous ids must be inert too — `?node=` is user-supplied.
    assert client.get("/graph", params={"node": "__proto__"}).status_code == 200


# The three source assertions that used to sit here are gone. They regexed
# `graph.html` for constants and stayed green while the complexity claim one of
# them named was false (Codex P2-TEST-GAP-006, round 2). The renderer now has a
# real behaviour suite that executes the shipped script — `tests/js/`, run by
# `node --test` as a required step in `scripts/ci.py`. The Python side keeps only
# what Python can actually observe: that the ids reach the page intact (above).


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
        "---\nname: private\nstatus: active\n---\n\n## Prompts\n- LEAKED-VIA-TRAVERSAL-AAA\n",
        encoding="utf-8",
    )
    store.upsert_curated(root, PROJECT, "attacker", "x", provenance=["raw/../nodes/private.md"])
    client = TestClient(build_app(root), base_url="http://127.0.0.1:8765")
    html = client.get("/graph?orphans=1").text
    assert "LEAKED-VIA-TRAVERSAL-AAA" not in html


def _swap_for_symlink(store_root: Path, name: str, target: Path) -> None:
    """Replace a project store directory with a symlink to ``target``."""
    directory = store_root / "projects" / PROJECT / "memory" / name
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()
    directory.symlink_to(target, target_is_directory=True)


def test_a_symlinked_curated_dir_leaks_neither_body_nor_frontmatter(tmp_path: Path) -> None:
    """Containment is the read-side perimeter, and it has to run *before* any
    field is read — not just before the body is.

    The original test asserted only that the body was absent, and passed while the
    external document's `name` and `updated_at` became a real fact node in the
    embedded payload: frontmatter can itself carry secrets, and it is what decides
    graph identity and provenance (Codex P2-SAFETY-SECURITY-001).
    """
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    # A legitimate in-store capture, so the page still renders a payload and this
    # cannot pass merely by rendering the empty state.
    _capture(root, "sess-legit", _BODY)
    outside = tmp_path / "outside"
    outside.mkdir()
    # `status: active` and the blank line after the closing `---` are both load
    # bearing: `list_curated` filters on the former and `store._DOC_RE` demands the
    # latter, so a fixture missing either is skipped before containment is ever
    # consulted — which is what made the original version of this test pass
    # against a store with no containment at all.
    (outside / "exfil.md").write_text(
        "---\nname: EXTERNAL-FACT-NAME\nstatus: active\nupdated_at: '2031-01-01T00:00:00Z'\n"
        "provenance:\n  - raw/EXTERNAL-PROVENANCE.md\n---\n\nTOP SECRET FROM OUTSIDE THE STORE\n",
        encoding="utf-8",
    )
    _swap_for_symlink(root, "curated", outside)

    html = (
        TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1").text
    )
    assert "TOP SECRET FROM OUTSIDE THE STORE" not in html  # the body, as before
    assert "EXTERNAL-FACT-NAME" not in html  # ...and the identity it forged
    assert "2031-01-01" not in html
    assert "EXTERNAL-PROVENANCE" not in html  # ...and the edges it minted
    payload = _payload(html)
    assert _kinds(payload, "fact") == []
    assert len(_kinds(payload, "session")) == 1  # the real capture is still drawn


def test_a_symlinked_raw_dir_leaks_no_session_identity(tmp_path: Path) -> None:
    """The same hole on the other enumeration: an external capture's `agent` and
    `session_id` became a SessionNode, which is an invented conversation in a
    provenance tool."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    # A legitimate curated fact keeps the payload non-empty, so this cannot pass
    # by rendering the empty state.
    store.upsert_curated(root, PROJECT, "in-store-fact", "a real fact")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "2026-07-01T09-00-00Z_claude_deadbeef.md").write_text(
        "---\nsession_id: EXTERNAL-SESSION-ID\nagent: EXTERNAL-METADATA-LEAK\n"
        "captured_at: '2031-01-01T00:00:00Z'\n---\n\n## Prompts\n- EXTERNAL-PROMPT-BODY\n",
        encoding="utf-8",
    )
    _swap_for_symlink(root, "raw", outside)

    html = (
        TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1").text
    )
    for secret in ("EXTERNAL-SESSION-ID", "EXTERNAL-METADATA-LEAK", "EXTERNAL-PROMPT-BODY"):
        assert secret not in html
    payload = _payload(html)
    assert _kinds(payload, "session") == []
    assert len(_kinds(payload, "fact")) == 1  # the real fact is still drawn


def test_a_symlinked_curator_journal_mints_no_session_or_edge(tmp_path: Path) -> None:
    """The journal is a **fourth** enumeration channel, and the worst of the four:
    it needs no external file to point at. Its `consumed` entries become resolved
    `SessionNode` identity directly, and its `edges` mint **journal**-sourced
    attribution — the highest-trust edge the graph draws, the one the inspector
    labels as recorded-by-the-curator (Codex P2-SAFETY-SECURITY-001, round 2).
    Round 2's fix skipped it on an "inside the store by construction" argument
    that a symlink disproves."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    store.upsert_curated(root, PROJECT, "real-fact", "an ordinary in-store fact")

    outside = tmp_path / "outside-journal.jsonl"
    outside.write_text(
        json.dumps(
            {
                "fold": {
                    "consumed": [
                        {
                            "file": "2026-07-01T09-00-00Z_claude_deadbeef.md",
                            "session_id": "EXTERNAL-JOURNAL-SID",
                            "agent": "EXTERNAL-JOURNAL-AGENT",
                            "captured_at": "2031-01-01T00:00:00Z",
                        }
                    ],
                    "edges": {"real-fact": ["2026-07-01T09-00-00Z_claude_deadbeef.md"]},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    journal = root / "projects" / PROJECT / "memory" / store.CURATOR_LOG
    journal.unlink(missing_ok=True)
    journal.symlink_to(outside)

    html = (
        TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1").text
    )
    for secret in ("EXTERNAL-JOURNAL-SID", "EXTERNAL-JOURNAL-AGENT", "2031-01-01"):
        assert secret not in html
    payload = _payload(html)
    assert _kinds(payload, "session") == []
    assert payload["edges"] == [], "an out-of-store journal must mint no attribution"
    assert len(_kinds(payload, "fact")) == 1  # the real fact still renders


def test_an_ordinary_journal_still_supplies_its_edges(tmp_path: Path) -> None:
    """The other half — the containment check must not cost Slice A its journal."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-folded", _BODY)
    store.upsert_curated(root, PROJECT, "folded", "x")
    journal = root / "projects" / PROJECT / "memory" / store.CURATOR_LOG
    journal.write_text(
        json.dumps({"fold": {"edges": {"folded": [raw.name]}}}) + "\n", encoding="utf-8"
    )

    payload = graph_view.graph_payload(open_store(root, StoreMode.READ), root)
    edge = next(e for e in payload["edges"] if e["kind"] == "session")
    assert edge["source"] == "journal"


def _contained_store_with_one_real_edge(tmp_path: Path) -> Path:
    """A store whose graph is non-empty *and* has a real edge.

    Every containment test needs this: with no in-store nodes the connectivity
    filter renders the empty state, and an assertion that a secret is absent
    passes against a page that drew nothing at all — the vacuity mode that let
    the first symlink test through with no containment in the code.
    """
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-real", _BODY)
    store.upsert_curated(root, PROJECT, "prefer-uv", "a real fact", provenance=[f"raw/{raw.name}"])
    return root


def test_a_symlinked_proposals_dir_mints_no_proposal_identity(tmp_path: Path) -> None:
    """Proposals are the **fifth** document-identity channel, and the one round 2
    missed: `load_all_proposals` globs `<root>/proposals/*.md` and proves nothing
    about containment, so a symlinked `proposals/` renders an external document's
    slug, body, project, and a curated-evidence edge on the 200 page (Codex
    P2-SAFETY-SECURITY-008)."""
    root = _contained_store_with_one_real_edge(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    # Written through the same §12.1 writer the real proposals use, so this
    # fixture cannot pass by being silently rejected as malformed — it is a valid
    # proposal in every respect except where it lives.
    _proposal(
        outside,
        "external-proposal",
        evidence=[EvidenceRef.curated(PROJECT, "prefer-uv")],
        body="TOP SECRET PROPOSAL BODY",
    )
    (root / "proposals").symlink_to(outside / "proposals", target_is_directory=True)

    html = (
        TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1").text
    )
    assert "TOP SECRET PROPOSAL BODY" not in html  # the snippet
    assert "external-proposal" not in html  # ...the identity it forged
    payload = _payload(html)
    assert _kinds(payload, "proposal") == []
    assert not [e for e in payload["edges"] if e["kind"] == "curated"], (
        "an out-of-store proposal must mint no evidence edge"
    )
    # The real graph still renders — this cannot pass on the empty state.
    assert len(_kinds(payload, "fact")) == 1
    assert len(_kinds(payload, "session")) == 1


def test_an_individually_symlinked_proposal_is_contained_but_a_real_one_is_not(
    tmp_path: Path,
) -> None:
    """The directory is not the only lever: one symlinked `*.md` inside a genuine
    `proposals/` reaches the same glob. The in-store proposal beside it is the
    positive control — containment must cost the external file everything and the
    ordinary one nothing."""
    root = _contained_store_with_one_real_edge(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    external = _proposal(
        outside,
        "external-proposal",
        evidence=[EvidenceRef.curated(PROJECT, "prefer-uv")],
        body="TOP SECRET PROPOSAL BODY",
    )
    _proposal(root, "in-store-proposal", evidence=[EvidenceRef.curated(PROJECT, "prefer-uv")])
    # The link MUST be named for the proposal's own `name`: §12.1 validation
    # rejects any document whose `name` differs from its file stem, so a
    # `smuggled.md` pointing at `external-proposal` is dropped as *malformed*
    # before containment is ever consulted — a fixture that passes this test with
    # the guard deleted. Mutation caught exactly that.
    (root / "proposals" / "external-proposal.md").symlink_to(external)

    html = (
        TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1").text
    )
    assert "TOP SECRET PROPOSAL BODY" not in html
    assert "external-proposal" not in html
    payload = _payload(html)
    assert [n["label"] for n in _kinds(payload, "proposal")] == ["in-store-proposal"]
    assert ("curated", f"f:{PROJECT}:prefer-uv", "p:in-store-proposal") in {
        (e["kind"], e["a"], e["b"]) for e in payload["edges"]
    }, "the ordinary proposal keeps its evidence edge"


def _unregistered_project_with_data(root: Path, slug: str, marker: str) -> None:
    """A project that exists on disk in the store but was never registered.

    Only a registry can select it, which is what makes it the probe for
    P2-SAFETY-SECURITY-010: if its `marker` reaches the page, an external file
    chose the namespace.
    """
    store.ensure_tree(slug, root)
    raw = _capture(root, "sess-" + slug, _BODY.replace("teach the shell", marker), project=slug)
    # The slug must satisfy `store.SLUG_RE`; the loud marker goes in the body,
    # which is what a leak would actually render.
    store.upsert_curated(
        root,
        slug,
        "shadow-fact",
        f"a fact only {slug} has: {marker}",
        provenance=[f"raw/{raw.name}"],
    )


def test_a_symlinked_registry_selects_no_project(tmp_path: Path) -> None:
    """The registry is the **sixth** identity channel, and the only one that is a
    *selector* rather than a document: it decides which project namespaces the
    all-projects sweep walks at all. Containing the five document enumerations
    while trusting an external selector leaves the boundary incomplete — Codex's
    probe had an external registry name an otherwise-unregistered in-store project
    and the graph emitted its session, fact and edge (P2-SAFETY-SECURITY-010)."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    _capture(root, "sess-real", _BODY)
    _unregistered_project_with_data(root, "zz-external-namespace", "EXTERNALLY-SELECTED-NAMESPACE")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "registry.toml").write_text(
        '[projects.zz-external-namespace]\nroots = ["/tmp/zz-external-namespace"]\n',
        encoding="utf-8",
    )
    registry = root / "registry.toml"
    registry.unlink()
    registry.symlink_to(outside / "registry.toml")

    response = TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph?orphans=1")
    assert response.status_code == 200, "an out-of-store registry must not 500 the home page"
    assert "EXTERNALLY-SELECTED-NAMESPACE" not in response.text
    assert "zz-external-namespace" not in response.text

    # Asserted on the composed payload, not the HTML: with no project selected the
    # template renders the empty state and embeds no payload script at all, so
    # scraping the page could not tell "selected nothing" from "rendered nothing".
    payload = graph_view.graph_payload(open_store(root, StoreMode.READ), root)
    assert payload["projects"] == [], "an out-of-store registry selects nothing"
    assert payload["nodes"] == []


def test_an_ordinary_registry_still_drives_the_sweep(tmp_path: Path) -> None:
    """The other half — containment must not cost a legitimate store its projects."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-real", _BODY)
    store.upsert_curated(root, PROJECT, "prefer-uv", "a real fact", provenance=[f"raw/{raw.name}"])

    handle = open_store(root, StoreMode.READ)
    assert PROJECT in handle.load_registry()
    payload = graph_view.graph_payload(handle, root)
    assert payload["projects"] == [PROJECT]
    assert len(_kinds(payload, "fact")) == 1
    assert len(_kinds(payload, "session")) == 1


def test_a_store_root_reached_through_a_symlink_keeps_its_registry(tmp_path: Path) -> None:
    """`contains()` resolves BOTH sides, so a store whose own root is a symlink
    (the macOS `/var` → `/private/var` case) must be unaffected. Filed because the
    010 fix is the first containment check applied to a path built from
    `self.root` itself rather than to a document under it — the failure mode is
    refusing an entire legitimate store, not leaking one."""
    real = tmp_path / "real-store"
    store.ensure_tree(PROJECT, real)
    projects.register_project(real, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(real, "sess-real", _BODY)
    store.upsert_curated(real, PROJECT, "prefer-uv", "a real fact", provenance=[f"raw/{raw.name}"])
    _proposal(real, "in-store-proposal", evidence=[EvidenceRef.curated(PROJECT, "prefer-uv")])

    via_link = tmp_path / "via-link"
    via_link.symlink_to(real, target_is_directory=True)

    handle = open_store(via_link, StoreMode.READ)
    assert PROJECT in handle.load_registry(), "a symlinked store root must keep its registry"
    payload = graph_view.graph_payload(handle, via_link)
    assert payload["projects"] == [PROJECT]
    assert [n["label"] for n in _kinds(payload, "proposal")] == ["in-store-proposal"]


def test_registry_containment_holds_at_the_handle_boundary(tmp_path: Path) -> None:
    """Filed at the layer the fix lives in. Twelve call sites reach
    `handle.load_registry()` — search, enable, MCP, CLI, corpus, the emitters, the
    app shell and four routes — and `_artifact_state` reopens the store, so a
    graph-only guard would be bypassed by the liveness path alone."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "registry.toml").write_text(
        '[projects.zz-external-namespace]\nroots = ["/tmp/zz-external-namespace"]\n',
        encoding="utf-8",
    )
    registry = root / "registry.toml"
    registry.unlink()
    registry.symlink_to(outside / "registry.toml")

    assert open_store(root, StoreMode.READ).load_registry() == {}


def test_containment_holds_at_the_core_graph_boundary(tmp_path: Path) -> None:
    """Filed at the layer the fix lives in: every caller of `memory_graph` — the
    graph page, and anything built on it later — gets contained nodes, rather
    than each having to re-filter."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "exfil.md").write_text(
        "---\nname: external-fact\nstatus: active\n---\n\nbody\n", encoding="utf-8"
    )
    _swap_for_symlink(root, "curated", outside)

    memory = core_graph.memory_graph(open_store(root, StoreMode.READ))
    assert memory.facts == ()


def test_one_symlink_loop_capture_does_not_blank_the_whole_graph(tmp_path: Path) -> None:
    """`Path.resolve()` raises `RuntimeError` — not `OSError` — on a
    self-referential symlink. Letting it escape handed the route's
    whole-composition guard a reason to degrade the entire home page over one
    corrupt filesystem entry (Codex P2-REGRESSION-002)."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-ok", _BODY)
    store.upsert_curated(root, PROJECT, "healthy", "a normal fact", provenance=[f"raw/{raw.name}"])

    loop = root / "projects" / PROJECT / "memory" / "raw" / "loop.md"
    loop.symlink_to("loop.md")
    store.upsert_curated(root, PROJECT, "cites-the-loop", "x", provenance=["raw/loop.md"])

    response = TestClient(build_app(root), base_url="http://127.0.0.1:8765").get("/graph")
    assert response.status_code == 200
    assert "could not be read" not in response.text, "one bad file must not degrade the page"
    labels = [n["label"] for n in _payload(response.text)["nodes"]]
    assert "healthy" in labels and "cites-the-loop" in labels
    assert "teach the shell to draw the graph" in labels  # the good session survived


def test_journal_attribution_beats_the_facts_own_claim(tmp_path: Path) -> None:
    """`journal` (the curator recorded it at fold time, per-pass validated) vs
    `frontmatter` (the document claims it) is the edge's *trust* signal, and the
    inspector prints which. The old test accepted either value without ever
    creating a journal edge, so it could not tell them apart."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-folded", _BODY)
    store.upsert_curated(root, PROJECT, "folded", "x", provenance=[f"raw/{raw.name}"])
    journal = root / "projects" / PROJECT / "memory" / store.CURATOR_LOG
    journal.write_text(
        json.dumps({"fold": {"edges": {"folded": [raw.name]}}}) + "\n", encoding="utf-8"
    )

    handle = open_store(root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, root)
    edge = next(e for e in payload["edges"] if e["kind"] == "session")
    assert edge["source"] == "journal", "the validated record must win over the claim"


def test_frontmatter_only_attribution_says_so(tmp_path: Path) -> None:
    """The other half of the pair — otherwise "journal" could be hardcoded."""
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    raw = _capture(root, "sess-claimed", _BODY)
    store.upsert_curated(root, PROJECT, "claimed", "x", provenance=[f"raw/{raw.name}"])

    payload = graph_view.graph_payload(open_store(root, StoreMode.READ), root)
    edge = next(e for e in payload["edges"] if e["kind"] == "session")
    assert edge["source"] == "frontmatter"


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


def test_all_three_evidence_kinds_map_to_their_own_edge(store_root: Path) -> None:
    """`curated` → fact→proposal, `raw` → session→proposal, `proposal` →
    proposal→proposal. The `proposal` branch was structurally dead once (edges
    resolved against a known-set built before proposal nodes existed), and the
    test that was supposed to guard it created no proposals at all, so it looped
    over nothing and passed against a deleted branch (Codex P2-TEST-GAP-006)."""
    handle = open_store(store_root, StoreMode.READ)
    raw_name = next(iter(handle.list_raw(PROJECT, unconsumed_only=False))).file_path.name
    _proposal(store_root, "cites-a-fact", evidence=[EvidenceRef.curated(PROJECT, "prefer-uv")])
    _proposal(store_root, "cites-a-capture", evidence=[EvidenceRef.raw(PROJECT, raw_name)])
    _proposal(store_root, "cites-a-proposal", evidence=[EvidenceRef.proposal("cites-a-fact")])

    payload = graph_view.graph_payload(handle, store_root)
    by_kind = {(e["kind"], e["a"], e["b"]) for e in payload["edges"]}
    session_id = _kinds(payload, "session")[0]["id"]

    assert ("curated", f"f:{PROJECT}:prefer-uv", "p:cites-a-fact") in by_kind
    assert ("raw", session_id, "p:cites-a-capture") in by_kind
    assert ("proposal", "p:cites-a-fact", "p:cites-a-proposal") in by_kind


def test_a_proposal_citing_itself_makes_no_self_loop(store_root: Path) -> None:
    """A self-edge would draw a line from a node to itself and put the node in
    its own neighbour list — an endless "connected to itself" card."""
    _proposal(store_root, "narcissus", evidence=[EvidenceRef.proposal("narcissus")])
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    assert not [e for e in payload["edges"] if e["a"] == e["b"]]


def test_evidence_naming_a_node_that_does_not_exist_draws_no_edge(store_root: Path) -> None:
    """Evidence outlives its target: a pruned fact or a capture from a project no
    longer registered would otherwise draw a line to nowhere."""
    _proposal(store_root, "stale", evidence=[EvidenceRef.curated(PROJECT, "long-pruned")])
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["a"] in node_ids and edge["b"] in node_ids


def test_a_ghost_fact_gets_no_link_that_would_404(store_root: Path) -> None:
    """A ghost is referenced by an edge but present in neither `curated/` nor
    `.tombstones/`, so `/memory/...` — which reads `curated/` only — 404s. The
    fixture must actually *contain* one: the old test iterated a graph with zero
    ghosts and asserted nothing (Codex P2-TEST-GAP-006)."""
    store.upsert_curated(
        store_root, PROJECT, "successor", "the new fact", supersedes=["never-existed"]
    )
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    ghost = _node(payload, f"f:{PROJECT}:never-existed")
    assert ghost["status"] == "ghost"
    assert ghost["href"] is None
    # ...and the real one beside it still links, so this is not vacuously true.
    assert _node(payload, f"f:{PROJECT}:successor")["href"] == f"/memory/{PROJECT}/successor"


def test_a_tombstoned_fact_gets_no_link_that_would_404(store_root: Path) -> None:
    """The fresh-supersession case `include_tombstoned=True` exists to show:
    `/memory/<project>/<slug>` reads `curated/` only, so the tombstone's CTA was
    a guaranteed 404 (Codex P2-UX-API-CONTRACT-003)."""
    store.soft_delete_curated(store_root, PROJECT, "prefer-uv")
    store.upsert_curated(store_root, PROJECT, "prefer-uv-run", "newer", supersedes=["prefer-uv"])
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)

    tomb = _node(payload, f"f:{PROJECT}:prefer-uv")
    assert tomb["status"] == "tombstoned" and tomb["tombstoned"] is True
    assert tomb["href"] is None, "a tombstone's Memory link always 404s"
    # The edge it was included *for* must still exist, and the live fact still links.
    assert {"a": f"f:{PROJECT}:prefer-uv", "b": f"f:{PROJECT}:prefer-uv-run"}.items() <= next(
        e for e in payload["edges"] if e["kind"] == "supersedes"
    ).items()
    assert _node(payload, f"f:{PROJECT}:prefer-uv-run")["href"] is not None


def test_a_tombstoned_facts_memory_route_really_does_404(client: TestClient, store_root: Path):
    """Pins the premise of the test above rather than assuming it: if Memory ever
    grows a tombstone view, this fails and the href rule should be revisited."""
    store.soft_delete_curated(store_root, PROJECT, "prefer-uv")
    assert client.get(f"/memory/{PROJECT}/prefer-uv").status_code == 404


def test_an_unknown_agent_is_not_reported_as_claude(store_root: Path) -> None:
    """Fabricated attribution is the one thing a provenance tool must not do. The
    fixture has to contain a session whose agent is genuinely unknown — the old
    test asserted a membership every session in a Claude-only fixture passed."""
    raw_dir = store_root / "projects" / PROJECT / "memory" / "raw"
    # A filename that fails the `{ts}_{agent}_{sid8}.md` grammar: cited by a fact,
    # so it becomes a skeleton session with no recoverable agent.
    (raw_dir / "handwritten-note.md").write_text(
        "---\nagent: null\n---\n\nbody\n", encoding="utf-8"
    )
    store.upsert_curated(
        store_root, PROJECT, "from-nowhere", "x", provenance=["raw/handwritten-note.md"]
    )
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root, include_orphan_sessions=True)

    unknown = _node(payload, f"s:{PROJECT}:handwritten-note.md")
    assert unknown["agent"] is None
    assert "unknown agent" in unknown["sub"]
    assert "Claude" not in unknown["sub"]


def test_hidden_count_is_in_captures_not_sessions(store_root: Path) -> None:
    """Sessions collapse many captures into one node, so a disclosure worded in
    captures must count captures. The old fixture had one capture per session,
    where the two numbers are equal and `>=` proves nothing."""
    for _ in range(3):
        _capture(store_root, "sess-chatty", _BODY.replace("teach the shell", "another turn"))
    handle = open_store(store_root, StoreMode.READ)
    counts = graph_view.graph_payload(handle, store_root)["counts"]
    # sess-orphan (1 capture) + sess-chatty (3) are unattributed → 2 sessions, 4 captures.
    assert counts["hidden_sessions"] == 2
    assert counts["hidden_captures"] == 4


# --- proposal nodes tell the truth about themselves (P2-CORRECTNESS-004) -----


def test_a_proposed_rule_is_not_called_a_skill(store_root: Path) -> None:
    """§12.1's `type` is `skill` **or** `rule`; a rule targets AGENTS.md /
    CLAUDE.md. The canvas called every proposal a skill."""
    _proposal(store_root, "always-uv", kind="rule")
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    assert _node(payload, "p:always-uv")["type"] == "rule"


def test_the_legend_and_aria_label_do_not_call_a_rule_a_skill(store_root: Path) -> None:
    """004 fixed the *inspector*; the legend and the canvas's accessible label are
    the user's FIRST description of what the amber diamond means, and a rule draws
    the same diamond (Codex P3-UX-API-CONTRACT-012). Asserted on rendered HTML —
    the page is the deliverable here, not an unexecutable claim about it."""
    _proposal(store_root, "always-uv", kind="rule")
    html = (
        TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
        .get("/graph?orphans=1")
        .text
    )
    assert "skill / rule" in html, "the legend must not name only one of the two kinds"
    assert "proposals (skills and rules)" in html
    assert "curated facts, and skills" not in html


def test_an_accepted_proposal_is_not_claimed_installed_without_its_artifact(
    store_root: Path, tmp_path: Path
) -> None:
    """`status: accepted` records a decision, not that the artifact is on this
    machine. The node must carry the same liveness answer `revert` and the Skills
    gallery use, so the two surfaces can never disagree."""
    _proposal(
        store_root,
        "gone-skill",
        status="accepted",
        installed_path=str(tmp_path / "nowhere" / "SKILL.md"),
    )
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    assert _node(payload, "p:gone-skill")["artifact"] == "missing"


def test_a_never_accepted_proposal_has_no_artifact_state(store_root: Path) -> None:
    _proposal(store_root, "just-proposed")
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    node = _node(payload, "p:just-proposed")
    assert node["artifact"] is None and node["status"] == "proposed"


def test_a_schema_refusal_during_liveness_is_not_softened_into_a_badge(
    store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D11 is a refusal, not a degradation. `artifact_state` **reopens the store**
    while re-deriving candidate targets, so a newer-schema `store.toml` can raise
    `UnsupportedSchemaError` from *inside* composition — after the request-boundary
    `open_store` already succeeded. The fail-soft wrapper swallowed it and the
    route's broad guard would have swallowed a re-raise too, so the front door kept
    reading and rendering after spec §10/§14 said stop (Codex
    P1-SAFETY-SECURITY-007)."""

    def refuse(*_args: object, **_kwargs: object) -> str:
        raise store.UnsupportedSchemaError("store.toml: schema 99 is newer than this binary")

    _proposal(store_root, "accepted-one", status="accepted")
    monkeypatch.setattr(graph_view.proposals, "artifact_state", refuse)
    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")

    response = client.get("/graph")
    assert response.status_code == 409, "a D11 refusal must reach the typed schema page"
    assert "accepted-one" not in response.text, "no store state may render after the refusal"
    assert "could not be read" not in response.text, "409 is not the degraded graph"


def test_an_unreadable_artifact_probe_costs_one_badge_not_the_page(
    store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the app's front door; a liveness probe that raises must degrade to
    "state unknown", not take the graph down."""

    def boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("unreadable mount")

    _proposal(store_root, "probe-explodes", status="accepted")
    monkeypatch.setattr(graph_view.proposals, "artifact_state", boom)
    handle = open_store(store_root, StoreMode.READ)
    payload = graph_view.graph_payload(handle, store_root)
    assert _node(payload, "p:probe-explodes")["artifact"] is None


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
