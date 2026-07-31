"""Tests for the Projects directory — the surface that says which folders have
Neurobase enabled, and the registry edits that change that set.

The write half is the point of these tests. Every mutation asserts on-disk state
through ``core.projects``/``StoreHandle``, never by re-reading the rendered page:
the page can be right about a registry that is wrong. The destructive delete gets
the most attention — a mismatched confirmation must leave *everything* in place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects, store
from neurobase.core.config import Config, EnableConfig
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.webui.app import build_app
from neurobase.webui.security import CSRF_FORM_FIELD

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


def _post(client: TestClient, app: Starlette, url: str, **fields: str):
    return client.post(
        url,
        data={CSRF_FORM_FIELD: app.state.csrf_token, **fields},
        headers={"origin": str(client.base_url)},
        follow_redirects=False,
    )


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


# --- add ---------------------------------------------------------------------


def test_add_registers_the_folder_and_creates_its_tree(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "newthing"
    folder.mkdir()
    response = _post(client, app, "/projects/add", path=str(folder))
    assert response.status_code == 303
    assert projects.load_registry(store_root)["newthing"] == [str(folder.resolve())]
    assert store.memory_dir("newthing", store_root).is_dir()


def test_add_accepts_an_explicit_slug(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "newthing"
    folder.mkdir()
    _post(client, app, "/projects/add", path=str(folder), slug="custom")
    assert "custom" in projects.load_registry(store_root)


def test_add_rejects_a_relative_path(client: TestClient, app: Starlette, store_root: Path) -> None:
    """A relative path would resolve against the *server's* cwd, not the user's."""
    response = _post(client, app, "/projects/add", path="some/repo")
    assert response.status_code == 400
    assert "absolute" in response.text
    assert list(projects.load_registry(store_root)) == ["proj"]


def test_add_rejects_a_path_that_does_not_exist(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    response = _post(client, app, "/projects/add", path=str(tmp_path / "nope"))
    assert response.status_code == 400
    assert "not an existing directory" in response.text
    assert list(projects.load_registry(store_root)) == ["proj"]


def test_add_rejects_a_path_inside_the_store(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    """Registering the store itself would make the curator capture its own output."""
    inside = store_root / "projects"
    response = _post(client, app, "/projects/add", path=str(inside))
    assert response.status_code == 400
    assert "inside the Neurobase store" in response.text
    assert list(projects.load_registry(store_root)) == ["proj"]


def test_add_rejects_the_store_root_itself(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/add", path=str(store_root))
    assert response.status_code == 400
    assert "inside the Neurobase store" in response.text


def test_add_reports_a_slug_collision_without_writing(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere" / "proj"
    other.mkdir(parents=True)
    response = _post(client, app, "/projects/add", path=str(other))
    assert response.status_code == 409
    assert "already registered" in response.text
    assert projects.load_registry(store_root)["proj"] == [str(tmp_path / "repo")]


# --- add-root ----------------------------------------------------------------


def test_add_root_appends_to_an_existing_project(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    second = tmp_path / "second-checkout"
    second.mkdir()
    response = _post(client, app, "/projects/proj/add-root", path=str(second))
    assert response.status_code == 303
    assert projects.load_registry(store_root)["proj"] == [
        str(tmp_path / "repo"),
        str(second.resolve()),
    ]


def test_add_root_on_an_unregistered_slug_is_404_not_a_create(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    """A stale link must not become a create."""
    folder = tmp_path / "folder"
    folder.mkdir()
    response = _post(client, app, "/projects/nosuch/add-root", path=str(folder))
    assert response.status_code == 404
    assert "nosuch" not in projects.load_registry(store_root)


# --- remove-root -------------------------------------------------------------


def test_remove_root_drops_one_and_keeps_the_others(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    second = tmp_path / "second"
    second.mkdir()
    projects.register_project(store_root, second, slug="proj")
    response = _post(client, app, "/projects/proj/remove-root", root=str(second.resolve()))
    assert response.status_code == 303
    assert projects.load_registry(store_root)["proj"] == [str(tmp_path / "repo")]


def test_removing_the_last_root_deregisters_the_project(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    """A zero-root entry could never be matched by resolve_project — it would be an
    invisible project that every sweep still walks."""
    response = _post(client, app, "/projects/proj/remove-root", root=str(tmp_path / "repo"))
    assert response.status_code == 303
    assert projects.load_registry(store_root) == {}
    assert store.memory_dir("proj", store_root).is_dir()  # memory untouched


def test_remove_root_that_is_not_registered_is_404(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/proj/remove-root", root="/not/a/root")
    assert response.status_code == 404
    assert projects.load_registry(store_root)["proj"]


def test_remove_root_works_for_a_directory_that_no_longer_exists(
    client: TestClient, app: Starlette, store_root: Path, tmp_path: Path
) -> None:
    """Matching is on the stored string, not a resolved path — otherwise the case
    removal matters most for would be the one case it could not handle."""
    gone = tmp_path / "vanished"
    gone.mkdir()
    projects.register_project(store_root, gone, slug="ghost")
    gone_str = str(gone.resolve())
    gone.rmdir()
    response = _post(client, app, "/projects/ghost/remove-root", root=gone_str)
    assert response.status_code == 303
    assert "ghost" not in projects.load_registry(store_root)


# --- deregister --------------------------------------------------------------


def test_deregister_removes_the_entry_and_keeps_every_byte(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/proj/deregister")
    assert response.status_code == 303
    assert projects.load_registry(store_root) == {}
    handle = open_store(store_root, StoreMode.READ)
    assert len(handle.list_raw("proj", unconsumed_only=False)) == 1
    assert len(handle.list_curated("proj", active_only=True)) == 1


def test_deregister_an_unregistered_project_is_404(client: TestClient, app: Starlette) -> None:
    response = _post(client, app, "/projects/nosuch/deregister")
    assert response.status_code == 404


# --- delete (destructive) ----------------------------------------------------


def test_delete_page_shows_what_will_be_destroyed(client: TestClient, store_root: Path) -> None:
    body = client.get("/projects/proj/delete").text
    assert str(store_root / "projects" / "proj") in body
    assert "cannot be undone" in body
    assert "Deregister, keep memory" in body  # the non-destructive way out


def test_delete_without_the_typed_confirmation_changes_nothing(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/proj/delete", confirm="wrong")
    assert response.status_code == 409
    assert store.memory_dir("proj", store_root).is_dir()
    assert projects.load_registry(store_root)["proj"]


def test_delete_with_an_empty_confirmation_changes_nothing(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/proj/delete", confirm="")
    assert response.status_code == 409
    assert store.memory_dir("proj", store_root).is_dir()


def test_delete_with_the_typed_slug_removes_tree_and_registry_entry(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = _post(client, app, "/projects/proj/delete", confirm="proj")
    assert response.status_code == 303
    assert not (store_root / "projects" / "proj").exists()
    assert projects.load_registry(store_root) == {}


def test_delete_works_on_an_orphaned_tree(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    """An unregistered tree is exactly what a user wants to clear out, and there is
    no registry entry to deregister first."""
    store.ensure_tree("orphan", store_root)
    response = _post(client, app, "/projects/orphan/delete", confirm="orphan")
    assert response.status_code == 303
    assert not (store_root / "projects" / "orphan").exists()


def test_delete_of_an_unknown_project_is_404(client: TestClient, app: Starlette) -> None:
    assert client.get("/projects/nosuch/delete").status_code == 404


def test_delete_rejects_a_slug_that_is_not_a_valid_slug(
    client: TestClient, store_root: Path
) -> None:
    """The slug is the only thing naming a directory to remove, so it is validated
    against ^[a-z0-9-]+$ before any path is built.

    ``Bad_Slug`` is deliberately route-matchable — a ``..``-shaped slug is refused
    by Starlette's own path handling before a handler ever runs, which proves
    nothing about *this* guard. This one reaches the handler and must still be
    refused, with the app's rendered error page rather than a traceback."""
    response = client.get("/projects/Bad_Slug/delete")
    assert response.status_code == 404
    assert "not a valid project slug" in response.text
    assert store_root.is_dir()


def test_a_traversal_shaped_slug_never_reaches_a_handler(client: TestClient) -> None:
    """Belt to the previous test's braces: the routing layer refuses these first."""
    assert client.get("/projects/..%2F..%2Fetc/delete").status_code == 404
    assert client.get("/projects/../../etc/delete").status_code == 404


# --- the §14 gate ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/projects/add",
        "/projects/proj/add-root",
        "/projects/proj/remove-root",
        "/projects/proj/deregister",
        "/projects/proj/delete",
    ],
)
def test_every_mutation_is_rejected_without_a_csrf_token(
    client: TestClient, store_root: Path, url: str
) -> None:
    response = client.post(url, data={"path": "/tmp", "confirm": "proj"})
    assert response.status_code == 403
    assert projects.load_registry(store_root)["proj"]
    assert store.memory_dir("proj", store_root).is_dir()


def test_delete_is_rejected_cross_origin(
    client: TestClient, app: Starlette, store_root: Path
) -> None:
    response = client.post(
        "/projects/proj/delete",
        data={CSRF_FORM_FIELD: app.state.csrf_token, "confirm": "proj"},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert store.memory_dir("proj", store_root).is_dir()


def test_projects_is_a_live_rail_entry(client: TestClient) -> None:
    assert 'href="/projects"' in client.get("/skills").text
