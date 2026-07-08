"""Tests for the Phase 7 MCP server (build-plan Phase 7; plan WS-C).

Covers the five baseline tools, the ``memory_remember`` write path (redaction +
user-directed provenance + no-clobber slugs), and the load-bearing invariant:
``resources/list`` always returns a valid array and never errors — Codex probes
it at startup.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from neurobase.core import projects, store
from neurobase.core.config import Config, McpConfig
from neurobase.mcp.server import build_server


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


def _register(root: Path, tmp_path: Path, slug: str) -> None:
    store.ensure_tree(slug, root)
    projects.register_project(root, tmp_path / slug, slug=slug)


def _server(root: Path, *, expose: bool = False, cwd: Path | None = None):
    return build_server(
        root=root,
        config=Config(mcp=McpConfig(expose_resources=expose)),
        cwd=cwd or root,
    )


def _call(server, tool: str, **args) -> object:
    """Return a tool's Python value regardless of FastMCP's content shape:
    list-returning tools carry structured output (a tuple); bare-dict tools
    emit one JSON content block."""
    r = anyio.run(server.call_tool, tool, args)
    if isinstance(r, tuple):
        return r[1]["result"]
    return json.loads(r[0].text)


# Both names read the same now; kept for call-site readability.
_structured = _call
_result = _call


# --- tool surface --------------------------------------------------------


def test_all_five_baseline_tools_registered(root: Path) -> None:
    tools = anyio.run(_server(root).list_tools)
    assert sorted(t.name for t in tools) == [
        "memory_list_projects",
        "memory_read_node",
        "memory_remember",
        "memory_search",
        "recommendations_list",
    ]


# --- the resources/list invariant (Codex startup probe) ------------------


def test_resources_list_empty_when_dual_exposure_off(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    store.write_node(root, "alpha", "alpha-status", "content")
    assert anyio.run(_server(root, expose=False).list_resources) == []


def test_resources_list_empty_when_on_but_no_nodes(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")  # tree exists, zero nodes
    assert anyio.run(_server(root, expose=True).list_resources) == []


def test_resources_list_populated_when_on_with_nodes(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    store.write_node(root, "alpha", "alpha-status", "content")
    resources = anyio.run(_server(root, expose=True).list_resources)
    assert [str(r.uri) for r in resources] == ["neurobase://node/alpha/alpha-status"]


def test_resources_list_valid_on_empty_store(root: Path) -> None:
    # No store at all, dual-exposure on ⇒ still a valid empty array, no error.
    assert anyio.run(_server(root, expose=True).list_resources) == []


# --- memory_search -------------------------------------------------------


def test_memory_search_returns_ranked_hits(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    store.upsert_curated(root, "alpha", "deploy-uses-uv", "Deploy via uv.", provenance=["t"])
    hits = _result(_server(root), "memory_search", query="deploy")
    assert hits[0]["name"] == "deploy-uses-uv"
    assert hits[0]["kind"] == "curated"


# --- memory_read_node ----------------------------------------------------


def test_memory_read_node_found_and_missing(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    store.write_node(root, "alpha", "alpha-status", "the body")
    srv = _server(root)
    found = _structured(srv, "memory_read_node", project="alpha", name="alpha-status")
    assert found == {"found": True, "project": "alpha", "name": "alpha-status", "body": "the body"}
    missing = _structured(srv, "memory_read_node", project="alpha", name="nope")
    assert missing == {"found": False, "project": "alpha", "name": "nope"}


def test_memory_read_node_bad_slug_is_not_an_error(root: Path) -> None:
    missing = _structured(_server(root), "memory_read_node", project="Bad Slug!", name="x")
    assert missing["found"] is False


# --- memory_list_projects ------------------------------------------------


def test_memory_list_projects_counts(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    store.upsert_curated(root, "alpha", "f1", "a", provenance=["t"])
    store.write_node(root, "alpha", "alpha-status", "n")
    out = _result(_server(root), "memory_list_projects")
    assert out == [{"project": "alpha", "curated_count": 1, "node_count": 1}]


# --- memory_remember -----------------------------------------------------


def test_memory_remember_writes_user_directed_curated_fact(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")
    res = _structured(srv, "memory_remember", fact="Prefer uv over pip for installs")
    assert res["project"] == "alpha"
    doc = store.read_doc(Path(res["path"]))
    assert doc.get("provenance") == ["user-directed"]
    assert "uv" in doc.body


def test_memory_remember_redacts_before_writing(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")
    res = _structured(srv, "memory_remember", fact="key is AKIAIOSFODNN7EXAMPLE do not leak")
    body = store.read_doc(Path(res["path"])).body
    assert "AKIAIOSFODNN7EXAMPLE" not in body


def test_memory_remember_does_not_clobber_same_first_line(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")
    a = _structured(srv, "memory_remember", fact="Deploy notes\nfirst")
    b = _structured(srv, "memory_remember", fact="Deploy notes\nsecond")
    assert a["slug"] != b["slug"]


def test_memory_remember_errors_without_resolvable_project(root: Path, tmp_path: Path) -> None:
    # cwd resolves to no registered project and no explicit project given.
    srv = _server(root, cwd=tmp_path / "unregistered")
    with pytest.raises(Exception):  # noqa: B017 - FastMCP surfaces a tool error
        anyio.run(srv.call_tool, "memory_remember", {"fact": "orphan fact"})


def test_memory_remember_explicit_project_overrides_cwd(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "beta")
    srv = _server(root, cwd=tmp_path / "unregistered")
    res = _structured(srv, "memory_remember", fact="scoped fact", project="beta")
    assert res["project"] == "beta"


# --- recommendations_list (Phase 8 seam) ---------------------------------


def test_recommendations_list_empty_without_proposals_dir(root: Path) -> None:
    assert _result(_server(root), "recommendations_list") == []


def test_recommendations_list_reads_proposals(root: Path) -> None:
    proposals = root / "proposals"
    proposals.mkdir(parents=True)
    store.write_doc(
        proposals / "use-uv.md",
        {"name": "use-uv", "status": "proposed", "type": "rule", "target": "AGENTS.md"},
        "body",
    )
    out = _result(_server(root), "recommendations_list")
    assert out == [
        {
            "slug": "use-uv",
            "status": "proposed",
            "type": "rule",
            "target": "AGENTS.md",
            "path": str(proposals / "use-uv.md"),
        }
    ]


# --- recall prompt (Claude sugar) ----------------------------------------


def test_recall_prompt_only_registered_with_dual_exposure(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    assert anyio.run(_server(root, expose=False).list_prompts) == []
    on = anyio.run(_server(root, expose=True).list_prompts)
    assert [p.name for p in on] == ["recall"]
