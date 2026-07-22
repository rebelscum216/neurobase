"""Tests for the core keyword search primitive (build-plan Phase 7, D-a/D-c).

Grep + term-frequency over curated facts + status nodes; fail-soft; explicit
project scopes, omitted project spans the registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neurobase.core import projects, search, store
from neurobase.core.store_handle import StoreMode, open_store


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


def _curated(root: Path, project: str, slug: str, body: str) -> None:
    store.ensure_tree(project, root)
    store.upsert_curated(root, project, slug, body, provenance=["test"])


def _node(root: Path, project: str, name: str, body: str) -> None:
    store.ensure_tree(project, root)
    store.write_node(root, project, name, body)


# --- hits over both kinds ------------------------------------------------


def test_finds_curated_fact_by_keyword(root: Path) -> None:
    _curated(root, "alpha", "deploy-uses-uv", "Deploy runs via uv tool install.")
    hits = search.search(open_store(root, StoreMode.READ), "deploy", project="alpha")
    assert [(h.name, h.kind) for h in hits] == [("deploy-uses-uv", "curated")]
    assert hits[0].score > 0
    assert "uv" in hits[0].snippet


def test_finds_status_node_by_keyword(root: Path) -> None:
    _node(root, "alpha", "alpha-status", "Current work: the curator refactor.")
    hits = search.search(open_store(root, StoreMode.READ), "curator", project="alpha")
    assert [(h.name, h.kind) for h in hits] == [("alpha-status", "node")]


# --- ranking -------------------------------------------------------------


def test_name_match_outranks_body_only_match(root: Path) -> None:
    _curated(root, "alpha", "redaction-policy", "Applies before any raw write.")
    _curated(root, "alpha", "hook-latency", "Redaction runs inside the budget.")
    hits = search.search(open_store(root, StoreMode.READ), "redaction", project="alpha")
    # Slug hit (weighted) must sort ahead of the body-only mention.
    assert [h.name for h in hits] == ["redaction-policy", "hook-latency"]
    assert hits[0].score > hits[1].score


def test_multi_term_query_sums_frequency(root: Path) -> None:
    _curated(root, "alpha", "a", "codex codex")
    _curated(root, "alpha", "b", "codex claude")
    hits = search.search(open_store(root, StoreMode.READ), "codex claude", project="alpha")
    assert hits[0].name == "a"  # two 'codex' tokens outweigh one+one


# --- empty / miss --------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "!!!", "---"])
def test_query_with_no_word_tokens_returns_empty(root: Path, query: str) -> None:
    _curated(root, "alpha", "fact", "anything at all")
    assert search.search(open_store(root, StoreMode.READ), query, project="alpha") == []


def test_no_match_returns_empty(root: Path) -> None:
    _curated(root, "alpha", "fact", "unrelated content")
    assert search.search(open_store(root, StoreMode.READ), "nonexistent", project="alpha") == []


# --- scoping (D-c) -------------------------------------------------------


def test_explicit_project_excludes_other_projects(root: Path) -> None:
    _curated(root, "alpha", "shared", "shared keyword here")
    _curated(root, "beta", "shared", "shared keyword here")
    hits = search.search(open_store(root, StoreMode.READ), "keyword", project="alpha")
    assert {h.project for h in hits} == {"alpha"}


def test_omitted_project_spans_registry(root: Path, tmp_path: Path) -> None:
    for slug in ("alpha", "beta"):
        _curated(root, slug, "fact", "shared keyword here")
        projects.register_project(root, tmp_path / slug, slug=slug)
    hits = search.search(open_store(root, StoreMode.READ), "keyword")  # no project → all registered
    assert {h.project for h in hits} == {"alpha", "beta"}


def test_omitted_project_ignores_unregistered_trees(root: Path) -> None:
    # A tree exists on disk but was never registered ⇒ not searched.
    _curated(root, "ghost", "fact", "shared keyword here")
    assert search.search(open_store(root, StoreMode.READ), "keyword") == []


# --- limit + fail-soft ---------------------------------------------------


def test_limit_caps_results(root: Path) -> None:
    for i in range(5):
        _curated(root, "alpha", f"fact-{i}", "shared keyword here")
    assert (
        len(search.search(open_store(root, StoreMode.READ), "keyword", project="alpha", limit=2))
        == 2
    )


def test_invalid_project_slug_is_fail_soft(root: Path) -> None:
    # An invalid slug must yield [] rather than raising InvalidSlugError.
    assert search.search(open_store(root, StoreMode.READ), "anything", project="Not A Slug!") == []


def test_missing_store_returns_empty(root: Path) -> None:
    assert search.search(open_store(root, StoreMode.READ), "anything") == []
