"""Tests for the Projects directory — the read-only surface that says which
folders have Neurobase enabled and what state each one is actually in.

The theme of these tests is **hostile registry content**. ``load_registry``
preserves arbitrary hand-edited TOML by contract — any table key, any string in
``roots`` — and three review rounds found the same defect class each time: a
surface trusting that output and 500-ing the whole page, hiding every healthy
project behind one bad entry. So the cases below are deliberately the nasty
instances (an invalid slug, an embedded NUL), not the polite ones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects, store
from neurobase.core.config import Config, EnableConfig
from neurobase.webui.app import build_app

NOW = datetime(2026, 7, 10, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def store_root(tmp_path: Path, repo: Path) -> Path:
    """A store with one registered project carrying a raw capture and a fact."""
    root = tmp_path / "store"
    projects.register_project(root, repo, slug="proj")
    store.ensure_tree("proj", root)
    store.write_raw(
        root,
        "proj",
        agent="claude",
        session_id="abc12345",
        cwd=str(repo),
        branch="main",
        captured_at=NOW,
        body="a captured session",
    )
    store.upsert_curated(root, "proj", "a-fact", "The fact body.")
    return root


@pytest.fixture
def app(store_root: Path) -> Starlette:
    return build_app(store_root)


@pytest.fixture
def client(app: Starlette) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8765")


# --- the directory itself ----------------------------------------------------


def test_directory_lists_the_project_its_root_and_counts(client: TestClient, repo: Path) -> None:
    body = client.get("/projects").text
    assert "proj" in body
    assert str(repo) in body
    assert "1 enabled" in body
    # One raw capture and one active fact, each rendered in its own metric block.
    assert '<span class="label">Captures</span><span class="value">1' in body
    assert '<span class="label">Facts</span><span class="value">1<' in body
    assert '<span class="label">Nodes</span><span class="value">0<' in body


def test_a_root_that_no_longer_exists_is_flagged_not_hidden(
    client: TestClient, store_root: Path, repo: Path, tmp_path: Path
) -> None:
    """The case removal exists for: the repo moved or was deleted. The row must
    still render (you cannot unregister what you cannot see) and say so."""
    gone = tmp_path / "vanished"
    projects.register_project(store_root, gone, slug="ghost")
    gone_str = str(gone.resolve())
    body = client.get("/projects").text
    assert gone_str in body
    assert "missing" in body


def test_denylisted_root_is_badged(
    client: TestClient, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denylisted project is registered but silently suppressed — the state that
    is hardest to diagnose from the outside, so the row has to say it."""
    config = Config(enable=EnableConfig(denylist=[str(repo.parent)]))
    monkeypatch.setattr("neurobase.webui.routes.load_config", lambda: config)
    body = client.get("/projects").text
    assert "denylisted" in body
    assert "suppressed" in body


def test_a_tree_with_no_registry_entry_is_surfaced(client: TestClient, store_root: Path) -> None:
    """An orphaned tree is a dead end — nothing resolves to it, so nothing can ever
    capture into it. Before this surface existed it was invisible."""
    store.ensure_tree("orphan", store_root)
    body = client.get("/projects").text
    assert "orphan" in body
    assert "unregistered" in body


# --- hostile registry content (Codex F1 round 2, F6 round 3) -----------------
#
# `load_registry` validates an entry's `roots` *shape* but deliberately never its
# table key, and permits any string inside `roots`. Both are reachable by hand
# edit, and each one used to take down the entire directory — the page that shows
# every other project.


def _append_entry(store_root: Path, key: str, root_value: str) -> None:
    registry = store_root / "registry.toml"
    registry.write_text(
        registry.read_text(encoding="utf-8") + f'\n[projects."{key}"]\nroots = ["{root_value}"]\n',
        encoding="utf-8",
    )


def test_an_invalid_slug_entry_does_not_500_the_directory(
    client: TestClient, store_root: Path, tmp_path: Path
) -> None:
    """Every store accessor — including `memory_dir`, which is not a "count" and so
    did not look like it belonged inside the counting guard — raises
    InvalidSlugError for a key like this."""
    _append_entry(store_root, "bad_slug", str(tmp_path / "somewhere"))
    assert client.get("/projects").status_code == 200


def test_an_invalid_slug_entry_does_not_hide_the_healthy_projects(
    client: TestClient, store_root: Path, tmp_path: Path
) -> None:
    """The regression that matters: one bad row degrades itself, not the page."""
    _append_entry(store_root, "bad_slug", str(tmp_path / "somewhere"))
    body = client.get("/projects").text
    assert "proj" in body
    assert '<span class="label">Facts</span><span class="value">1<' in body


def test_an_invalid_slug_row_is_rendered_and_labelled(
    client: TestClient, store_root: Path, tmp_path: Path
) -> None:
    _append_entry(store_root, "bad_slug", str(tmp_path / "somewhere"))
    body = client.get("/projects").text
    assert "bad_slug" in body
    assert "invalid slug" in body


def test_a_slash_containing_key_is_rendered_like_any_other_invalid_slug(
    client: TestClient, store_root: Path, tmp_path: Path
) -> None:
    """A TOML key may contain anything, including `/`. The round-2 fix used
    `bad_slug` — an underscore, which happens to be URL-path-safe — and so proved
    less than it looked. Nothing on this read-only page interpolates a key into a
    URL any more, but the row still has to render."""
    _append_entry(store_root, "bad/slug", str(tmp_path / "somewhere"))
    response = client.get("/projects")
    assert response.status_code == 200
    assert "bad/slug" in response.text
    assert "proj" in response.text


def test_a_root_that_is_not_a_usable_path_does_not_500_the_directory(
    client: TestClient, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An embedded NUL is the sharp case: `Path.is_dir()` swallows it, but
    `Path.resolve()` inside the denylist/auto-enable lookups raises ValueError —
    which `_config_hit` caught neither. One hand-edited root plus a non-empty
    denylist took down the whole page (Codex F6)."""
    _append_entry(store_root, "nulroot", "\\u0000")
    monkeypatch.setattr(
        "neurobase.webui.routes.load_config",
        lambda: Config(enable=EnableConfig(denylist=["/tmp"])),
    )
    response = client.get("/projects")
    assert response.status_code == 200
    assert "unusable" in response.text
    assert "proj" in response.text  # the healthy project is still listed


def test_an_unusable_root_is_still_fail_soft_with_no_folder_rules(
    client: TestClient, store_root: Path
) -> None:
    """The rule lookups short-circuit on an empty denylist, so the NUL case only
    bit when rules were configured. Pin both directions."""
    _append_entry(store_root, "nulroot", "\\u0000")
    assert client.get("/projects").status_code == 200


def test_the_directory_is_read_only(client: TestClient) -> None:
    """Registry editing is deliberately deferred (ADR-0027) — no mutating route
    exists on this surface, and the page says how to change the list instead."""
    body = client.get("/projects").text
    # The only <form> the page carries is the app shell's topbar search (a GET).
    assert body.count("<form") == 1
    assert 'action="/search"' in body
    assert "read-only" in body
    assert "neurobase enable" in body
    for url in ("/projects/add", "/projects/proj/deregister", "/projects/proj/delete"):
        assert client.post(url, data={}).status_code in (403, 404, 405)
