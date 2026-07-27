"""Tests for the Memory + Search surface (app-shell plan, Phase 2c): the
read-only browser over curated facts + synthesized nodes, and keyword search.
Covers the list, the fact detail (with provenance linked back to Sessions),
display-time redaction on both the fact page and search snippets, keyword
search, and the not-found / bad-slug guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects, store
from neurobase.webui.app import build_app

PROJECT = "myrepo"
_SECRET = "AKIAIOSFODNN7EXAMPLE"  # an AWS key shape core.redact scrubs by default
_RAW_FILE = "2026-07-01T09-00-00.000000Z_claude_sess1234.md"


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    store.upsert_curated(
        root,
        PROJECT,
        "prefer-uv",
        "Prefer uv over pip for installs — it resolves faster.",
        provenance=[f"raw/{_RAW_FILE}"],
    )
    store.upsert_curated(
        root,
        PROJECT,
        "leaky-fact",
        f"A note that inline-quotes a credential {_SECRET} in its body.",
    )
    store.write_node(root, PROJECT, "commandcenter", "## Status\n\nSynthesized memory node body.")
    return root


@pytest.fixture
def client(store_root: Path) -> TestClient:
    app: Starlette = build_app(store_root)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_memory_nav_is_live_with_count(client: TestClient) -> None:
    html = client.get("/memory").text
    assert 'href="/memory"' in html
    assert '<span class="count">2</span>' in html  # two active curated facts


def test_memory_list_shows_facts_and_the_node(client: TestClient) -> None:
    r = client.get("/memory")
    assert r.status_code == 200
    assert "prefer-uv" in r.text and "leaky-fact" in r.text
    assert "Synthesized memory node body." in r.text  # the node is surfaced


def test_memory_detail_links_provenance_back_to_sessions(client: TestClient) -> None:
    r = client.get(f"/memory/{PROJECT}/prefer-uv")
    assert r.status_code == 200
    assert "Prefer uv over pip" in r.text
    # provenance "raw/<file>" links to the source capture in Sessions
    assert f'href="/sessions/{PROJECT}/{_RAW_FILE}"' in r.text


def test_memory_detail_redacts_secrets_at_display(client: TestClient) -> None:
    r = client.get(f"/memory/{PROJECT}/leaky-fact")
    assert r.status_code == 200
    assert _SECRET not in r.text, "a secret rendered unredacted on the fact page"


# --- two-pane master/detail ------------------------------------------------


def test_memory_is_two_pane_with_node_overview(client: TestClient) -> None:
    html = client.get("/memory").text
    assert 'class="split"' in html, "Memory should render the two-pane split"
    # with nothing selected, the default right pane shows the synthesized node
    assert "Synthesized memory node body." in html


def test_clicking_a_fact_previews_it_inline(client: TestClient) -> None:
    html = client.get(f"/memory/{PROJECT}/prefer-uv").text
    assert 'class="split"' in html  # facts list stays on the left
    assert "Prefer uv over pip" in html  # fact body in the right pane
    assert f"/memory/{PROJECT}/prefer-uv?view=full" in html  # open-as-page affordance


def test_memory_fragment_is_pane_only(client: TestClient) -> None:
    html = client.get(f"/memory/{PROJECT}/prefer-uv", params={"fragment": "1"}).text
    assert "Prefer uv over pip" in html
    assert "<html" not in html.lower(), "fragment must not carry the page shell"
    assert 'class="split"' not in html, "fragment must be the pane only"


def test_memory_open_as_page_is_standalone(client: TestClient) -> None:
    html = client.get(f"/memory/{PROJECT}/prefer-uv", params={"view": "full"}).text
    assert "Prefer uv over pip" in html
    assert 'class="split"' not in html
    assert "page narrow" in html


def test_memory_selected_row_is_highlighted(client: TestClient) -> None:
    assert 'class="on"' in client.get(f"/memory/{PROJECT}/prefer-uv").text
    assert 'class="on"' not in client.get("/memory").text


def test_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/memory/no-such-project/prefer-uv").status_code == 404


def test_unknown_fact_is_404(client: TestClient) -> None:
    assert client.get(f"/memory/{PROJECT}/does-not-exist").status_code == 404


def test_non_slug_fact_name_is_404(client: TestClient) -> None:
    # a name that isn't a valid store slug (^[a-z0-9-]+$) is refused before any
    # path is built — this also forecloses traversal (no `.`/`/` in a slug).
    assert client.get(f"/memory/{PROJECT}/Not.A.Slug").status_code == 404


def test_symlinked_curated_dir_fact_is_404(tmp_path: Path, store_root: Path) -> None:
    """P2-SAFETY-SECURITY-003: a valid slug can't escape ``curated/``, but if
    ``curated/`` itself is a symlink out of the store, the resolved fact file is
    outside the validated root and must not be read/rendered."""
    external = tmp_path / "external-curated"
    external.mkdir()
    (external / "loot.md").write_text(
        f"---\nname: loot\nstatus: active\n---\n\nexternal fact {_SECRET}", encoding="utf-8"
    )
    curated = store.memory_dir(PROJECT, store_root) / "curated"
    for f in curated.iterdir():
        f.unlink()
    curated.rmdir()
    curated.symlink_to(external, target_is_directory=True)

    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    r = client.get(f"/memory/{PROJECT}/loot")
    assert r.status_code == 404
    assert "external fact" not in r.text and _SECRET not in r.text


def test_symlinked_nodes_dir_is_skipped(tmp_path: Path, store_root: Path) -> None:
    """P2-SAFETY-SECURITY-003: a symlinked ``nodes/`` directory resolves outside
    the store, so its entries are skipped rather than rendered on the overview."""
    external = tmp_path / "external-nodes"
    external.mkdir()
    (external / "loot.md").write_text(
        "---\nname: loot\n---\n\nEXTERNAL NODE BODY", encoding="utf-8"
    )
    nodes = store.memory_dir(PROJECT, store_root) / "nodes"
    for f in nodes.iterdir():
        f.unlink()
    nodes.rmdir()
    nodes.symlink_to(external, target_is_directory=True)

    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    html = client.get("/memory").text
    assert "EXTERNAL NODE BODY" not in html, "a symlinked nodes/ dir must not be rendered"


def test_search_finds_a_fact_and_links_to_it(client: TestClient) -> None:
    r = client.get("/search", params={"q": "uv"})
    assert r.status_code == 200
    assert "prefer-uv" in r.text
    assert f'href="/memory/{PROJECT}/prefer-uv"' in r.text


def test_search_redacts_secrets_in_snippets(client: TestClient) -> None:
    """A query that surfaces the leaky fact must not echo its secret in the
    ranked snippet."""
    r = client.get("/search", params={"q": "credential"})
    assert r.status_code == 200
    assert "leaky-fact" in r.text, "precondition: the query surfaces the leaky fact"
    assert _SECRET not in r.text, "a secret rendered unredacted in a search snippet"


def test_search_without_query_prompts(client: TestClient) -> None:
    r = client.get("/search")
    assert r.status_code == 200
    assert "Type a query" in r.text


def test_search_no_match_says_so(client: TestClient) -> None:
    r = client.get("/search", params={"q": "zzzznotfound"})
    assert r.status_code == 200
    assert "No matches" in r.text
