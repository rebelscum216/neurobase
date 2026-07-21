"""Tests for the Phase 7 MCP server (build-plan Phase 7; plan WS-C).

Covers the five baseline tools, the ``memory_remember`` write path (redaction +
user-directed provenance + no-clobber slugs), and the load-bearing invariant:
``resources/list`` always returns a valid array and never errors — Codex probes
it at startup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolResult

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


def _call(server, tool: str, **args) -> Any:
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


def _write_unsupported_schema(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "store.toml").write_text(
        f"schema = {store.STORE_SCHEMA_VERSION + 1}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "metadata",
    [
        f"schema = {store.STORE_SCHEMA_VERSION + 1}\n",
        "this is [not valid toml",
    ],
)
def test_incompatible_store_metadata_keeps_server_and_resources_list_alive(
    root: Path, metadata: str
) -> None:
    root.mkdir(parents=True)
    (root / "store.toml").write_text(metadata, encoding="utf-8")

    srv = _server(root, expose=True)

    assert anyio.run(srv.list_resources) == []
    assert sorted(tool.name for tool in anyio.run(srv.list_tools)) == [
        "memory_list_projects",
        "memory_read_node",
        "memory_remember",
        "memory_search",
        "recommendations_list",
    ]


@pytest.mark.parametrize(
    ("tool", "arguments", "list_result"),
    [
        ("memory_search", {"query": "anything"}, True),
        ("memory_read_node", {"project": "alpha", "name": "status"}, False),
        ("memory_list_projects", {}, True),
        ("memory_remember", {"fact": "remember this", "project": "alpha"}, False),
        ("recommendations_list", {}, True),
    ],
)
def test_unsupported_schema_returns_structured_error_from_every_tool(
    root: Path,
    tool: str,
    arguments: dict,
    list_result: bool,
) -> None:
    _write_unsupported_schema(root)
    srv = _server(root)

    result = anyio.run(srv.call_tool, tool, arguments)

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "unsupported_store_schema"
    assert "newer than this binary supports" in result.structuredContent["error"]["message"]
    if list_result:
        assert result.structuredContent["result"] == []


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


def test_memory_read_node_rejects_path_traversal_name(root: Path, tmp_path: Path) -> None:
    # A node name that escapes nodes/ must NOT read an arbitrary store file.
    _register(root, tmp_path, "alpha")
    store.upsert_curated(root, "alpha", "secret-fact", "TOP SECRET", provenance=["t"])
    res = _structured(
        _server(root), "memory_read_node", project="alpha", name="../curated/secret-fact"
    )
    assert res["found"] is False
    assert "body" not in res  # the curated fact's body must not leak through


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


def test_memory_remember_secret_in_first_line_does_not_leak_into_slug(
    root: Path, tmp_path: Path
) -> None:
    # A secret on the first line must not survive in the slug / path / node name
    # (the slug is derived from the redacted text).
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")
    res = _structured(srv, "memory_remember", fact="AKIAIOSFODNN7EXAMPLE do not leak")
    # Check the lowercased form — the slug is lowercased, so a case-sensitive
    # check would pass even if the secret leaked.
    secret = "akiaiosfodnn7example"
    assert secret not in res["slug"].lower()
    assert secret not in res["path"].lower()


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


def test_memory_remember_invalid_explicit_project_is_documented_error(
    root: Path, tmp_path: Path
) -> None:
    # A bad explicit project slug must surface the documented no-project error
    # (§13 fail-soft), not a raw InvalidSlugError from ensure_tree.
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")
    with pytest.raises(Exception) as exc:  # noqa: B017
        anyio.run(srv.call_tool, "memory_remember", {"fact": "hi", "project": "Bad Slug!"})
    msg = str(exc.value).lower()
    assert "available" in msg and "invalid project slug" not in msg


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


# --- fail-soft on a corrupt registry (invariant) -------------------------


def _corrupt_registry(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "registry.toml").write_text("this is [not valid toml", encoding="utf-8")


def test_build_server_survives_corrupt_registry(root: Path) -> None:
    _corrupt_registry(root)
    # Must construct and answer resources/list validly, not raise on startup.
    srv = _server(root, expose=True, cwd=root)
    assert anyio.run(srv.list_resources) == []


def test_search_and_list_projects_fail_soft_on_corrupt_registry(root: Path) -> None:
    _corrupt_registry(root)
    srv = _server(root)
    assert _result(srv, "memory_search", query="anything") == []
    assert _result(srv, "memory_list_projects") == []


# --- a bad slug in the registry is skipped, not fatal --------------------
#
# `projects.load_registry` deliberately does not validate slugs — it returns
# whatever keys the TOML holds. So a registry hand-edited (or written by some
# other tool) with an invalid project key reaches `store.memory_dir`, which
# raises InvalidSlugError. Both scans below must skip that one entry and still
# serve the valid projects, rather than sinking the whole pass.


def _registry_with_bad_slug(root: Path, tmp_path: Path) -> None:
    """A registry holding one valid project and one invalid-slug project,
    written directly because `register_project` would reject the bad one."""
    _register(root, tmp_path, "alpha")
    (root / "registry.toml").write_text(
        '[projects.alpha]\nroots = ["/repos/alpha"]\n'
        '[projects."Bad Slug!"]\nroots = ["/repos/bad"]\n',
        encoding="utf-8",
    )


def test_bad_slug_in_registry_does_not_sink_the_node_scan(root: Path, tmp_path: Path) -> None:
    """The invalid-slug entry is skipped and the valid project's node is still
    exposed — the resources/list invariant holds against a registry that
    `load_registry` happily returns but `memory_dir` refuses."""
    _registry_with_bad_slug(root, tmp_path)
    store.write_node(root, "alpha", "alpha-status", "content")

    resources = anyio.run(_server(root, expose=True).list_resources)

    assert [str(r.uri) for r in resources] == ["neurobase://node/alpha/alpha-status"]


def test_bad_slug_in_registry_is_skipped_by_list_projects(root: Path, tmp_path: Path) -> None:
    """`memory_list_projects` skips the invalid-slug entry the same way, and
    still reports the valid project — not an empty list, not an error."""
    _registry_with_bad_slug(root, tmp_path)

    listed = _result(_server(root), "memory_list_projects")

    assert [entry["project"] for entry in listed] == ["alpha"]


# --- memory_remember: the empty-fact guard -------------------------------


@pytest.mark.parametrize("fact", ["", "   ", "\n\t  \n"])
def test_memory_remember_rejects_empty_fact(root: Path, tmp_path: Path, fact: str) -> None:
    """An empty or whitespace-only fact is refused rather than saved as a
    blank curated file. Asserted with a resolvable project registered, so the
    failure is provably the empty-fact guard and not the no-project error."""
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")

    with pytest.raises(Exception) as exc:  # noqa: B017 - FastMCP surfaces a tool error
        anyio.run(srv.call_tool, "memory_remember", {"fact": fact})

    assert "must not be empty" in str(exc.value)


def test_memory_remember_empty_fact_guard_precedes_project_resolution(root: Path) -> None:
    """The empty-fact check fires before the target project is resolved: an
    empty fact with no resolvable project reports emptiness, not the
    "available projects" no-project error."""
    srv = _server(root, cwd=root / "unregistered")

    with pytest.raises(Exception) as exc:  # noqa: B017
        anyio.run(srv.call_tool, "memory_remember", {"fact": "  "})

    assert "must not be empty" in str(exc.value)


def test_memory_remember_writes_nothing_when_the_fact_is_empty(root: Path, tmp_path: Path) -> None:
    """The rejection leaves no trace — no curated file is created."""
    _register(root, tmp_path, "alpha")
    srv = _server(root, cwd=tmp_path / "alpha")

    with pytest.raises(Exception):  # noqa: B017
        anyio.run(srv.call_tool, "memory_remember", {"fact": ""})

    assert store.list_curated(root, "alpha") == []


# --- recall prompt (Claude sugar) ----------------------------------------


def test_recall_prompt_only_registered_with_dual_exposure(root: Path, tmp_path: Path) -> None:
    _register(root, tmp_path, "alpha")
    assert anyio.run(_server(root, expose=False).list_prompts) == []
    on = anyio.run(_server(root, expose=True).list_prompts)
    assert [p.name for p in on] == ["recall"]
